# syntax=docker/dockerfile:1.12
# Targets: dev (reload, dev deps), test (CI), prod (slim, non-root). One image serves api, worker, beat, seed.
ARG PYTHON=3.14
FROM python:${PYTHON}-slim-trixie AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client tini \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system --gid 10001 astro && useradd --system --uid 10001 --gid astro --home /app astro
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv
WORKDIR /app

FROM base AS deps
COPY pyproject.toml uv.lock* ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-install-project --extra sky --no-dev

FROM deps AS dev
RUN --mount=type=cache,target=/root/.cache/uv uv sync --no-install-project --extra sky
COPY . .
ENV PATH=/app/.venv/bin:$PATH PYTHONPATH=/app/src ASTRO_ENV=development
USER astro
CMD ["uvicorn", "astro_api.main:app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "src"]

FROM dev AS test
CMD ["pytest", "-q"]

FROM deps AS build
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --extra sky --no-dev

FROM base AS prod
COPY --from=build --chown=astro:astro /app /app
ENV PATH=/app/.venv/bin:$PATH PYTHONPATH=/app/src ASTRO_ENV=production
USER astro
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health/live', timeout=2).status == 200 else 1)"
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "astro_api.main:app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]
