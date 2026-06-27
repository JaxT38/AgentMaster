"""
agent_runtime/events.py

Thin writer for events.jsonl, matching schemas/event.schema.json.
Each call appends one newline-delimited JSON object and flushes
immediately, since the monolith (Phase 5/6) tails this file live
for dashboard progress -- buffered writes would make progress
appear to stall.
"""

import json
import os
from datetime import datetime, timezone


class EventWriter:
    def __init__(self, run_id: str, output_dir: str):
        self.run_id = run_id
        self.path = os.path.join(output_dir, "events.jsonl")
        # Open in append mode once; the file may already exist if a
        # previous attempt partially ran (shouldn't happen in normal
        # operation since each run_id is unique, but don't clobber).
        self._fh = open(self.path, "a", buffering=1)  # line-buffered

    def _write(self, event_type: str, message: str | None = None, data: dict | None = None) -> None:
        event = {
            "run_id": self.run_id,
            "event_type": event_type,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if message is not None:
            event["message"] = message
        if data is not None:
            event["data"] = data
        self._fh.write(json.dumps(event) + "\n")
        self._fh.flush()

    def started(self) -> None:
        self._write("started")

    def step(self, message: str, step_name: str, progress: float) -> None:
        self._write("step", message=message, data={"step_name": step_name, "progress": progress})

    def artifact_written(self, relative_path: str, artifact_type: str) -> None:
        self._write(
            "artifact-written",
            data={"relative_path": relative_path, "artifact_type": artifact_type},
        )

    def completed(self, output_path: str) -> None:
        self._write("completed", data={"output_path": output_path})

    def failed(self, message: str, error_code: str | None = None, fatal: bool = True) -> None:
        data = {"fatal": fatal}
        if error_code is not None:
            data["error_code"] = error_code
        self._write("failed", message=message, data=data)

    def close(self) -> None:
        self._fh.close()