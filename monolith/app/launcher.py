"""
monolith/app/launcher.py

Automates everything that's been done by hand across Phases 2-4:
  - generating the per-run proxy ACL (acl_generator.py)
  - force-restarting the proxy container so it picks up the fresh ACL
    (Squid only reads config at startup -- see docs/issues-log.md Issue 15)
  - creating the per-run workspace with permissions the agent container's
    non-root user can actually write to (see docs/issues-log.md Issue 17 /
    docs/deferred-tasks.md "Phase 5 launcher requirements")
  - constructing and running the hardened `docker run` invocation
  - polling `docker stats` while the container runs (Phase 6)
  - collecting the exit code, logs, and artifacts

Shells out to the `docker` CLI via subprocess rather than the Docker SDK,
to stay consistent with how every prior phase was operated and tested
by hand -- if this ever needs to move to the SDK, the command
construction here is the reference for exactly what flags matter.
"""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PROXY_DIR = REPO_ROOT / "proxy"
WORKSPACES_DIR = REPO_ROOT / "workspaces"

PROXY_IMAGE = "agent-platform/proxy:1.0.0"
PROXY_CONTAINER_NAME = "agent-platform-proxy"
PROXY_NETWORK = "agent-platform-net"
PROXY_NETWORK_ALIAS = "proxy"

DOCKER_STATS_POLL_INTERVAL_SECONDS = 2
DOCKER_STATS_INITIAL_POLL_INTERVAL_SECONDS = 0.25  # tight initial polling so very
                                                    # short-lived containers (a few
                                                    # seconds total) still get at
                                                    # least one real sample before
                                                    # they exit -- see
                                                    # docs/issues-log.md Issue 22
CONTAINER_WAIT_TIMEOUT_SECONDS = 600  # hard ceiling; overridden per-agent below if set


