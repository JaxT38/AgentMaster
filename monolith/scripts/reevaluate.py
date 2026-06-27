#!/usr/bin/env python3
"""
monolith/scripts/reevaluate.py

Standalone tool to re-run the Phase 7 schema evaluation against any
run's report.json, without needing to trigger a real agent run.

Two uses:
  1. Verification: prove the evaluator correctly rejects a deliberately
     malformed report (this is how Phase 7's negative-case "done when"
     criterion gets tested against the real running system, not just
     jsonschema in isolation).
  2. Practical re-check: if schemas/wcag-output.schema.json is ever
     revised, this lets you re-validate every existing run in reports/
     against the new schema without re-running the agents.

Usage:
  python3 reevaluate.py <run_id>                 # re-evaluate one run, update DB
  python3 reevaluate.py <run_id> --dry-run        # evaluate and print, don't touch DB
  python3 reevaluate.py --all                     # re-evaluate every run in reports/
  python3 reevaluate.py --inject-malformed <run_id>
      # copies the real report.json aside, writes a deliberately broken
      # version in its place, evaluates it, then restores the original.
      # This is the actual negative-case proof for Phase 7.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

import jsonschema

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "wcag-output.schema.json"
REPORTS_DIR = REPO_ROOT / "reports"

sys.path.insert(0, str(REPO_ROOT / "monolith"))
from app import db  # noqa: E402


def evaluate_report(report_path: Path) -> tuple[bool, list[str]]:
    with open(SCHEMA_PATH) as f:
        schema = json.load(f)
    with open(report_path) as f:
        report = json.load(f)

    validator = jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(report), key=lambda e: list(e.path))
    if not errors:
        return True, []
    messages = [f"{'.'.join(str(p) for p in e.path) or '(root)'}: {e.message}" for e in errors]
    return False, messages


def reevaluate_run(run_id: str, dry_run: bool = False) -> None:
    report_path = REPORTS_DIR / run_id / "report.json"
    if not report_path.exists():
        print(f"[{run_id}] no report.json found at {report_path}", file=sys.stderr)
        return

    valid, errors = evaluate_report(report_path)
    if valid:
        print(f"[{run_id}] VALID")
    else:
        print(f"[{run_id}] INVALID ({len(errors)} error(s)):")
        for e in errors:
            print(f"  - {e}")

    if not dry_run:
        db.set_schema_validation(run_id, valid, errors if not valid else None)
        print(f"[{run_id}] run history updated")


def reevaluate_all(dry_run: bool = False) -> None:
    if not REPORTS_DIR.exists():
        print("no reports/ directory found", file=sys.stderr)
        return
    for run_dir in sorted(REPORTS_DIR.iterdir()):
        if (run_dir / "report.json").exists():
            reevaluate_run(run_dir.name, dry_run=dry_run)


def inject_malformed_and_test(run_id: str) -> None:
    """
    The actual negative-case proof: temporarily corrupt a real run's
    report.json, confirm the evaluator catches it, then restore the
    original so the run history isn't left in a broken state.
    """
    report_path = REPORTS_DIR / run_id / "report.json"
    if not report_path.exists():
        print(f"[{run_id}] no report.json found at {report_path}", file=sys.stderr)
        return

    backup_path = report_path.with_suffix(".json.bak")
    shutil.copy(report_path, backup_path)

    try:
        with open(report_path) as f:
            report = json.load(f)

        # Two deliberate breaks, mirroring the manual test already
        # proven in isolation: remove a required field, and set an
        # enum field to an invalid value.
        report.pop("summary", None)
        report["wcag_level"] = "INVALID_LEVEL"

        with open(report_path, "w") as f:
            json.dump(report, f, indent=2)

        print(f"[{run_id}] injected malformed report.json, evaluating...")
        reevaluate_run(run_id, dry_run=False)

        # Re-fetch from DB to prove the stored state actually reflects
        # the injected failure, not just what evaluate_report printed.
        record = db.get_run(run_id)
        print(f"[{run_id}] DB state after injection: schema_valid={record['schema_valid']!r}")
        print(f"[{run_id}] DB schema_errors: {record['schema_errors']}")

    finally:
        shutil.copy(backup_path, report_path)
        backup_path.unlink()
        print(f"[{run_id}] restored original report.json")
        reevaluate_run(run_id, dry_run=False)
        record = db.get_run(run_id)
        print(f"[{run_id}] DB state after restore: schema_valid={record['schema_valid']!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id", nargs="?", help="Run ID to re-evaluate")
    parser.add_argument("--all", action="store_true", help="Re-evaluate every run in reports/")
    parser.add_argument("--dry-run", action="store_true", help="Evaluate and print only, don't update the DB")
    parser.add_argument(
        "--inject-malformed",
        metavar="RUN_ID",
        help="Temporarily corrupt this run's report.json to prove the evaluator catches it, then restore it",
    )
    args = parser.parse_args()

    db.init_db()

    if args.inject_malformed:
        inject_malformed_and_test(args.inject_malformed)
    elif args.all:
        reevaluate_all(dry_run=args.dry_run)
    elif args.run_id:
        reevaluate_run(args.run_id, dry_run=args.dry_run)
    else:
        parser.print_help()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())