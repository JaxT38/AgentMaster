# Build Plan: Local Agent Platform v1

This is the step-by-step sequence for building the v1 monolith platform and shipping the first agent (WCAG 2.2 Auditor). It's written so you can work through it yourself, in order, without LLM assistance. Each phase has a clear done-state before you move to the next one.

Reference: `docs/full-blueprint.md` is the original full-scale design. This plan is the trimmed v1 path through it — monolith instead of microservices, rootless Docker only, no Postgres/Redis/Prometheus/Grafana yet.

---

## Phase 0 — Repo and machine setup

**Goal:** A repo exists, pushed to GitHub, with the layout from the README, and rootless Docker is confirmed working on your dev machine.

1. Create the repo locally and on GitHub (private, since this will contain client-audit-style tooling).
2. Create the directory structure from the README's "Repo layout" section. Empty dirs are fine for now — add `.gitkeep` where needed.
3. Add a `.gitignore` covering: `workspaces/`, `reports/`, `__pycache__/`, `.venv/`, `*.db`, `.env`.
4. Confirm rootless Docker is installed and running (`docker info` should show `rootless` in context, not the system daemon). If you're on the Dell G7 Ubuntu box, decide now whether you're using your existing Docker setup or switching to rootless mode specifically for this project — they are not the same thing, and the security guarantees only hold with rootless.
5. Confirm the same check on the Mac M4 Air (Docker Desktop, rootless mode if available, or note the gap if not).
6. Push the skeleton repo with README.md and this build plan committed.

**Done when:** `docker info` confirms rootless on both machines, repo skeleton is on GitHub.

---

## Phase 1 — Schemas

**Goal:** The contracts that everything else depends on are written down and versioned, before any code that consumes them.

1. Define `agent.yaml` schema v1 — fields: `metadata` (id, name, version, category), `runtime` (image, command, execution_mode), `platforms`, `model` (capability, minimum_context, temperature), `capabilities`, `permissions` (network: mode, allowed_targets, method_restriction; workspace: input/output access).
2. Define the standard **task envelope** schema — what gets handed to an agent at run start (target URL, run ID, any parameters) and what a `run-request.json` looks like on disk.
3. Define the standard **event** schema — how the agent reports progress/status back (e.g. `started`, `step`, `artifact-written`, `completed`, `failed`).
4. Define the **output schema** for the WCAG agent specifically — what a completed audit report JSON looks like (findings list, severity, screenshot references, axe-core raw results, page URL, timestamp).
5. Write these as JSON Schema files under `schemas/` and commit them. Nothing consumes them yet — that's fine, this is the contract everyone codes against.

**Done when:** Four schema files exist in `schemas/`, each with at least one valid example JSON file next to it for reference.

---

## Phase 2 — Egress proxy and curated allowlist

**Goal:** A standalone proxy container exists that enforces domain + method restrictions, independent of any agent.

1. Choose your proxy tool (Squid is the reference choice in the README; suitable alternatives exist if you prefer something simpler to configure).
2. Write the base proxy config: deny-all by default.
3. Build the **method restriction**: allow only GET and HEAD; deny POST/PUT/DELETE/PATCH regardless of destination.
4. Build the **ACL template** mechanism: a config file the launcher regenerates per-run, combining:
   - The single `${TARGET_DOMAIN}` for that run (exact match)
   - The contents of `proxy/common-web-assets.txt` (your curated CDN/font/analytics suffix list, wildcarded)
5. Seed `common-web-assets.txt` with an initial list (common CDNs, Google Fonts, analytics domains you expect to encounter) — it's fine to start small and grow it from observed denials later.
6. Configure logging so every denied request (domain or method) is written to a log file you can review after a run.
7. Test in isolation: run the proxy container by itself, point `curl` at it with `HTTP_PROXY` set, confirm allowed domains/methods succeed and everything else is denied and logged.
8. Confirm DNS is scoped the same way — the container using the proxy should not be able to resolve domains outside the allowed set either.

**Done when:** You can demonstrate, with `curl` through the proxy: an allowed GET succeeds, a GET to a non-allowlisted domain is denied and logged, and a POST to an allowed domain is denied and logged.

---

## Phase 3 — Browser-audit runtime image

