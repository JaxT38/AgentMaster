"""
monolith/app/artifacts.py

Copies a completed run's output workspace into reports/<run_id>/,
which is the durable, user-facing location per the README's repo
layout (workspaces/ is working storage; reports/ is the kept output).
"""

import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REPORTS_DIR = REPO_ROOT / "reports"


def collect(run_id: str, output_dir: Path) -> Path:
    """
    Copies everything from the run's output workspace into
    reports/<run_id>/. Returns the destination path.
    Uses copy rather than move so workspaces/ remains available for
    debugging a specific run without needing to dig through reports/.
    """
    dest = REPORTS_DIR / run_id
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(output_dir, dest)
    return dest


def report_relative_path(run_id: str) -> str:
    """Path relative to REPORTS_DIR, suitable for storing in run history."""
    return f"{run_id}/report.json"