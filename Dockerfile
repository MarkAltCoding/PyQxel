# The PyQxel image: the API, serving the web frontend at /.
#
#   docker build -t pyqxel .
#
# It serves on $PORT (default 8000) as an unprivileged user, and migrates the database at
# startup. Secrets come from the environment at run time; none are baked into the image.

ARG PYTHON_VERSION=3.14

# ── Shared base: Python and an unprivileged user ──
FROM python:${PYTHON_VERSION}-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/opt/venv/bin:$PATH"
RUN useradd --create-home --uid 1000 pyqxel
WORKDIR /app

# ── The web frontend, built to static files the API serves at / ──
FROM node:24-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
# Pyodide's wheels are committed in web/vendor, so the build fetches nothing from a CDN.
ENV PYODIDE_OFFLINE=1
RUN npm run build

# ── Python dependencies ──
FROM base AS deps
RUN python -m venv /opt/venv
COPY requirements.txt .
RUN pip install -r requirements.txt

# ── Runtime image ──
FROM base AS app
COPY --from=deps /opt/venv /opt/venv
COPY alembic.ini .
COPY app ./app
COPY --from=web /web/dist ./web/dist
RUN mkdir -p data_cache && chown pyqxel:pyqxel data_cache
USER pyqxel
ENV PORT=8000
EXPOSE 8000
# Liveness only: the container is unhealthy when the process stops answering, not when
# the database does, so a database outage does not restart every app container.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health', timeout=4)"]
# WEB_CONCURRENCY sets the number of uvicorn workers (default 1). --proxy-headers takes the
# client address and scheme from the hosting platform's proxy.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port \"$PORT\" --proxy-headers --forwarded-allow-ips '*'"]

