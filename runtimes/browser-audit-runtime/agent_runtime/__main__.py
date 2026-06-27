"""
agent_runtime/__main__.py

Real Phase 4 implementation of the wcag22-auditor agent. Replaces the
Phase 3 stub. Reads the task envelope from /workspace/input, runs an
axe-core scan (and, for scope=="site", a bounded crawl of same-domain
links) via Playwright, captures screenshots, normalizes findings per
schemas/wcag-output.schema.json, and writes report.json plus
events.jsonl to /workspace/output.

Exit code is 0 on a successful (schema-valid) run, 1 on any failure --
the launcher (Phase 5) checks this to detect failed runs.
"""

import json
import os
import re
import sys
import traceback
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright
from axe_playwright_python.sync_playwright import Axe

from agent_runtime.events import EventWriter
from agent_runtime.normalize import normalize_axe_results, summarize

INPUT_DIR = "/workspace/input"
OUTPUT_DIR = "/workspace/output"
TASK_ENVELOPE_PATH = os.path.join(INPUT_DIR, "run-request.json")

NAV_TIMEOUT_MS = 20000

# Matches Chromium/Playwright network error codes like
# "net::ERR_TUNNEL_CONNECTION_FAILED" or "net::ERR_NAME_NOT_RESOLVED"
# embedded in an exception message.
NET_ERROR_PATTERN = re.compile(r"net::(ERR_[A-Z_]+)")


def extract_error_code(exc: Exception) -> str:
    """
    Prefer a specific net::ERR_* code if present in the exception
    message (e.g. ERR_TUNNEL_CONNECTION_FAILED from a proxy denial,
    ERR_NAME_NOT_RESOLVED from a bad/unreachable hostname,
    ERR_CONNECTION_TIMED_OUT from a real timeout). These are far more
    useful for dashboard display and triage than the generic Python
    exception class name, which for most Playwright failures is just
    "Error" regardless of the actual underlying cause.
    Falls back to the exception's class name if no net::ERR_* code is
    found in the message (e.g. a non-network failure like a schema
    validation error or a file I/O problem).
    """
    match = NET_ERROR_PATTERN.search(str(exc))
    if match:
        return match.group(1)
    return type(exc).__name__


def load_task_envelope() -> dict:
    with open(TASK_ENVELOPE_PATH) as f:
        return json.load(f)


def same_registrable_domain(url: str, base_host: str) -> bool:
    """
    Minimal same-site check for crawl purposes -- mirrors the
    last-two-labels heuristic in monolith/app/acl_generator.py, with
    the same documented multi-part-TLD limitation. Kept independent
    rather than importing the launcher module, since this runs inside
    the agent container, not the monolith.
    """
    try:
        host = urlparse(url).hostname
    except Exception:
        return False
    if not host:
        return False

    def registrable(h: str) -> str:
        labels = h.lower().split(".")
        return ".".join(labels[-2:]) if len(labels) >= 2 else h

    return registrable(host) == registrable(base_host)


def discover_links(page, base_url: str, base_host: str) -> list[str]:
    """Pull same-domain <a href> links off the current page, deduped, absolute."""
    hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")
    links = []
    seen = set()
    for href in hrefs:
        if not href or href.startswith("#") or href.startswith("mailto:") or href.startswith("javascript:"):
            continue
        absolute = urljoin(base_url, href)
        # strip fragment so #section links don't count as separate pages
        absolute = absolute.split("#")[0]
        if absolute in seen:
            continue
        if same_registrable_domain(absolute, base_host):
            seen.add(absolute)
            links.append(absolute)
    return links


def scan_page(page, axe, url: str, page_index: int, events: EventWriter) -> dict:
    """Navigate to url, run axe, screenshot, return the page result dict."""
    events.step(f"Navigating to page {page_index + 1}", "navigation", 0.2 + page_index * 0.1)
    page.goto(url, timeout=NAV_TIMEOUT_MS, wait_until="load")

    events.step(f"Running axe-core scan on page {page_index + 1}", "axe-scan", 0.4 + page_index * 0.1)
    raw_result = axe.run(page)
    # axe-playwright-python returns an object with .response holding the
    # raw axe JSON dict -- adapt here if the library's return shape differs
    raw_axe_dict = raw_result.response if hasattr(raw_result, "response") else raw_result

    screenshot_rel_path = f"screenshots/page-{page_index + 1:03d}.png"
    screenshot_abs_path = os.path.join(OUTPUT_DIR, screenshot_rel_path)
    os.makedirs(os.path.dirname(screenshot_abs_path), exist_ok=True)
    page.screenshot(path=screenshot_abs_path, full_page=True)
    events.artifact_written(screenshot_rel_path, "screenshot")

    raw_rel_path = f"raw/page-{page_index + 1:03d}-axe.json"
    raw_abs_path = os.path.join(OUTPUT_DIR, raw_rel_path)
    os.makedirs(os.path.dirname(raw_abs_path), exist_ok=True)
    with open(raw_abs_path, "w") as f:
        json.dump(raw_axe_dict, f)
    events.artifact_written(raw_rel_path, "log")

    findings, dropped = normalize_axe_results(raw_axe_dict)
    if dropped:
        events.step(
            f"Note: {dropped} violation(s) on page {page_index + 1} had no mappable WCAG criteria and were excluded",
            "normalize-warning",
            0.5 + page_index * 0.1,
        )

    return {
        "url": url,
        "screenshot_path": screenshot_rel_path,
        "raw_axe_result_path": raw_rel_path,
        "findings": findings,
    }


def run(envelope: dict, events: EventWriter) -> dict:
    target = envelope["target"]
    entry_url = target["entry_url"]
    scope = target["scope"]
    max_pages = target.get("max_pages", 1) if scope == "site" else 1
    wcag_level = envelope.get("parameters", {}).get("wcag_level", "AA")
    base_host = urlparse(entry_url).hostname

    pages_result = []
    to_visit = [entry_url]
    visited = set()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        axe = Axe()

        page_index = 0
        while to_visit and page_index < max_pages:
            url = to_visit.pop(0)
            if url in visited:
                continue
            visited.add(url)

            page_result = scan_page(page, axe, url, page_index, events)
            pages_result.append(page_result)

            if scope == "site" and page_index + 1 < max_pages:
                discovered = discover_links(page, url, base_host)
                for link in discovered:
                    if link not in visited and link not in to_visit:
                        to_visit.append(link)

            page_index += 1

        browser.close()

    findings_by_page = [p["findings"] for p in pages_result]
    summary = summarize(findings_by_page)

    report = {
        "run_id": envelope["run_id"],
        "agent_id": "wcag22-auditor",
        "wcag_level": wcag_level,
        "scanned_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "target": {"entry_url": entry_url, "scope": scope},
        "pages": pages_result,
        "summary": summary,
    }
    return report


def main() -> int:
    # run_id isn't known until the envelope is loaded, so the very first
    # EventWriter call has to happen after that -- if envelope loading
    # itself fails, there's no run_id to attach a 'failed' event to, so
    # that specific failure mode is just printed to stderr and exits 1.
    try:
        envelope = load_task_envelope()
    except Exception as e:
        print(f"FATAL: could not load task envelope: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1

    events = EventWriter(envelope["run_id"], OUTPUT_DIR)
    events.started()

    try:
        report = run(envelope, events)
    except Exception as e:
        events.failed(f"Run failed: {e}", error_code=extract_error_code(e), fatal=True)
        traceback.print_exc()
        events.close()
        return 1

    report_path = os.path.join(OUTPUT_DIR, "report.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    events.artifact_written("report.json", "report")
    events.completed("report.json")
    events.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())