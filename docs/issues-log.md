# Setup & Build Issues Log

A running record of problems hit while building the local agent platform, how they were diagnosed, and how they were resolved. Keep this updated as you go — it doubles as onboarding notes for your future self (or anyone else) on a new machine.

Format per entry: **Symptom → Diagnosis → Fix → Verification**

---

## Phase 0 — Repo and machine setup

### Issue 1: Rootless Docker not installed (Ubuntu / Dell G7)

- **Symptom:** `docker context ls` showed only the `default` context — no `rootless` context present.
- **Diagnosis steps:**
  - `which dockerd` confirmed the standard Docker daemon binary was present (expected, not informative on its own).
  - `which dockerd-rootless-setuptool.sh` confirmed the rootless extras package *was* installed.
  - `systemctl --user status docker` returned "Unit docker.service could not be found" — confirmed the rootless user-level service had never been set up, even though the tooling existed.
- **Fix attempt 1 — blocked:** Running `dockerd-rootless-setuptool.sh install` aborted with an error that rootful Docker is running and accessible. This is a safety heuristic in the installer, not a real conflict (rootless uses a separate socket from rootful).
  - **Resolution:** Bypassed with the documented override:
    ```bash
    FORCE_ROOTLESS_INSTALL=1 dockerd-rootless-setuptool.sh install
    ```
- **Fix attempt 2 — blocked:** Installer then reported a missing prerequisite: `newuidmap` (and `newgidmap`) not found — these come from the `uidmap` package and are required for rootless user-namespace mapping.
  - **Resolution:**
    ```bash
    sudo apt-get update
    sudo apt-get install -y uidmap
    ```
  - Verified subuid/subgid ranges were already allocated for the user:
    ```bash
    grep "$(whoami)" /etc/subuid /etc/subgid
    ```
  - Re-ran the forced install command, which succeeded.
- **Verification:**
  ```bash
  docker context ls        # confirmed `rootless` context now present
  docker context use rootless
  docker info               # confirmed rootless mode
  docker run --rm hello-world   # succeeded
  ```
- **Coexistence note:** Rootful Docker (used by the unrelated AskGap project) was left running and untouched throughout. Rootless and rootful daemons run side by side on separate sockets — installing/using one does not stop or interfere with the other. Decision: don't disable the rootful daemon, since AskGap depends on it; instead, be deliberate about which context is active per project (`docker context use rootless` before AgentMaster work).
- **Follow-up / not yet done:** Set up a per-directory context switch (e.g. via `direnv`) so the AgentMaster repo automatically uses the `rootless` context without relying on manually remembering to switch each session.

### Issue 2: Created directories not appearing on GitHub after push

- **Symptom:** After pushing the repo skeleton, GitHub only showed `docs/`, `.env.example`, `.gitignore`, and `README.md`. The `scripts/`, `src/`, `tests/`, and `config/` folders created locally did not appear.
- **Diagnosis:** Git does not track empty directories — only files. The folders existed locally but had no files inside them, so there was nothing for git to commit.
- **Fix:**
  ```bash
  touch scripts/.gitkeep src/.gitkeep tests/.gitkeep config/.gitkeep
  git add scripts/.gitkeep src/.gitkeep tests/.gitkeep config/.gitkeep
  git commit -m "Add placeholder files for empty directories"
  git push
  ```
- **Verification:** Confirm all four folders now appear on GitHub after the push. `.gitkeep` files can be deleted later once real files exist in each folder.

### Open item: Repo visibility (public)

- **Goal:** Make the repo public to share progress.
- **Where to do it:** GitHub repo → Settings → Danger Zone → Change visibility → Make public.
- **Pre-flight check before flipping it public:** Confirm `.env.example` contains no real-looking secrets, and confirm `workspaces/` and `reports/` were never actually committed (only gitignored going forward — already-committed history persists even after a file is deleted unless history is rewritten). Scan full commit history once before making public, not just the current working tree.
- **Status:** Not yet actioned — pending the pre-flight check.

### Open item: Mac M4 Air rootless Docker check

- **Status:** Deferred. Not being worked in parallel with the Ubuntu box — will revisit on the Mac separately when that machine is next in scope.

---

---

## Phase 2 — Egress proxy and curated allowlist

### Issue 3: `docker build` failed with "docker buildx build requires 1 argument"

- **Symptom:** Running `docker build -t agent-platform/proxy:1.0.0` (without a trailing path) failed with a buildx usage error.
- **Diagnosis:** On modern Docker, `docker build` is an alias for `docker buildx build`, which requires the build context (a path, URL, or `-`) as a positional argument — it's not optional just because a tag was given.
- **Fix:** Add the trailing `.` to specify "current directory" as the build context:
  ```bash
  docker build -t agent-platform/proxy:1.0.0 .
  ```
- **Verification:** Build completed successfully.

### Issue 4: `acl_generator.py` failed with `JSONDecodeError: Expecting value: line 1 column 1`

