"""
agent_runtime/normalize.py

Converts axe-core's raw violation results into the normalized
`findings` shape required by schemas/wcag-output.schema.json.

Key translation: axe-core tags success criteria as concatenated-digit
strings like "wcag143" (no separators), which this module maps back to
dotted form ("1.4.3") via a static lookup table built from the published
WCAG 2.2 success criteria list. This table is NOT derived programmatically
from axe -- it's hand-built and should be reviewed/extended if axe-core
ever adds tags for criteria not listed here (e.g. a future WCAG version).
"""

# axe wcag-tag (digits only, no dots) -> dotted SC number.
# Source: WCAG 2.2 success criteria, https://www.w3.org/TR/WCAG22/
WCAG_TAG_TO_CRITERION = {
    "wcag111": "1.1.1",
    "wcag121": "1.2.1",
    "wcag122": "1.2.2",
    "wcag123": "1.2.3",
    "wcag124": "1.2.4",
    "wcag125": "1.2.5",
    "wcag131": "1.3.1",
    "wcag132": "1.3.2",
    "wcag133": "1.3.3",
    "wcag134": "1.3.4",
    "wcag135": "1.3.5",
    "wcag141": "1.4.1",
    "wcag142": "1.4.2",
    "wcag143": "1.4.3",
    "wcag144": "1.4.4",
    "wcag145": "1.4.5",
    "wcag1410": "1.4.10",
    "wcag1411": "1.4.11",
    "wcag1412": "1.4.12",
    "wcag1413": "1.4.13",
    "wcag211": "2.1.1",
    "wcag212": "2.1.2",
    "wcag214": "2.1.4",
    "wcag221": "2.2.1",
    "wcag222": "2.2.2",
    "wcag224": "2.2.4",
    "wcag226": "2.2.6",
    "wcag231": "2.3.1",
    "wcag233": "2.3.3",
    "wcag241": "2.4.1",
    "wcag242": "2.4.2",
    "wcag243": "2.4.3",
    "wcag244": "2.4.4",
    "wcag245": "2.4.5",
    "wcag246": "2.4.6",
    "wcag247": "2.4.7",
    "wcag2411": "2.4.11",
    "wcag251": "2.5.1",
    "wcag252": "2.5.2",
    "wcag253": "2.5.3",
    "wcag254": "2.5.4",
    "wcag257": "2.5.7",
    "wcag258": "2.5.8",
    "wcag311": "3.1.1",
    "wcag312": "3.1.2",
    "wcag321": "3.2.1",
    "wcag322": "3.2.2",
    "wcag323": "3.2.3",
    "wcag324": "3.2.4",
    "wcag326": "3.2.6",
    "wcag331": "3.3.1",
    "wcag332": "3.3.2",
    "wcag333": "3.3.3",
    "wcag334": "3.3.4",
    "wcag337": "3.3.7",
    "wcag338": "3.3.8",
    "wcag411": "4.1.1",
    "wcag412": "4.1.2",
    "wcag413": "4.1.3",
}

# Cap on how many affected DOM nodes are included per finding, even if
# node_count is higher. Keeps report.json a reasonable size for display.
MAX_NODES_PER_FINDING = 5

# Truncate html_snippet to this many characters.
HTML_SNIPPET_MAX_LEN = 200


def extract_wcag_criteria(tags: list[str]) -> list[str]:
    """
    Pull dotted WCAG criteria numbers out of an axe-core tags list.
    Tags not found in the lookup table (e.g. "best-practice", "cat.*",
    "wcag2a") are silently skipped -- only criteria we have an exact
    dotted-number mapping for are included.
    """
    criteria = []
    for tag in tags:
        mapped = WCAG_TAG_TO_CRITERION.get(tag)
        if mapped and mapped not in criteria:
            criteria.append(mapped)
    return criteria


def normalize_violation(violation: dict) -> dict | None:
    """
    Convert one axe-core violation object into one schema-valid finding.
    Returns None if the violation has no mappable WCAG criteria --
    schemas/wcag-output.schema.json requires wcag_criteria to be
    non-empty, so a violation we can't map to a real SC number is
    dropped rather than emitted as invalid output. These dropped
    violations are NOT silently lost -- the caller is responsible for
    logging how many were dropped, since a high drop rate may mean the
    lookup table above needs extending.
    """
    wcag_criteria = extract_wcag_criteria(violation.get("tags", []))
    if not wcag_criteria:
        return None

    raw_nodes = violation.get("nodes", [])
    node_count = len(raw_nodes)

    nodes = []
    for node in raw_nodes[:MAX_NODES_PER_FINDING]:
        target = node.get("target", [])
        selector = target[0] if target else "unknown"
        html = node.get("html", "")
        if len(html) > HTML_SNIPPET_MAX_LEN:
            html = html[:HTML_SNIPPET_MAX_LEN] + "..."
        nodes.append({"selector": selector, "html_snippet": html})

    return {
        "rule_id": violation.get("id", "unknown-rule"),
        "impact": violation.get("impact") or "minor",
        "wcag_criteria": wcag_criteria,
        "description": violation.get("description", ""),
        "help_url": violation.get("helpUrl", ""),
        "node_count": node_count,
        "nodes": nodes,
    }


def normalize_axe_results(raw_axe_result: dict) -> tuple[list[dict], int]:
    """
    Returns (findings, dropped_count) for one page's raw axe-core result.
    dropped_count is the number of violations that had no mappable WCAG
    criteria and were excluded -- the caller should log this, and a
    persistently nonzero count across runs is a signal to extend
    WCAG_TAG_TO_CRITERION above.
    """
    findings = []
    dropped = 0
    for violation in raw_axe_result.get("violations", []):
        finding = normalize_violation(violation)
        if finding is None:
            dropped += 1
        else:
            findings.append(finding)
    return findings, dropped


def summarize(all_page_findings: list[list[dict]]) -> dict:
    """
    Builds the summary block (schemas/wcag-output.schema.json) from
    findings across all pages.
    """
    by_impact = {"critical": 0, "serious": 0, "moderate": 0, "minor": 0}
    total = 0
    for findings in all_page_findings:
        for f in findings:
            impact = f["impact"]
            by_impact[impact] = by_impact.get(impact, 0) + 1
            total += 1
    return {
        "pages_scanned": len(all_page_findings),
        "total_findings": total,
        "by_impact": by_impact,
    }