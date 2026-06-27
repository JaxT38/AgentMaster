# runtimes/browser-audit-runtime/Dockerfile
#
# Multi-arch image (linux/amd64, linux/arm64) for the WCAG 2.2 auditor.
# All dependency versions are pinned explicitly — no floating "latest" tags.
#
# Build with buildx for both architectures:
#   docker buildx build --platform linux/amd64,linux/arm64 \
#     -t MasterAgent/browser-audit-runtime:1.0.0 \
#     --load .
#
# (--load only works for a single platform at a time on most setups;
#  use --push to a registry, or build each platform separately with
#  --load, if you need both images available locally at once.)

FROM mcr.microsoft.com/playwright/python:v1.55.0-noble

# ---- Pinned dependencies ----
# IMPORTANT: the playwright pip package version MUST match the base
# image's Playwright version (v1.55.0 here), or Playwright will be
# unable to locate the browser executables baked into this image.
RUN pip install --no-cache-dir \
    playwright==1.55.0 \
    axe-playwright-python==0.1.4 \
    jsonschema==4.23.0

# ---- Non-root user ----
# The Playwright base image creates a 'pwuser' user already; use it
# explicitly rather than relying on the image default, so this is
# documented and doesn't silently change on a base-image update.
ARG RUNTIME_UID=1000
RUN id pwuser >/dev/null 2>&1 || useradd -m -u ${RUNTIME_UID} pwuser

WORKDIR /app
COPY agent_runtime/ /app/agent_runtime/

# Workspace mount points — populated by the launcher per run.
# /workspace/input is mounted read-only, /workspace/output read-write.
RUN mkdir -p /workspace/input /workspace/output && \
    chown -R pwuser:pwuser /workspace /app

# ---- Forced egress through the proxy ----
# Default alias assumes the launcher attaches this container to a
# Docker network where the proxy service is reachable as "proxy".
# The launcher may override these at `docker run` time with --env if
# a different alias/port is used for a given run.
ENV HTTP_PROXY=http://proxy:3128 \
    HTTPS_PROXY=http://proxy:3128 \
    NO_PROXY=localhost,127.0.0.1

USER pwuser

ENTRYPOINT ["python", "-m", "agent_runtime"]