- **Symptom:** Running the ACL generator against a copied test envelope file threw a JSON decode error.
- **Diagnosis:** This specific error means the file opened successfully but was empty (0 bytes) — not a missing-file error, which would have thrown `FileNotFoundError` instead. The `cp` of `schemas/task-envelope.example.json` into `tests/fixtures/envelope-single-page.json` either didn't run as expected or copied from a bad path.
- **Fix:** Verified both the source and destination file contents directly (`cat` on each) before re-copying, to confirm the source was intact and the destination actually received its contents.
- **Verification:** Re-ran `acl_generator.py` against the fixture once confirmed non-empty; it produced `allowed-domains.acl` correctly.

### Issue 5: Docker bind-mount error — "not a directory: Are you trying to mount a directory onto a file?"

- **Symptom:** `docker run` failed trying to mount `proxy/squid.conf` into the container, with an OCI runtime error about mounting a directory onto a file (or vice versa).
- **Diagnosis:** The repo only had `squid.conf.template` (the source template the launcher is meant to render per-run) — there was no actual `squid.conf` file at that path yet, so Docker's bind-mount source resolution behaved oddly against the non-existent/wrong-type path.
- **Fix:** For manual smoke testing (no real launcher templating logic yet), simply copied the template to the expected filename:
  ```bash
  cp proxy/squid.conf.template proxy/squid.conf
  ```
- **Verification:** Re-ran the same `docker run` command; the mount error was gone (a new, different error appeared next — see Issue 6).

### Issue 6: `docker: invalid reference format` on a multi-line `docker run` command

- **Symptom:** A `docker run` command split across multiple lines with `\` continuations failed with "invalid reference format," as if Docker parsed something unexpected as the image name.
- **Diagnosis:** Almost certainly a shell line-continuation issue — a trailing space after a `\` silently breaks the continuation in bash, causing the command to be parsed differently than intended.
- **Fix:** Re-ran the exact same command collapsed onto a single line, eliminating the possibility of a broken continuation.
- **Verification:** Command ran without the reference-format error (a new, real error appeared next — see Issue 7).

### Issue 7: Squid fatal config error — "is a subdomain of" / duplicate `dstdomain` ACL entries

- **Symptom:** Squid failed to start with: `ERROR: 'fonts.googleapis.com' is a subdomain of '.googleapis.com'` / `FATAL: Bungled /etc/squid/squid.conf line 24`.
- **Diagnosis:** `common-web-assets.txt` contained both a wildcard entry (`.googleapis.com`) and a specific subdomain already covered by that wildcard (`fonts.googleapis.com`). Squid treats this as a configuration error rather than silently de-duplicating — it refuses to start at all rather than risk an ambiguous ACL. The same redundancy existed for `.gstatic.com`/`fonts.gstatic.com`, `.google-analytics.com`/`www.google-analytics.com`, and `.googletagmanager.com`/`www.googletagmanager.com`.
- **Fix:** Removed the redundant specific entries, keeping only the wildcard parent for each:
  - Removed `fonts.googleapis.com` (covered by `.googleapis.com`)
  - Removed `fonts.gstatic.com` (covered by `.gstatic.com`)
  - Removed `www.google-analytics.com` (covered by `.google-analytics.com`)
  - Removed `www.googletagmanager.com` (covered by `.googletagmanager.com`)
- **Verification:** Squid started cleanly with no config errors after the fix.
- **Lesson for the future:** Any time a new wildcard entry is added to `common-web-assets.txt`, audit the rest of the file for now-redundant specific entries under it — Squid will reject the *entire* config over one redundant line rather than just ignoring it.

### Issue 8: curl `-I` combined with `-X POST` silently sent a different method than intended

- **Symptom:** A test meant to confirm POST requests are denied to an allowlisted domain instead returned `200 OK`, suggesting the method restriction wasn't working.
- **Diagnosis:** Checking squid's own `access.log` afterward showed the request had actually been logged as `HEAD http://example.com/`, not `POST` — confirming curl's `-I` flag overrode the `-X POST` flag and sent a HEAD request despite the explicit `-X POST`. This is a known curl quirk, not a proxy bug.
- **Fix:** Re-ran the test using a real POST with a body and without `-I`, dumping headers separately:
  ```bash
  curl -x http://localhost:3128 -X POST -d "test=1" http://example.com -o /dev/null -D - -s
  ```
- **Verification:** This correctly returned `403 Forbidden` (`TCP_DENIED`), and squid's access log confirmed the method was logged as `POST` this time. Cross-checking the access log is what actually caught the false pass — worth doing this any time a test result looks surprising, rather than trusting the curl flags alone.

### Phase 2 — Final verification (all checks passed)

Confirmed via squid's own `access.log`, not just curl's exit status:

```
HEAD http://example.com/                  -> TCP_MISS/200      (allowed domain, allowed method)
HEAD http://somethingnotallowed.test/     -> TCP_DENIED/403    (disallowed domain, denied + logged)
POST http://example.com/                  -> TCP_DENIED/403    (allowed domain, disallowed method, denied + logged)
```

Domain allowlisting and method restriction were proven to work **independently** of each other — an allowed domain does not bypass the method check, and an allowed method does not bypass the domain check.

---

