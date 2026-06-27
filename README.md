# Local Agent Platform

A local-first, Docker-based platform for running specialized agents (starting with a WCAG 2.2 accessibility auditor) without giving them host-level access. Built to run on Ubuntu 24.04, Debian 13, and macOS.

This repo is a deliberately trimmed-down **v1 monolith** version of a larger blueprint (see `docs/full-blueprint.md` for the original design). The goal of v1 is to prove the full pipeline — agent manifest → isolated run → artifacts → evaluation → human approval — with as few moving parts as possible. Services get split out later only when there's a real reason to.

## Why this exists

Run specialized local agents (accessibility/privacy audits, frontend dev, git management, content drafting, Chrome extension dev) frequently, without:
- Giving agents host root, Docker socket access, SSH keys, or the full home directory
- Trusting generated code or shell commands by default
- Locking the agent definitions to one machine's Docker setup

## Architecture (v1)

```
┌─────────────────────────────┐
│        Agent Monolith       │   <- single FastAPI app
│                              │
│  Agent Registry (reads      │
│    agent.yaml from disk)    │
│  Launcher (docker run +     │
│    hardening flags)         │
│  Artifact Writer (local fs) │
│  Run history (SQLite)       │
└──────────────┬───────────────┘
               │
        docker run (rootless)
               │
   ┌───────────▼────────────┐       ┌────────────────────┐
   │  Agent Container       │──────▶│  Egress Proxy       │
   │  (browser-audit-runtime)│      │  (ACL-enforced,      │
   │  --network=none direct  │      │   GET/HEAD only)     │
   └─────────────────────────┘       └──────────┬──────────┘
                                                  │
                                          allowlisted domains only
```

No Postgres, Redis, Prometheus, or Grafana in v1. CPU/memory is captured via `docker stats`. Run history is SQLite. Dashboard is a single page hitting the monolith's API directly.

### Model runtime

Model serving is **Docker Model Runner**, not Ollama. It runs as a host-level Docker Desktop service exposing an OpenAI-compatible API — agent containers never talk to it directly. They go through the monolith's model-gateway logic, which is coded against the OpenAI-compatible API surface rather than any runtime-specific client, so the backend (Docker Model Runner now, possibly Ollama or something else on a given machine later) stays swappable. Record per-machine GPU acceleration support (Metal on the Mac, NVIDIA Container Toolkit / driver state on Linux) as part of the Phase 0 hardware notes — acceleration support is not guaranteed to be identical across machines.

## First agent: WCAG 2.2 Auditor

The first agent is a read-only accessibility auditor, not the Git manager. It exercises browser automation, tool calling, structured output, artifacts, screenshots, evaluation, CPU monitoring, portability, and the security boundaries — without any risk of host or repo damage.

### Network policy for this agent

- No direct container egress — all traffic forced through a proxy via `HTTP_PROXY`/`HTTPS_PROXY`.
- Proxy ACL = `${TARGET_DOMAIN}` (exact match, from the run manifest) + a curated `common-web-assets` wildcard list (CDNs, font hosts, analytics) maintained in this repo.
- **Method restriction: GET/HEAD only.** POST/PUT/DELETE/PATCH are denied at the proxy regardless of domain — the auditor observes, it never submits.
- DNS resolution scoped to the same allowed set; no open resolution.
- Everything denied is logged. Repeated denials on the same domain across runs are the signal to review and possibly promote it into the curated list.

## Repo layout

```
agent-platform/
├── README.md
├── docs/
│   ├── full-blueprint.md          # original full-scale design, for later phases
│   └── build-plan.md              # step-by-step build doc (this project)
├── monolith/
│   ├── app/
│   │   ├── main.py                 # FastAPI app entrypoint
│   │   ├── registry.py             # reads agent.yaml files
│   │   ├── launcher.py             # docker run wrapper + hardening flags
│   │   ├── artifacts.py            # writes run artifacts to disk
│   │   ├── model_gateway.py        # OpenAI-compatible client -> Docker Model Runner
│   │   └── db.py                   # SQLite run history
│   └── requirements.txt
├── agents/
│   └── wcag22-auditor/
│       └── agent.yaml
├── runtimes/
│   └── browser-audit-runtime/
│       ├── Dockerfile
│       └── agent_runtime/          # python package: playwright + axe-core driver
├── proxy/
│   ├── Dockerfile
│   ├── squid.conf.template         # ACL template, filled per-run
│   └── common-web-assets.txt       # curated CDN/asset domain list
├── workspaces/                     # per-run input/output, gitignored
├── reports/                        # generated audit reports, gitignored
└── dashboard/
    └── index.html                  # single-page run viewer
```

