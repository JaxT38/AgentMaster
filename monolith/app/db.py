"""
monolith/app/db.py

SQLite-backed run history. Deliberately simple for v1 -- no ORM,
no migrations framework, just a single table and raw SQL. Revisit
if/when Postgres is introduced (out of scope for v1 per the build plan).
"""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "runs.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    target_entry_url TEXT NOT NULL,
    status TEXT NOT NULL,              -- queued | running | completed | failed
    schema_valid INTEGER,              -- NULL until evaluated; 0/1 after (Phase 7)
    schema_errors TEXT,                -- JSON-encoded list, if invalid
    created_at TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    peak_cpu_percent REAL,
    peak_memory_mb REAL,
    report_path TEXT,                  -- relative path under reports/<run_id>/ once collected
    error_message TEXT,
    approval_status TEXT               -- NULL | approved | rejected (Phase 8)
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.execute(SCHEMA)


def create_run(run_id: str, agent_id: str, target_entry_url: str, created_at: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO runs (run_id, agent_id, target_entry_url, status, created_at)
               VALUES (?, ?, ?, 'queued', ?)""",
            (run_id, agent_id, target_entry_url, created_at),
        )


def mark_started(run_id: str, started_at: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE runs SET status = 'running', started_at = ? WHERE run_id = ?",
            (started_at, run_id),
        )


def mark_finished(run_id: str, status: str, ended_at: str, error_message: str | None = None) -> None:
    """status should be 'completed' or 'failed'."""
    with get_conn() as conn:
        conn.execute(
            """UPDATE runs SET status = ?, ended_at = ?, error_message = ?
               WHERE run_id = ?""",
            (status, ended_at, error_message, run_id),
        )


def set_resource_usage(run_id: str, peak_cpu_percent: float | None, peak_memory_mb: float | None) -> None:
    """
    None means no docker stats sample was ever successfully captured
    (e.g. the container exited too quickly to measure) -- this is
    distinct from and must not be conflated with 0.0, which would
    incorrectly claim "measured and confirmed zero usage."
    See docs/issues-log.md Issue 22.
    """
    with get_conn() as conn:
        conn.execute(
            "UPDATE runs SET peak_cpu_percent = ?, peak_memory_mb = ? WHERE run_id = ?",
            (peak_cpu_percent, peak_memory_mb, run_id),
        )


def set_schema_validation(run_id: str, valid: bool, errors: list[str] | None = None) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE runs SET schema_valid = ?, schema_errors = ? WHERE run_id = ?",
            (1 if valid else 0, json.dumps(errors) if errors else None, run_id),
        )


def set_report_path(run_id: str, report_path: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE runs SET report_path = ? WHERE run_id = ?", (report_path, run_id))


def set_approval(run_id: str, approval_status: str) -> None:
    """approval_status should be 'approved' or 'rejected'."""
    with get_conn() as conn:
        conn.execute("UPDATE runs SET approval_status = ? WHERE run_id = ?", (approval_status, run_id))


def get_run(run_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None


def list_runs(limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]