## Phase 3 — Browser-audit runtime image

### Issue 9: Pinned an unpublished Playwright base image tag

- **Symptom:** `docker build` failed with `mcr.microsoft.com/playwright/python:v1.46.0-noble: not found`.
- **Diagnosis:** The `-noble` tag suffix was only published starting from Playwright v1.47.0 onward; earlier Noble-based images were published under the bare version tag without that suffix. `v1.46.0-noble` was never a real published tag — it was guessed rather than confirmed against the actual registry.
- **Fix:** Switched to a confirmed-published tag, `v1.55.0-noble`, and pinned the `playwright` pip package to the matching version (`playwright==1.55.0`) in the same `RUN pip install` block — the pip package version must match the base image's Playwright version, or Playwright can't locate the browser executables baked into the image.
- **Verification:** Build progressed past the `FROM` line successfully.
- **Lesson:** Always check a registry's actual published tags before pinning a version+suffix combination — don't guess at a plausible-looking tag.

### Issue 10: Local Dockerfile edits didn't match what was discussed

- **Symptom:** After being told to fix the base image tag and add the matching pip pin, repeated rebuilds kept failing with the *original* unpublished-tag error, even after supposedly editing the file.
- **Diagnosis:** The artifact shown in chat had been updated, but the actual local `Dockerfile` on disk had not been edited to match — edits discussed/shown in conversation don't automatically apply to local files on the user's machine.
- **Fix:** Manually verified the live file's contents with `grep`/`cat` before each rebuild attempt, rather than assuming an edit had taken effect, and hand-edited the file directly.
- **Verification:** `grep -n "FROM\|playwright=="  Dockerfile` confirmed both the corrected tag and the matching pip pin were actually present before rebuilding again.
- **Lesson:** After any edit instruction, verify the actual file contents directly rather than assuming the edit landed — especially before re-running an expensive build.

### Issue 11: `COPY agent_runtime/ /app/agent_runtime/` failed — directory didn't exist in the build context

- **Symptom:** Build failed with `failed to calculate checksum of ref ...: "/agent_runtime": not found`.
- **Diagnosis:** The `agent_runtime/` directory (containing `__init__.py` and `__main__.py`) didn't exist yet inside `runtimes/browser-audit-runtime/` (the build context) — it had been created one level too high, as a sibling of `browser-audit-runtime/` instead of inside it.
- **Fix:** Moved the directory into the correct location:
  ```bash
  mv agent_runtime browser-audit-runtime/
  ```
- **Verification:** Re-running `docker build` reached and completed the `COPY` step.

### Issue 12: `docker run` "Unable to find image" / "invalid reference format" — a string of typos, not real bugs

- **Symptom:** Several different `docker run` attempts failed with various image-resolution errors: `pull access denied for agent-plaform/proxy` (missing a letter), `invalid reference format`, `Unable to find image 'AgentMaster/proxy/1.0.0:latest'` (DNS lookup attempted against "AgentMaster" as if it were a registry host), and `pull access denied for agent-platform/proxy/1.0.0` (slash used instead of colon before the tag).
- **Diagnosis:** Each was a distinct manual-typing error: a dropped letter, a broken line-continuation, and — most instructively — using `/` instead of `:` to separate the image name from its tag, which Docker parses as an additional path segment (turning `name:tag` into `name/tag`, and in one case causing it to interpret part of the string as a registry hostname and attempt a real DNS lookup).
- **Fix:** Copy-pasting the exact command rather than retyping it from memory each time.
- **Verification:** `docker images | grep <name>` to confirm the exact stored repository:tag string before trying to reference it in a `docker run` command.
- **Lesson:** When an image reference error looks bizarre (e.g. a DNS lookup against your own project name), suspect a `/` vs `:` typo before suspecting something structural.

### Issue 13: Image was actually built under a different name than the documented convention (`AgentMaster/...` vs `agent-platform/...`)

- **Symptom:** After ruling out typos and Docker context mismatches, `docker run agent-platform/browser-audit-runtime:1.0.0` still couldn't find the image — but `docker images` showed it present.
- **Diagnosis:** `docker images | grep browser-audit-runtime` revealed the image was actually stored as `AgentMaster/browser-audit-runtime:1.0.0` — the original `docker build -t ...` command had apparently been run with the repo's directory name as the image prefix, not the documented `agent-platform/...` image-naming convention. No effect on Squid, bind-mount paths, or network aliases — only on this one `docker run`/`docker build` tag string. This is the concrete case that prompted formalizing the naming rule going forward: **`AgentMaster` refers to the repo/directory itself; `agent-platform` is the generic, modular prefix used for image names, network names, and anywhere else not tied specifically to this one repo.**
- **Fix:** Re-tagged the existing image to match the documented convention, avoiding an unnecessary rebuild:
  ```bash
  docker tag AgentMaster/browser-audit-runtime:1.0.0 agent-platform/browser-audit-runtime:1.0.0
  ```