**Goal:** A multi-arch container image exists with everything the WCAG auditor needs, with no host egress except through the proxy.

1. Write the `Dockerfile` for `browser-audit-runtime`: base image, Node.js (for Playwright) or Python+Playwright depending on your language choice, Chromium, axe-core.
2. Bake in `HTTP_PROXY`/`HTTPS_PROXY` env vars pointing at the proxy service's network alias (not a hardcoded IP — it should resolve via Docker's internal DNS on the shared network).
3. Apply the hardening profile from the blueprint: run as non-root user, read-only root filesystem, `cap_drop: ALL`, `no-new-privileges`, tmpfs for `/tmp`, `pids_limit`.
4. Pin all dependency versions explicitly (no floating `latest` tags, no unpinned npm/pip packages).
5. Build for `linux/amd64` and `linux/arm64` using Docker buildx.
6. Smoke-test: run the container with `--network=none` directly attached to nothing but the proxy's network, confirm Chromium can still reach an allowlisted test page through the proxy and cannot reach anything else.

**Done when:** The image builds for both architectures, runs on both the Ubuntu and Mac machines, and a manual smoke test confirms the network restriction holds (allowed page loads, disallowed page fails with the proxy's denial visible in its log).

---

## Phase 4 — Agent runtime code (inside the container)

**Goal:** The actual Python (or Node) program that runs inside `browser-audit-runtime` and performs the audit.

1. Write `agent_runtime`: reads the task envelope (target URL, run ID) from a mounted read-only input path.
2. Launches Playwright/Chromium, navigates to the target URL.
3. Runs axe-core against the page, captures the raw results.
4. Captures a full-page screenshot.
5. Writes the output JSON (matching the Phase 1 output schema) and the screenshot to the mounted output path (read-write).
6. Emits events (per the Phase 1 event schema) at minimum on start, on completion, and on failure — write these to a log file in the output path; the monolith will read them.
7. Exit cleanly with a non-zero code on failure so the launcher can detect it.

**Done when:** Running the container manually with a sample input produces a schema-valid output JSON and a screenshot file in the output directory, with no manual intervention.

---

## Phase 5 — Monolith: registry, launcher, artifacts, run history

**Goal:** A single FastAPI app that can register the WCAG agent, launch a run, and record the result.

1. Set up the FastAPI project under `monolith/`. Add SQLite via SQLAlchemy or raw `sqlite3` — keep it simple, this is v1.
2. `registry.py`: on startup, scan `agents/*/agent.yaml`, parse and validate against the Phase 1 schema, hold them in memory (or a `agents` table).
3. `launcher.py`: given an agent ID and a task envelope, construct the `docker run` command with:
   - The hardening flags from Phase 3
   - The per-run network setup: agent container attached only to the proxy's Docker network, no other network
   - Volume mounts: `workspaces/<run-id>/input:/workspace/input:ro`, `workspaces/<run-id>/output:/workspace/output:rw`
   - Before launching the agent container, regenerate the proxy's ACL file for this run (from the agent.yaml's `permissions.network.allowed_targets` + the curated list) and (re)start or reload the proxy container.
4. `artifacts.py`: after the run completes, copy/move the contents of the output workspace into `reports/<run-id>/` and record paths in the run history table.
5. `db.py`: a `runs` table — run ID, agent ID, target, status, start/end time, CPU/memory peak (Phase 6), result path.
6. Wire up basic API endpoints: `POST /runs` (start a run), `GET /runs/{id}` (status + result), `GET /runs` (list).
7. `model_gateway.py`: a thin client coded against the OpenAI-compatible API surface, pointed at your local **Docker Model Runner** endpoint (not Ollama). Keep the base URL and model name as config, not hardcoded, so a future machine running a different backend (Ollama, or something else) just needs a config change, not a code change. Confirm GPU acceleration status per machine (Metal on the Mac M4 Air, NVIDIA Container Toolkit / driver presence on the Ubuntu/Debian boxes) and note it in your Phase 0 hardware record — don't assume parity.

