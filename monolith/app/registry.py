"""
monolith/app/registry.py

Scans agents/*/agent.yaml at startup, validates each against
schemas/agent.schema.json, and holds them in memory. No hot-reload
in v1 -- restart the monolith to pick up a new/changed agent.yaml.
"""

import json
from pathlib import Path

import yaml
import jsonschema

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
AGENTS_DIR = REPO_ROOT / "agents"
SCHEMA_PATH = REPO_ROOT / "schemas" / "agent.schema.json"


class AgentRegistryError(Exception):
    pass


class AgentRegistry:
    def __init__(self):
        self._agents: dict[str, dict] = {}
        with open(SCHEMA_PATH) as f:
            self._schema = json.load(f)

    def load_all(self) -> None:
        """
        Scan agents/*/agent.yaml, validate each, and load into memory.
        A single invalid agent.yaml does NOT prevent the others from
        loading -- it's logged and skipped, so one bad manifest can't
        take down the whole registry at startup.
        """
        self._agents.clear()
        if not AGENTS_DIR.exists():
            return

        for agent_dir in sorted(AGENTS_DIR.iterdir()):
            manifest_path = agent_dir / "agent.yaml"
            if not manifest_path.is_file():
                continue
            try:
                self._load_one(manifest_path)
            except Exception as e:
                # Intentionally broad: a malformed YAML file, a schema
                # violation, or a duplicate id should all be reported
                # the same way -- skip this agent, keep going.
                print(f"[registry] WARNING: skipping {manifest_path}: {e}")

    def _load_one(self, manifest_path: Path) -> None:
        with open(manifest_path) as f:
            manifest = yaml.safe_load(f)

        jsonschema.validate(instance=manifest, schema=self._schema)

        agent_id = manifest["metadata"]["id"]
        if agent_id in self._agents:
            raise AgentRegistryError(
                f"duplicate agent id '{agent_id}' (already loaded from "
                f"{self._agents[agent_id]['_manifest_path']})"
            )

        manifest["_manifest_path"] = str(manifest_path)
        self._agents[agent_id] = manifest
        print(f"[registry] loaded agent '{agent_id}' from {manifest_path}")

    def get(self, agent_id: str) -> dict | None:
        return self._agents.get(agent_id)

    def list_ids(self) -> list[str]:
        return list(self._agents.keys())

    def list_all(self) -> list[dict]:
        return list(self._agents.values())