- **Verification:** `docker run agent-platform/browser-audit-runtime:1.0.0 ...` resolved correctly afterward.
- **Lesson:** When an image "can't be found" but `docker images` shows something plausible, grep the exact stored name rather than assuming it must match whatever you intended to type. Going forward: image/network names use `agent-platform`, repo-path references use `AgentMaster` — don't mix them.

### Issue 14: HTTPS navigation failed — `ERR_TUNNEL_CONNECTION_FAILED` — missing CONNECT/ssl_ports handling

- **Symptom:** The runtime stub could reach the proxy (env vars correctly set, no connection-refused error) but every HTTPS navigation failed with `net::ERR_TUNNEL_CONNECTION_FAILED`.
- **Diagnosis:** Squid's access log showed `CONNECT example.com:443` being `TCP_DENIED/403`. The original `squid.conf` only defined a `safe_methods` ACL for `GET HEAD` and denied everything else — but HTTPS requires the browser to first send a `CONNECT` request to establish an encrypted tunnel, which isn't a GET or HEAD and was therefore being blocked before any actual page request could happen inside the tunnel.
- **Fix:** Added explicit `CONNECT` handling, scoped to port 443 and the same domain allowlist, as a separate mechanism from the GET/HEAD plaintext-HTTP method restriction:
  ```
  acl connect_method method CONNECT
  acl ssl_ports port 443
  http_access deny connect_method !ssl_ports
  http_access allow connect_method allowed_dst ssl_ports
  ```
