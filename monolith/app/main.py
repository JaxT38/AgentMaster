"""
monolith/app/main.py

The v1 monolith. Single FastAPI app: agent registry, launcher,
artifact collection, run history, and a deterministic schema
evaluator (Phase 7) -- all in one process, per the build plan's
"monolith-first" decision for v1.

Endpoints:
  POST /runs           start a run
  GET  /runs           list recent runs
  GET  /runs/{run_id}  get one run's status/result
  POST /runs/{run_id}/approve
  POST /runs/{run_id}/reject
"""

import json
import string
import random
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, launcher, artifacts
from .registry import AgentRegistry

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WCAG_OUTPUT_SCHEMA_PATH = REPO_ROOT / "schemas" / "wcag-output.schema.json"
REPORTS_DIR = REPO_ROOT / "reports"

app = FastAPI(title="Local Agent Platform (v1 monolith)")
registry = AgentRegistry()

# Dashboard (Phase 8) is a static HTML file opened directly in a browser,
# which makes it a different origin than this API -- CORS must be enabled
# for its fetch() calls to succeed. Wide open ("*") is acceptable for v1
# since this only ever binds to localhost and is never exposed externally;
# revisit if that assumption changes.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serves reports/<run_id>/report.json and reports/<run_id>/screenshots/*.png
# directly so the dashboard can fetch them without a dedicated endpoint.
REPORTS_DIR.mkdir(exist_ok=True)
app.mount("/reports", StaticFiles(directory=REPORTS_DIR), name="reports")


@app.on_event("startup")
def startup() -> None:
    db.init_db()
    registry.load_all()


class StartRunRequest(BaseModel):
    agent_id: str
    entry_url: str
    scope: str = "single-page"
    max_pages: int | None = None
    wcag_level: str = "AA"


def _new_run_id() -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
    return f"run{suffix}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@app.post("/runs")
def start_run(req: StartRunRequest):
    agent_manifest = registry.get(req.agent_id)
    if agent_manifest is None:
        raise HTTPException(404, f"unknown agent_id: {req.agent_id!r}")

    run_id = _new_run_id()
    created_at = _now_iso()

    target = {"entry_url": req.entry_url, "scope": req.scope}
    if req.scope == "site":
        target["max_pages"] = req.max_pages or 1

    envelope = {
        "run_id": run_id,
        "agent_id": req.agent_id,
        "target": target,
        "created_at": created_at,
        "parameters": {"wcag_level": req.wcag_level},
    }

    db.create_run(run_id, req.agent_id, req.entry_url, created_at)

    try:
        _execute_run(run_id, envelope, agent_manifest)
    except Exception as e:
        db.mark_finished(run_id, "failed", _now_iso(), error_message=str(e))
        raise HTTPException(500, f"run failed: {e}")

    return db.get_run(run_id)


def _execute_run(run_id: str, envelope: dict, agent_manifest: dict) -> None:
    """
    The actual orchestration sequence -- this is the automated
    replacement for every manual step from Phases 2-4:
      1. generate the per-run proxy ACL and restart the proxy
      2. prepare the workspace (input envelope + writable output dir)
      3. launch the agent container, polling resource usage
      4. record exit status + resource usage
      5. collect artifacts into reports/
      6. evaluate report.json against the WCAG output schema (Phase 7)
    """
    input_dir, output_dir = launcher.prepare_workspace(run_id, envelope)

    acl_lines = launcher.build_acl_lines(envelope, agent_manifest)
    acl_path = launcher.write_acl_file(acl_lines, input_dir.parent)
    launcher.restart_proxy(acl_path)

    db.mark_started(run_id, _now_iso())
    result = launcher.launch_agent(agent_manifest, run_id, input_dir, output_dir)
    db.set_resource_usage(run_id, result["peak_cpu_percent"], result["peak_memory_mb"])

    if result["exit_code"] != 0:
        db.mark_finished(run_id, "failed", _now_iso(), error_message=f"agent exited {result['exit_code']}")
        return

    dest = artifacts.collect(run_id, output_dir)
    report_path = dest / "report.json"

    if report_path.exists():
        db.set_report_path(run_id, artifacts.report_relative_path(run_id))
        _evaluate_schema(run_id, report_path)
    else:
        db.set_schema_validation(run_id, False, ["report.json was not produced"])

    db.mark_finished(run_id, "completed", _now_iso())


def _evaluate_schema(run_id: str, report_path: Path) -> None:
    """
    Phase 7: deterministic schema check, run immediately after artifact
    collection. Uses jsonschema.ValidationError.message rather than
    str(e) -- the latter dumps the entire schema alongside the error,
    which is both useless for display and needlessly bloats the stored
    schema_errors value. .message is the short, human-readable part
    (e.g. "'summary' is a required property"). For multiple errors,
    iter_errors collects each one's short message rather than only
    surfacing the first failure validate() would raise on.
    """
    with open(WCAG_OUTPUT_SCHEMA_PATH) as f:
        schema = json.load(f)
    with open(report_path) as f:
        report = json.load(f)

    validator = jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(report), key=lambda e: list(e.path))

    if not errors:
        db.set_schema_validation(run_id, True)
    else:
        messages = [f"{'.'.join(str(p) for p in e.path) or '(root)'}: {e.message}" for e in errors]
        db.set_schema_validation(run_id, False, messages)


@app.get("/runs")
def list_runs(limit: int = 50):
    return db.list_runs(limit)


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    run = db.get_run(run_id)
    if run is None:
        raise HTTPException(404, f"unknown run_id: {run_id!r}")
    return run


@app.post("/runs/{run_id}/approve")
def approve_run(run_id: str):
    if db.get_run(run_id) is None:
        raise HTTPException(404, f"unknown run_id: {run_id!r}")
    db.set_approval(run_id, "approved")
    return db.get_run(run_id)


@app.post("/runs/{run_id}/reject")
def reject_run(run_id: str):
    if db.get_run(run_id) is None:
        raise HTTPException(404, f"unknown run_id: {run_id!r}")
    db.set_approval(run_id, "rejected")
    return db.get_run(run_id)


@app.get("/agents")
def list_agents():
    return registry.list_all()