## Getting started

See [`docs/build-plan.md`](docs/build-plan.md) for the full step-by-step build sequence. Short version:

1. Set up rootless Docker on your dev machine.
2. Build the egress proxy image and the browser-audit-runtime image.
3. Bring up the monolith (FastAPI + SQLite).
4. Register the `wcag22-auditor` agent manifest.
5. Run an audit against a test site and confirm the milestone criteria below.

## Milestone acceptance criteria (v1)

Status as of `v0.1.0-wcag-milestone` (tagged after Phase 10):

- [x] Agent runs on Ubuntu 24 via rootless Docker
- [ ] Same image runs on Apple Silicon Mac — **not yet tested.** Intentionally deferred until v1 was proven on Linux first; Mac rootless Docker setup itself has not been done. See `docs/deferred-tasks.md`.
- [ ] Same image runs on Debian 13 — **not yet tested.** No Debian 13 environment was available during v1 development; noted as a known gap rather than assumed to work.
- [x] No agent has host Docker socket or host-root access
- [x] Agent container has no direct internet route (proxy-only, ACL-enforced, GET/HEAD-only for plaintext HTTP). **Known caveat:** HTTPS method enforcement is weaker than plaintext HTTP — domain allowlisting holds for HTTPS via CONNECT/ssl_ports, but method-level restriction does not survive the TLS tunnel. See `docs/deferred-tasks.md` ("HTTPS method enforcement").
- [x] CPU and memory are visible per run. **Known caveat:** for very short-lived runs (a few seconds), `docker stats`' own sampling resolution can underreport true peak usage — see `docs/issues-log.md` Issue 26. Manual `docker stats` observation is the fallback for verifying a specific run's resource usage if the automated number looks suspicious.
- [x] Run output is schema-valid — enforced automatically after every run, with a negative-case proof (`monolith/scripts/reevaluate.py --inject-malformed`) confirming the evaluator actually catches malformed output, not just passes everything.
- [x] Artifacts (report, screenshots, logs) are preserved in `reports/<run_id>/`
- [x] A failed run is clearly visible in the dashboard
- [x] A human can approve or reject the result, via both the API and the dashboard UI
- [x] The agent manifest can change without changing monolith code — proven by the registry loading `agent.yaml` at startup independent of any application logic change

**End-to-end proof:** a single `POST /runs` call (or a single form submission in the dashboard) now automatically performs every step that was originally done by hand across Phases 2-4: generating the per-run proxy ACL, restarting the proxy, launching the hardened container, polling resource usage, collecting artifacts, validating the schema, and recording the full run history.

## Security principles (non-negotiable, carried into every future agent)

- Agents never run directly on the host.
- No host root, passwordless sudo, Docker socket, full home directory, SSH keys, or `/etc` write access.
- Generated code and shell commands are treated as untrusted by default.
- Isolation level matches risk: rootless containers for text-only agents; stronger isolation (gVisor / microVM, added later) for code execution, package installation, and git operations.
- Network egress is allowlisted and proxy-enforced, never left to the container's own restraint.
- Fine-tuning is a last resort — context, prompts, retrieval, and evaluation come first.

## Roadmap beyond v1

See `docs/full-blueprint.md` for the full design this repo is trimmed from, including later phases: remediation agents (HTML/CSS/frontend), Git isolation via microVM, CPRA/GDPR audit agents, content agents, Chrome extension agents, and a native macOS runner. Those get built only after the WCAG auditor milestone is solid and stable.

## License

TBD.