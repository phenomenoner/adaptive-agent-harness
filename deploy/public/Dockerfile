# syntax=docker/dockerfile:1

ARG PYTHON_IMAGE=python:3.13.7-slim-bookworm
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.32

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS builder

COPY --from=uv /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    UV_NO_DEV=1 \
    UV_NO_EDITABLE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/aar \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /build
COPY pyproject.toml uv.lock README.md LICENSE ./

RUN uv sync --locked --no-dev --no-install-project --no-editable

COPY src ./src
COPY skills ./skills
COPY profiles ./profiles
COPY schemas ./schemas
COPY tests/fixtures ./tests/fixtures
COPY benchmarks ./benchmarks
COPY docs/CODEX-INSTALL.md ./docs/CODEX-INSTALL.md
COPY integration ./integration

RUN uv sync --locked --no-dev --no-editable

FROM ${PYTHON_IMAGE} AS runtime

ENV AAR_PUBLIC_BIND_HOST=0.0.0.0 \
    AAR_PUBLIC_BIND_PORT=8000 \
    AAR_PUBLIC_DATA_ROOT=/var/lib/aar \
    PATH=/opt/aar/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system --gid 10001 aar \
    && useradd --system --uid 10001 --gid aar --home-dir /nonexistent --shell /usr/sbin/nologin aar \
    && install -d -o aar -g aar -m 0700 /var/lib/aar

COPY --from=builder /opt/aar /opt/aar

USER 10001:10001
EXPOSE 8000
VOLUME ["/var/lib/aar"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).read()"]

ENTRYPOINT ["aar-mcp-public"]
