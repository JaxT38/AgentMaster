#!/usr/bin/env python3
"""
acl_generator.py

Builds the per-run allowed-domains.acl file for the egress proxy, from:
  - the task envelope's target.entry_url / target.scope
  - the agent.yaml's permissions.network.allowed_targets (static extras)
  - the shared common-web-assets.txt curated list

This is launcher logic (Phase 5), pulled out here as a standalone,
testable module so the domain-derivation logic (especially the
registrable-domain wildcarding for scope == "site") can be unit-tested
without needing Docker or Squid running.

NOTE: registrable-domain extraction here is a minimal heuristic, not a
full public-suffix-list implementation. It does NOT correctly handle
multi-part TLDs like "example.co.uk" (it would treat "co.uk" as the
registrable domain, which is wrong). This is flagged in
docs/deferred-tasks.md as an item to replace with a proper
public-suffix-list library (e.g. Python's `tldextract`) before this
ever runs against a real multi-part-TLD site. Do not rely on this
heuristic for anything beyond a first working pipeline against
simple .com/.org/.net-style domains.
"""

from urllib.parse import urlparse


def extract_host(entry_url: str) -> str:
    """Pull the hostname out of a full URL, lowercased, no port."""
    parsed = urlparse(entry_url)
    if not parsed.hostname:
        raise ValueError(f"Could not parse a hostname from entry_url: {entry_url!r}")
    return parsed.hostname.lower()


def derive_registrable_domain(host: str) -> str:
    """
    Minimal heuristic: take the last two labels of the hostname.
    KNOWN LIMITATION: incorrect for multi-part TLDs (e.g. "co.uk", "com.au").
    See module docstring. Replace with tldextract or equivalent before
    relying on this for real multi-part-TLD targets.
    """
    labels = host.split(".")
    if len(labels) < 2:
        return host
    return ".".join(labels[-2:])


def build_acl_lines(entry_url: str, scope: str, static_extras: list[str], common_list: list[str]) -> list[str]:
    """
    Returns the list of dstdomain ACL lines (Squid format) for one run.

    - scope == "single-page": exact host only (e.g. "www.example.com")
    - scope == "site": wildcarded registrable domain (e.g. ".example.com"),
      so subdomains are covered for a multi-page crawl
    """
    host = extract_host(entry_url)

    if scope == "single-page":
        target_lines = [host]
    elif scope == "site":
        registrable = derive_registrable_domain(host)
        target_lines = [f".{registrable}"]
    else:
        raise ValueError(f"Unknown scope: {scope!r}")

    # de-dupe while preserving order: target first, then static extras,
    # then the shared common list
    seen = set()
    ordered = []
    for line in target_lines + list(static_extras) + list(common_list):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line not in seen:
            seen.add(line)
            ordered.append(line)
    return ordered


def write_acl_file(path: str, lines: list[str]) -> None:
    with open(path, "w") as f:
        f.write("# Auto-generated per-run ACL. Do not edit by hand.\n")
        for line in lines:
            f.write(line + "\n")


if __name__ == "__main__":
    # Manual smoke-test entrypoint; see test_acl_generator.py for the
    # actual unit tests run during Phase 2 validation.
    import sys
    import json

    if len(sys.argv) != 2:
        print("Usage: acl_generator.py <task-envelope.json>")
        sys.exit(1)

    with open(sys.argv[1]) as f:
        envelope = json.load(f)

    with open("common-web-assets.txt") as f:
        common = [l.strip() for l in f if l.strip() and not l.strip().startswith("#")]

    lines = build_acl_lines(
        entry_url=envelope["target"]["entry_url"],
        scope=envelope["target"]["scope"],
        static_extras=[],
        common_list=common,
    )
    write_acl_file("allowed-domains.acl", lines)
    print(f"Wrote {len(lines)} ACL lines to allowed-domains.acl")