class LaunchError(Exception):
    pass


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Thin wrapper so every docker CLI call is logged the same way."""
    print(f"[launcher] $ {' '.join(cmd)}")
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


# ---------------------------------------------------------------------------
# ACL generation (reuses the logic proven in Phase 2/3 testing)
# ---------------------------------------------------------------------------

def _extract_host(entry_url: str) -> str:
    parsed = urlparse(entry_url)
    if not parsed.hostname:
        raise LaunchError(f"could not parse hostname from entry_url: {entry_url!r}")
    return parsed.hostname.lower()


def _derive_registrable_domain(host: str) -> str:
    # Same documented heuristic/limitation as acl_generator.py --
    # incorrect for multi-part TLDs (e.g. co.uk). See deferred-tasks.md.
    labels = host.split(".")
    return ".".join(labels[-2:]) if len(labels) >= 2 else host


def build_acl_lines(envelope: dict, agent_manifest: dict) -> list[str]:
    target = envelope["target"]
    host = _extract_host(target["entry_url"])
    scope = target["scope"]

    if scope == "single-page":
        target_lines = [host]
    elif scope == "site":
        target_lines = [f".{_derive_registrable_domain(host)}"]
    else:
        raise LaunchError(f"unknown scope: {scope!r}")

    network_perms = agent_manifest["permissions"]["network"]
    static_extras = network_perms.get("allowed_targets", [])

    common_list_path = PROXY_DIR / "common-web-assets.txt"
    common_list = []
    if common_list_path.exists():
        with open(common_list_path) as f:
            common_list = [l.strip() for l in f if l.strip() and not l.strip().startswith("#")]

    seen = set()
    ordered = []
    for line in target_lines + list(static_extras) + common_list:
        if line and line not in seen:
            seen.add(line)
            ordered.append(line)
    return ordered


def write_acl_file(lines: list[str], run_dir: Path) -> Path:
    acl_path = run_dir / "allowed-domains.acl"
    with open(acl_path, "w") as f:
        f.write("# Auto-generated per-run ACL. Do not edit by hand.\n")
        for line in lines:
            f.write(line + "\n")
    return acl_path


# ---------------------------------------------------------------------------
# Proxy lifecycle
# ---------------------------------------------------------------------------

def restart_proxy(acl_path: Path) -> None:
    """
    Force a fresh proxy container start with the new ACL.
    Squid only reads its config at process startup -- editing the
    mounted file on an already-running container has no effect
    (docs/issues-log.md Issue 15) -- so this always removes and
    recreates the container rather than trying to reload in place.
    """
    _ensure_network()

    _run(["docker", "rm", "-f", PROXY_CONTAINER_NAME])

    squid_conf = PROXY_DIR / "squid.conf"
    if not squid_conf.exists():
        # squid.conf is generated once from the template; the template
        # itself doesn't change per run, only allowed-domains.acl does.
        shutil.copy(PROXY_DIR / "squid.conf.template", squid_conf)

    result = _run([
        "docker", "run", "--rm", "-d",
        "--name", PROXY_CONTAINER_NAME,
        "--network", PROXY_NETWORK,
        "--network-alias", PROXY_NETWORK_ALIAS,
        "-v", f"{squid_conf}:/etc/squid/squid.conf:ro",
        "-v", f"{acl_path}:/etc/squid/allowed-domains.acl:ro",
        PROXY_IMAGE,
    ])
    if result.returncode != 0:
        raise LaunchError(f"failed to start proxy container: {result.stderr}")

    # Give Squid a moment to finish initializing before the agent
    # container tries to use it -- a fixed short sleep is good enough
    # for v1; Phase 5+ could poll the access log or a healthcheck
    # endpoint instead if this proves flaky in practice.
    time.sleep(1.5)


def _ensure_network() -> None:
    result = _run(["docker", "network", "inspect", PROXY_NETWORK])
    if result.returncode != 0:
        create = _run(["docker", "network", "create", PROXY_NETWORK])
        if create.returncode != 0:
            raise LaunchError(f"failed to create network {PROXY_NETWORK}: {create.stderr}")


# ---------------------------------------------------------------------------
# Workspace setup
# ---------------------------------------------------------------------------

def prepare_workspace(run_id: str, envelope: dict) -> tuple[Path, Path]:
    run_dir = WORKSPACES_DIR / run_id
    input_dir = run_dir / "input"
    output_dir = run_dir / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(input_dir / "run-request.json", "w") as f:
        json.dump(envelope, f, indent=2)

    # See docs/issues-log.md Issue 17 / deferred-tasks.md: the agent
    # container runs as a non-root uid that doesn't correspond to a
    # writable uid on the host by default, especially under rootless
    # Docker's uid remapping. chmod 777 is the pragmatic v1 fix --
    # revisit with a proper --user mapping if this needs tightening.
    os.chmod(output_dir, 0o777)

    return input_dir, output_dir


# ---------------------------------------------------------------------------
# Agent container lifecycle
# ---------------------------------------------------------------------------

def launch_agent(agent_manifest: dict, run_id: str, input_dir: Path, output_dir: Path) -> dict:
    """
    Runs the agent container to completion, polling docker stats while
    it runs. Returns a dict: {exit_code, peak_cpu_percent, peak_memory_mb}.
    """
    image = agent_manifest["runtime"]["image"]
    timeout = agent_manifest["runtime"].get("timeout_seconds", CONTAINER_WAIT_TIMEOUT_SECONDS)
    resource_limits = agent_manifest["runtime"].get("resource_limits", {})

    container_name = f"agent-platform-run-{run_id}"

    cmd = [
        "docker", "run", "-d",
        "--name", container_name,
        "--network", PROXY_NETWORK,
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--read-only",
        "--tmpfs", "/tmp",
        "-v", f"{input_dir}:/workspace/input:ro",
        "-v", f"{output_dir}:/workspace/output:rw",
    ]

    if "cpu_limit" in resource_limits:
        cmd += ["--cpus", resource_limits["cpu_limit"]]
    if "memory_limit" in resource_limits:
        cmd += ["--memory", resource_limits["memory_limit"]]
    if "pids_limit" in resource_limits:
        cmd += ["--pids-limit", str(resource_limits["pids_limit"])]

    cmd.append(image)

    result = _run(cmd)
    if result.returncode != 0:
        raise LaunchError(f"failed to start agent container: {result.stderr}")
    container_id = result.stdout.strip()

    peak_cpu = None
    peak_mem_mb = None
    samples_captured = 0
    start_time = time.monotonic()

    try:
        # First few iterations poll quickly, since a short-lived agent
        # (a few seconds total, e.g. a single-page audit that errors
        # out fast) can otherwise exit before a slower interval ever
        # gets a sample -- docker stats against an already-exited
        # container just fails silently, leaving peak values at their
        # initial value with no indication anything went wrong.
        # See docs/issues-log.md Issue 22.
        iteration = 0
        while True:
            inspect = _run(["docker", "inspect", "-f", "{{.State.Running}}", container_id])
            still_running = inspect.stdout.strip() == "true"

            stats = _poll_stats(container_id)
            if stats:
                samples_captured += 1
                peak_cpu = max(peak_cpu or 0.0, stats["cpu_percent"])
                peak_mem_mb = max(peak_mem_mb or 0.0, stats["mem_mb"])

            if not still_running:
                break

            if time.monotonic() - start_time > timeout:
                _run(["docker", "kill", container_id])
                raise LaunchError(f"agent container exceeded timeout_seconds={timeout}, killed")

            sleep_for = (
                DOCKER_STATS_INITIAL_POLL_INTERVAL_SECONDS
                if iteration < 4
                else DOCKER_STATS_POLL_INTERVAL_SECONDS
            )
            time.sleep(sleep_for)
            iteration += 1

        exit_code_result = _run(["docker", "inspect", "-f", "{{.State.ExitCode}}", container_id])
        exit_code = int(exit_code_result.stdout.strip())
    finally:
        logs = _run(["docker", "logs", container_id])
        log_path = output_dir / "container.log"
        with open(log_path, "w") as f:
            f.write(logs.stdout)
            f.write(logs.stderr)
        _run(["docker", "rm", "-f", container_id])

    if samples_captured == 0:
        print(
            f"[launcher] WARNING: never captured a docker stats sample for "
            f"{container_id} -- container likely exited too quickly to measure. "
            f"Reporting resource usage as null/unknown rather than 0.0."
        )

    return {
        "exit_code": exit_code,
        "peak_cpu_percent": peak_cpu,
        "peak_memory_mb": peak_mem_mb,
        "samples_captured": samples_captured,
    }


def _poll_stats(container_id: str) -> dict | None:
    result = _run([
        "docker", "stats", "--no-stream", "--format",
        "{{.CPUPerc}}\t{{.MemUsage}}", container_id,
    ])
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        cpu_str, mem_str = result.stdout.strip().split("\t")
        cpu_percent = float(cpu_str.replace("%", ""))
        # MemUsage looks like "123.4MiB / 1GiB" -- take the used side
        used_str = mem_str.split("/")[0].strip()
        mem_mb = _parse_mem_to_mb(used_str)
        return {"cpu_percent": cpu_percent, "mem_mb": mem_mb}
    except (ValueError, IndexError):
        return None


def _parse_mem_to_mb(value: str) -> float:
    value = value.strip()
    if value.endswith("GiB"):
        return float(value[:-3]) * 1024
    if value.endswith("MiB"):
        return float(value[:-3])
    if value.endswith("KiB"):
        return float(value[:-3]) / 1024
    if value.endswith("B"):
        return float(value[:-1]) / (1024 * 1024)
    raise ValueError(f"unrecognized memory unit: {value!r}")