- **Known limitation introduced by this fix:** Once a CONNECT tunnel is established, Squid can no longer see or restrict the HTTP method happening inside the encrypted traffic. So for HTTPS targets specifically, the GET/HEAD-only guarantee is **weaker** than for plaintext HTTP — domain allowlisting still holds, but a compromised/buggy agent could technically send a POST over HTTPS to an allowlisted domain without the proxy blocking it. Recorded in `docs/deferred-tasks.md` as a real gap; SSL-bump (Squid terminating and re-encrypting TLS itself) would close it but adds real setup complexity (cert generation, trusting Squid's CA inside the runtime container).
- **Verification:** After the fix, confirmed via the full three-case matrix:
  - HTTPS to an allowlisted domain → succeeded (page loaded, stub printed the title)
  - HTTPS to a non-allowlisted domain → failed (CONNECT denied)

### Issue 15: A live, already-running proxy container doesn't pick up config file edits

- **Symptom:** After fixing `squid.conf` to add the CONNECT/ssl_ports handling, the smoke test still failed with the exact same `ERR_TUNNEL_CONNECTION_FAILED` error, even though `squid -k parse` confirmed the file itself was syntactically valid and logically correct.
- **Diagnosis:** Squid reads its config once at process startup. Editing the file on disk after a container is already running has no effect on that already-running daemon — only a genuinely fresh container start (`docker rm -f` then a new `docker run`) loads the current file contents.
- **Fix:** Always `docker rm -f <container>` before restarting after any config change, rather than assuming a bind-mounted file change takes effect automatically.
- **Verification:** A guaranteed-fresh proxy container start, confirmed via its own startup timestamp in `docker logs`, resolved the issue.
- **Lesson:** When a fix "should have worked" based on the file contents but didn't change behavior, suspect a stale running process before suspecting the fix itself is wrong.

### Phase 3 — Final verification (all checks passed)

- Image builds cleanly (`agent-platform/browser-audit-runtime:1.0.0`), consistent naming with `agent-platform/proxy:1.0.0`.
- Runs as non-root (`uid=1001`).
- No direct internet route — only reaches the network via the `proxy` alias.
- HTTPS navigation to an allowlisted domain succeeds end-to-end (confirmed: page loaded, title printed).
- HTTPS navigation to a non-allowlisted domain fails (CONNECT denied at the proxy).

---

## Phase 4 — Real agent runtime implementation

### Issue 16: `workspaces/` created in the wrong directory

- **Symptom:** After setting up a test run, `workspaces/` ended up nested under `runtimes/browser-audit-runtime/` instead of at the repo root.
- **Diagnosis:** The directory was created from whatever directory happened to be the current shell location at the time (left over from earlier Docker build/smoke-test work), not the repo root.
- **Fix:** Moved it up to the repo root, sibling to `proxy/`, `runtimes/`, `monolith/`, etc., matching the documented repo layout:
  ```bash
  mv workspaces ../../workspaces
  ```
- **Verification:** `ls workspaces/test-run-001/input/` confirmed correct placement before re-running the smoke test from the repo root.
- **Lesson:** Before creating any new top-level directory (workspaces, reports, etc.), confirm current working directory with `pwd` against the documented repo layout rather than assuming.

### Issue 17: `PermissionError: [Errno 13] Permission denied` writing to `/workspace/output/events.jsonl`

- **Symptom:** The container crashed immediately on `EventWriter.__init__` trying to open `events.jsonl` for writing.
- **Diagnosis:** Classic Docker bind-mount ownership mismatch. The container runs as a non-root user (`pwuser`, uid 1001 inside the container), but the host-side `workspaces/<run-id>/output/` directory was created by the host user with default permissions that don't grant a different uid write access. Bind mounts preserve host-side ownership/permissions as-is -- the container's internal user identity doesn't get special access just because it's "in a container." This is further complicated under rootless Docker, which remaps container UIDs into a subordinate range on the host, making a simple `chown` to a specific uid unreliable.
- **Fix (manual smoke-test workaround):**
  ```bash
  chmod -R 777 workspaces/<run-id>/output
  ```
- **Open follow-up for Phase 5:** The real launcher must handle this properly when it creates each run's output directory -- either create it with permissive permissions from the start, or pass a matching `--user`/`-u` flag to `docker run` based on the host's effective uid, rather than relying on a manual `chmod` every time. Added to `docs/deferred-tasks.md`.
- **Verification:** Re-ran after `chmod`; the container could write `events.jsonl` and all subsequent artifacts without error.

### Issue 18: Confirmed `axe-playwright-python==0.1.4`'s real API before writing against it

- **Context:** Before relying on `axe.run(page)` returning an object with a `.response` dict (used in `scan_page()`), the actual installed package was inspected directly with `inspect.signature()` and `inspect.getsource()` rather than trusting an assumed API shape.
- **Finding:** Confirmed `Axe.run(self, page, context=None, options={'resultTypes': ['violations']})` returns an `AxeResults` object whose `.response` attribute is the raw axe-core result dict -- exactly matching what the runtime code expected. No code change was needed.
- **Useful side-finding:** `Axe.run()` defaults to only computing `violations` (not `passes`/`incomplete`/`inapplicable`), which keeps the raw JSON smaller and matches what `normalize_axe_results()` actually needs -- left as the default rather than widening it.
- **Lesson:** When wrapping a third-party library's API from memory/assumption, verify the actual installed version's signature and return shape directly (`inspect.signature`, `inspect.getsource`, or `help()`) before relying on it in code that will be expensive to debug later inside a container.

### Issue 19: First real end-to-end run validated against real axe-core output, not just a hand-written example

- **Context:** A real run against `https://www.example.com/` completed successfully and produced a schema-valid `report.json` with `findings: []`.
- **Verification step taken:** Rather than assuming zero findings meant "nothing happened," the raw axe-core result file (`raw/page-001-axe.json`) was inspected directly. It showed exactly 2 violations (`landmark-one-main`, `region`), both tagged only with `best-practice`-category tags and no `wcagXXX` tag at all -- confirming the normalization logic's drop-if-unmappable behavior (`normalize.py`) was correctly excluding genuinely non-WCAG-mapped findings, not silently losing real ones due to a gap in the `WCAG_TAG_TO_CRITERION` lookup table.
- **Lesson:** When a normalization/filtering step results in "nothing," verify by inspecting the raw pre-filtered data directly rather than assuming the empty result is either correct or a bug -- in this case it happened to be correct, but the only way to know for sure was to check.

### Issue 20: Failure-path test -- confirmed clean, fast failure with a useful event, not a hang

- **Test performed:** Ran the real runtime against `https://www.wikipedia.org/`, a domain deliberately not present in `allowed-domains.acl`.
- **Result:** Failed in ~1 second (proxy denies the CONNECT immediately; no 20-second navigation timeout was hit), exited with code `1`, and correctly wrote a `failed` event to `events.jsonl` with a real, useful message (`net::ERR_TUNNEL_CONNECTION_FAILED`) and `fatal: true`. Confirmed schema-valid against `event.schema.json`.
- **Follow-up improvement made:** The `failed` event's `data.error_code` originally only captured the generic Python exception class name (`"Error"` for nearly all Playwright failures, regardless of actual cause). Added `extract_error_code()`, which pulls the specific `net::ERR_*` code out of the exception message when present (e.g. `ERR_TUNNEL_CONNECTION_FAILED`, `ERR_NAME_NOT_RESOLVED`), falling back to the exception class name only when no such code is found (e.g. non-network failures like a schema/file-I/O error).
- **Verification:** Confirmed via unit test against the real captured error message before rebuilding, then confirmed again against the actual container's `events.jsonl` output after rebuilding.

### Issue 21: A code fix didn't take effect after rebuilding -- stale Docker build cache

- **Symptom:** After adding `extract_error_code()` and rebuilding, two consecutive real runs still showed `"error_code": "Error"` in `events.jsonl`, as if the fix had never been applied.
- **Diagnosis:** `grep` confirmed the new code *was* present in the local `agent_runtime/__main__.py` on disk -- ruling out the "file never actually got updated" cause seen earlier (Issue 10). That left Docker's build layer cache: a plain `docker build` can reuse a cached `COPY agent_runtime/ /app/agent_runtime/` layer if Docker's cache invalidation heuristics didn't detect the file content change as significant, or if an intermediate layer was cached from a near-identical prior build.
- **Fix:**
  ```bash
  docker build --no-cache -t agent-platform/browser-audit-runtime:1.0.0 .
  ```
- **Verification:** Re-ran the failure-path test after the no-cache rebuild; the new run's event correctly showed `"error_code": "ERR_TUNNEL_CONNECTION_FAILED"`.
- **Lesson:** When a code change that's confirmed present on disk doesn't seem to take effect after a rebuild, suspect Docker's build cache before suspecting the code itself -- `--no-cache` is a cheap way to rule it out, even though it costs a full rebuild.

### Phase 4 — Final verification (all checks passed)

- Real axe-core scan + Playwright navigation, not a stub.
- Findings normalized correctly per `wcag-output.schema.json`, validated against actual (not hand-written) axe-core output.
- Full event lifecycle correct on both success (`started` → `step`s → `artifact-written` → `completed`) and failure (`started` → `step` → `failed`).
- Exit codes correct (0 success, 1 failure).
- Failure path is fast (no unnecessary timeout wait) and produces a specific, useful `error_code`.

---

## Phase 5 — Monolith (registry, launcher, artifacts, run history)

### Issue 22: Resource usage reported as `0.0` instead of reflecting "never measured"

- **Symptom:** A real, successfully completed run's history record showed `peak_cpu_percent: 0.0` and `peak_memory_mb: 0.0` — implausible for a run that launched a full Chromium instance.
- **Diagnosis:** The original polling loop called `docker stats --no-stream` on a fixed 2-second interval. A short-lived agent run (the whole WCAG scan completes in just a few seconds) could exit before the first 2-second sleep ever elapsed, meaning `docker stats` was never successfully called against a still-running container. `_poll_stats()` returns `None` on failure, and the original code's `peak_cpu = max(peak_cpu, ...)` only updated on a successful sample -- so the initial `0.0` silently passed straight through to the run record with no indication that nothing had actually been measured.
- **Fix:** Two changes in `launcher.py`:
  1. Poll on a much tighter interval (0.25s) for the first several iterations, so short-lived containers still get at least one real sample before exiting.
  2. Track `samples_captured`; initialize `peak_cpu`/`peak_mem_mb` to `None` rather than `0.0`, and only convert to a real number once an actual sample lands. If zero samples were ever captured, the run record now correctly stores `null` and a warning is printed, rather than silently asserting "measured and found to be zero."
- **Verification:** Re-ran the same single-page audit through the full `/runs` API; the run record showed real, plausible nonzero values (`peak_cpu_percent: 100.31`, `peak_memory_mb: 133.1`).
- **Lesson:** When a measurement defaults to `0`, treat that as a smell on any short-lived process -- `0` and "never measured" are different facts, and conflating them produces a number that looks valid but is actually nothing.

### Issue 23: `agents/wcag22-auditor/agent.yaml` was empty after copying

- **Symptom:** Registry startup log showed `WARNING: skipping ... agent.yaml: None is not of type 'object'`.
- **Diagnosis:** `None is not of type 'object'` means `yaml.safe_load()` returned `None`, which happens on a completely empty file -- same failure signature as the empty task-envelope fixture earlier (Issue 4). The file had been copied via VS Code's file explorer/editor rather than a shell `cp`, and ended up empty.
- **Fix:** Re-copied the source file's actual content into the destination and confirmed non-empty before restarting the monolith.
- **Verification:** `[registry] loaded agent 'wcag22-auditor' from ...` appeared on the next startup.
- **Lesson:** This empty-file failure mode has now appeared from at least two different copy mechanisms (shell `cp`, VS Code copy/paste). Whenever a copy "looks like it worked" but something downstream treats the file as missing/null, check the byte count (`wc -c <file>` or `ls -la`) before assuming the copy itself is the problem.

### Issue 24: Agent container failed to launch — wrong `image:` value in `agent.yaml`

- **Symptom:** `POST /runs` returned `500`, with `error_message`: `Unable to find image 'local-masteragent/browser-audit-runtime:1.0.0' locally ... pull access denied`.
- **Diagnosis:** `agents/wcag22-auditor/agent.yaml`'s `runtime.image` field held a stale/incorrect value (`local-masteragent/...`) that didn't match the actual built image tag (`agent-platform/browser-audit-runtime:1.0.0`) established back in Phase 3. The launcher correctly used whatever was in the manifest -- the manifest itself was wrong.
- **Fix:** Corrected the `image:` field in `agent.yaml` to match the real, confirmed-built image tag.
- **Verification:** `grep "image:" agents/wcag22-auditor/agent.yaml` confirmed the corrected value, cross-checked against `docker images | grep browser-audit-runtime`; the next `POST /runs` launched the correct image successfully.
- **Lesson:** Any time an image name appears in more than one place (a Dockerfile build command, an agent manifest, documentation), a naming drift between them is just a matter of time -- worth a periodic grep across the repo for the image name string to catch drift early, rather than only discovering it when a run fails.

### Issue 25: `ERROR: Error loading ASGI app. Attribute "app" not found in module "app.main"`

- **Symptom:** `uvicorn app.main:app` failed with this error on first attempt to start the monolith.
- **Diagnosis:** `main.py` uses relative imports (`from . import db, launcher, artifacts`), which requires `monolith/app/` to be a proper Python package -- i.e. it needs an `__init__.py`. Without one, the import of `app.main` fails partway through, and uvicorn's error reporting surfaces this as a confusing "attribute not found" rather than the real underlying ImportError.
- **Fix:** Added `monolith/app/__init__.py` (empty file is sufficient).
- **Verification:** `uvicorn app.main:app --reload --port 8000` started cleanly, with `[registry] loaded agent ...` and `Application startup complete.` both appearing.
- **Lesson:** When uvicorn reports a vague "attribute not found in module" error, run `python3 -c "import app.main"` directly first -- it surfaces the real traceback that uvicorn's error path hides.

### Non-issue: requesting an agent's own target domain as a "denied domain" test doesn't apply to this agent

- **Context:** Attempted to verify the proxy denial path through the live API by requesting a run against `wikipedia.org`, expecting it to be denied since it wasn't part of any prior static allowlist.
- **What actually happened:** The run succeeded. This is correct, not a bug -- `agent.yaml`'s `permissions.network.allow_dynamic_target: true` means the launcher always adds whatever URL is requested as a run's *own target* into that run's ACL, since an auditor's entire purpose is reaching whatever site the user asks it to audit. The earlier Phase 3/4 denial tests worked because they pointed the agent at a domain that was NOT the run's target (to prove unrelated domains stay blocked) -- requesting a domain AS the target is a fundamentally different, and correctly-allowed, case.
- **Real gap this surfaces:** There is currently no code path to test "the agent's target is domain A, but it attempts to reach unrelated domain B" through the live API for this specific agent, since the WCAG auditor only ever navigates to its one `entry_url` and never follows external links. This remains a non-issue for the WCAG auditor as designed, but is worth keeping in mind for any future agent type that does follow links to other domains (a true crawler, a research agent, etc.) -- that's where a real "is the dynamic target genuinely scoped correctly" test would matter.

### Issue 26: `docker stats` resolution limitation for very short-lived containers (distinct from Issue 22 — known limitation, not a bug to fix further)

- **Context:** After fixing Issue 22 (zero samples ever captured → incorrectly reported as `0.0`), a separate run still showed `peak_cpu_percent: 0.0` / `peak_memory_mb: 0.0` with no `WARNING` log line -- meaning a real sample WAS captured this time, just measuring near-zero.
- **Diagnosis:** The agent container's total lifetime for a single-page audit is roughly 1-2 seconds. `docker stats --no-stream` needs an internal ~1-second sampling window to compute a CPU% delta from cgroup counters. For a container that lives only slightly longer than that window, the one or two samples the launcher manages to catch can easily land right at container startup (before Chromium/Playwright has done any real work) rather than during peak usage -- by the time meaningful load would show up, the container has often already exited.
- **This is NOT the same as Issue 22.** Issue 22 was "zero samples ever captured, incorrectly defaulted to 0.0." This is "a real sample was captured, and it happened to measure a genuinely low-load instant because the container's total runtime is barely longer than docker stats' own measurement resolution." The `samples_captured` / `null`-on-zero-samples fix from Issue 22 correctly distinguishes these two cases (no `WARNING` line means a sample was captured) -- this is a separate, inherent limitation of polling-based external measurement against very short-lived processes, not a bug in the polling loop itself.
- **Decision:** Accepted as a known limitation for v1 rather than over-engineering further (e.g. higher-frequency polling than 0.25s, or switching to a continuous `docker stats` stream parsed in a background thread) -- the WCAG auditor's runs are inherently this fast, and peak CPU/memory accuracy at this timescale isn't critical to the platform's core purpose. Documented here rather than silently left as an unexplained "sometimes 0.0, sometimes real numbers" inconsistency.
- **If this becomes important later:** A more accurate approach would stream `docker stats` continuously (without `--no-stream`) in a background thread/process for the container's full lifetime, sampling at the container's own cgroup update frequency rather than polling from outside on a fixed interval -- this would catch fast spikes that the current poll-based approach can miss entirely. Worth revisiting if an agent type is ever added where accurate resource measurement matters more (e.g. for capacity planning or cost attribution), but not necessary for the WCAG auditor.

### Phase 5 — Final verification (all checks passed)

Confirmed via a single `POST /runs` call replacing every manual step from Phases 2-4:
- Registry loaded and validated the real agent manifest.
- Launcher generated the per-run ACL, force-restarted the proxy, launched the hardened agent container, and correctly captured real (nonzero) peak CPU/memory.
- Artifacts collected into `reports/<run_id>/`.
- Phase 7's schema evaluator ran automatically and correctly marked the result `schema_valid: 1`.
- Run history correctly recorded the full lifecycle (`queued` → `running` → `completed`/`failed`).
- Approve endpoint correctly updates `approval_status`.

---

## Phase 7 — Schema evaluator

### Issue 27: `_evaluate_schema` stored the entire schema dump as the error message

- **Symptom:** While testing the evaluator's negative-case behavior in isolation, `str(e)` on a `jsonschema.ValidationError` produced an enormous block of text -- the entire schema definition reprinted alongside the actual error -- rather than a short, useful message.
- **Diagnosis:** `jsonschema.exceptions.ValidationError.__str__()` includes the full schema and instance for debugging context by default. Storing `str(e)` directly into `schema_errors` would bloat the database and make any future dashboard display unusable for this field.
- **Fix:** Switched to `jsonschema.Draft7Validator(schema).iter_errors(report)` and used each error's `.message` attribute (the short, human-readable part, e.g. `"'summary' is a required property"`) instead of `str(e)`. This also fixed a second, related problem: the original `jsonschema.validate()` call only ever surfaces the *first* validation failure it hits -- switching to `iter_errors()` collects every failure in the report in one pass, which is much more useful for actually fixing a broken report with multiple problems at once.
- **Verification:** Tested against a report missing the required `summary` field AND with an invalid `wcag_level` enum value simultaneously -- confirmed both errors are now reported together, each as a short, readable, path-prefixed message (e.g. `"wcag_level: 'INVALID_LEVEL' is not one of ['A', 'AA', 'AAA']"`), and a genuinely valid report still produces zero errors (no false positives introduced).

### Negative-case proof: `monolith/scripts/reevaluate.py --inject-malformed`

- **Purpose:** Phase 7's "done when" criterion requires proving the evaluator correctly rejects malformed output -- but the real agent never produces invalid output on its own, so there was no natural way to exercise this path against the live system.
- **Approach:** Built a standalone script that temporarily backs up a real run's `report.json`, injects two deliberate breaks (removes the required `summary` field, sets `wcag_level` to an invalid value), re-runs the same evaluation logic used by the live monolith, confirms the run record is correctly marked `schema_valid: 0` with both real error messages stored, then restores the original file and re-evaluates again to confirm a clean return to `schema_valid: 1`.
- **Verification (against a real run in the actual repo, not just the schema in isolation):**
  ```
  [runsd2l4yjugmgu] INVALID (2 error(s)):
    - (root): 'summary' is a required property
    - wcag_level: 'INVALID_LEVEL' is not one of ['A', 'AA', 'AAA']
  [runsd2l4yjugmgu] DB state after injection: schema_valid=0
  ...
  [runsd2l4yjugmgu] DB state after restore: schema_valid=1
  ```
  Confirmed via the live API afterward that the run record was fully and correctly restored, including `approval_status: "approved"` (set earlier in Phase 5 testing) being left untouched throughout the injection/restore cycle -- proving the script only modified what it was meant to.
- **Reusability:** The same script also supports `--all` (re-evaluate every run in `reports/`, useful if `wcag-output.schema.json` is ever revised and old runs need re-checking against the new version) and `--dry-run` (evaluate without writing to the DB).

### Phase 7 — Final verification (all checks passed)

- Multiple simultaneous validation errors are all captured in one pass, not just the first.
- Error messages are short and human-readable, not a full schema dump.
- Negative case proven against a real run in the live system: malformed report correctly flagged invalid with accurate error messages, then correctly restored to valid with no side effects on unrelated fields (`approval_status`).

---

## Phase 8 — Minimal dashboard

### Note: first clean end-to-end pass, no fixes required

- **Context:** Built `dashboard/index.html` (single static page, no separate dashboard-api service) plus the two pieces `main.py` needed to support it: CORS middleware (the dashboard is a `file://` page, a different origin than `localhost:8000`) and a static file mount serving `reports/` directly (for `report.json` and screenshots).
- **Result:** Unlike every other phase so far, this one worked correctly on the very first real test against the live system -- no naming mismatches, no stale files, no permission errors. Full sequence confirmed via the uvicorn access log:
  ```
  POST /runs                                      -> 200  (form submission)
  GET  /runs (repeated, ~2s interval)              -> 200  (list polling)
  GET  /runs/{run_id}                              -> 200  (detail view)
  GET  /reports/{run_id}/report.json               -> 200  (findings rendered)
  GET  /reports/{run_id}/screenshots/page-001.png  -> 200  (screenshot rendered)
  POST /runs/{run_id}/approve                      -> 200  (approve button)
  GET  /reports/{run_id}/report.json               -> 304  (browser cache correctly kicked in on re-fetch after approve)
  ```
- **Why this one likely went smoothly where others didn't:** The CORS and static-mount additions to `main.py` were verified in isolation first (via `fastapi.testclient.TestClient`, confirming `/reports/.../report.json` and a screenshot both returned `200` before ever touching the browser), and the dashboard itself has no dependency on Docker, file paths on the host filesystem, or container lifecycle timing -- the categories of issue that caused nearly every problem in Phases 2-6. Worth noting as a pattern: testing the parts of a change that don't require the full Docker stack, in isolation, before the full integration test, continues to pay off.

### Phase 8 — Final verification (all checks passed)

- Form submission starts a real run end-to-end through the browser.
- Run list auto-updates via polling without manual refresh.
- Detail view correctly renders findings, WCAG criteria, and screenshots from the real report.
- Approve/reject buttons correctly update run history and reflect the change immediately in the UI.

---

## Template for new entries

```
### Issue N: <short description>

- **Symptom:** <what you observed>
- **Diagnosis:** <commands run, what they revealed>
- **Fix:** <exact commands/steps that resolved it>
- **Verification:** <how you confirmed it actually worked>
```