**Done when:** Hitting `POST /runs` with a target URL and the WCAG agent ID actually launches the container end-to-end and you can poll `GET /runs/{id}` to see it complete with a result path. Separately, confirm `model_gateway.py` can successfully complete a basic prompt against Docker Model Runner's OpenAI-compatible endpoint before wiring any agent logic to depend on it.

---

## Phase 6 — CPU/memory visibility

**Goal:** Per-run resource usage is captured without standing up Prometheus/Grafana yet.

1. While a run's container is active, poll `docker stats <container-id> --no-stream` on an interval (e.g. every 2 seconds) from the monolith.
2. Record peak CPU% and peak memory into the run history row.
3. Surface this on the run detail API response.

**Done when:** A completed run's API response includes peak CPU and memory figures that match what you'd see running `docker stats` manually during the same run.

---

## Phase 7 — Evaluator (deterministic schema check)

**Goal:** Every completed run is automatically checked against the Phase 1 output schema before it's presented as "done."

1. Add a simple JSON Schema validation step in the monolith, run immediately after artifact collection.
2. Mark the run as `schema-valid` or `schema-invalid` in the run history.
3. If invalid, surface the validation errors in the run detail response — don't silently pass a bad result through.

**Done when:** Feeding the evaluator a deliberately malformed output JSON correctly flags `schema-invalid` with a useful error message.

---

## Phase 8 — Minimal dashboard

**Goal:** A single HTML page where you can submit a URL, watch a run, and see the result — no separate dashboard-api service.

1. One static page (`dashboard/index.html`) that calls the monolith's API directly (same origin or CORS-permitted).
2. A form: target URL + agent selector (just WCAG auditor for now) → `POST /runs`.
3. A run list view: poll `GET /runs`, show status, CPU/memory, schema-valid flag.
4. A run detail view: show the findings, screenshot, and an **Approve / Reject** button that calls a new `POST /runs/{id}/approve` or `/reject` endpoint, which just writes that decision into the run history.

**Done when:** You can submit a URL from the browser, watch the run go from `running` to `completed`, see the screenshot and findings, and click Approve.

---

## Phase 9 — Cross-platform validation

**Goal:** Confirm the milestone acceptance criteria from the README, for real, on all three target environments.

1. Run the full pipeline on Ubuntu 24 (Dell G7).
2. Run the exact same `agent.yaml` and image on the Mac M4 Air.
3. Run it on Debian 13, if/when you have that environment available — otherwise note it as a known gap rather than skipping silently.
4. For each, confirm: no host Docker socket exposure, no host-root, proxy-only egress holds, CPU/memory appear, output is schema-valid, artifacts are preserved.
5. Deliberately break something (point the agent at a non-existent URL, or kill the container mid-run) and confirm the dashboard clearly shows a failed run rather than hanging or showing a false success.

**Done when:** All items in the README's "Milestone acceptance criteria (v1)" checklist are checked off, on at least two of the three platforms.

---

## Phase 10 — Tag and document the milestone

**Goal:** Lock in this working state before moving on to anything else.

1. Tag the repo (e.g. `v0.1.0-wcag-milestone`).
2. Write up the one approved run as a "golden example" — keep its input/output pair somewhere in the repo (e.g. `examples/golden/wcag-run-001/`) for future regression comparison.
3. Update the README's checklist with the actual results (which platforms passed, any known gaps like Debian not yet tested).

**Done when:** The tag exists, the golden example is committed, and the README accurately reflects what's actually been proven versus what's still aspirational.

---

## What comes after this plan

Once Phase 10 is done and stable, the next phases (in order, per the full blueprint) are:

- **Remediation agents** (HTML/CSS/frontend-integration developers) — these write code, so this is where you introduce stronger isolation (gVisor on Linux, or a tightened container profile on Mac) before letting them run.
- **Git isolation** — microVM-backed git-refactor/branch/repository managers, no host git credentials, no remote push, no force push.
- **Privacy audit agents** (CPRA, GDPR) — same browser-runtime pattern as WCAG, plus versioned legal reference packs.
- **Content agents** — lowest risk, rootless containers are sufficient.
- **Chrome extension agents** — manifest validation, packaging, browser test runner.
- **Native macOS runner** — only if/when native (non-containerized) macOS testing becomes necessary.

Each of these gets its own short build-plan addendum when you get there — don't try to design them in detail now.