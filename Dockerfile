# PyQxel images: the API, serving the web frontend at /. Two targets share every layer up
# to the R install:
#
#   docker build --target app   -t pyqxel .     # without R; GARCH endpoints return 503
#   docker build --target app-r -t pyqxel-r .   # with R and rugarch for GARCH
#
# Both serve on $PORT (default 8000) as an unprivileged user, and migrate the database at
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
RUN npm run build

# ── Python dependencies without rpy2 ──
FROM base AS deps
RUN python -m venv /opt/venv
COPY requirements.txt .
RUN grep -v '^rpy2' requirements.txt > /tmp/requirements-no-r.txt \
    && pip install -r /tmp/requirements-no-r.txt

# ── R with the Debian-packaged dependencies of rugarch ──
FROM base AS r-runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        r-base-core r-cran-chron r-cran-fnn r-cran-fracdiff r-cran-future.apply \
        r-cran-kernlab r-cran-ks r-cran-mclust r-cran-multicool r-cran-mvtnorm \
        r-cran-nloptr r-cran-numderiv r-cran-pracma r-cran-rcpparmadillo r-cran-truncnorm \
        r-cran-xts r-cran-zoo \
    && rm -rf /var/lib/apt/lists/*
ENV R_HOME=/usr/lib/R

# ── Compilers: rugarch and the dependencies Debian lacks, then rpy2 ──
FROM r-runtime AS r-build
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential gfortran r-base-dev \
    && rm -rf /var/lib/apt/lists/*
# rugarch needs newer Rcpp and Rsolnp than Debian ships. Packages land in
# /usr/local/lib/R/site-library, which the runtime stage copies.
ARG CRAN_REPO=https://cloud.r-project.org
RUN Rscript -e "options(repos = c(CRAN = '${CRAN_REPO}')); \
        install.packages(c('Rcpp', 'Rsolnp', 'rugarch'), Ncpus = parallel::detectCores()); \
        library(rugarch)"
COPY --from=deps /opt/venv /opt/venv
COPY requirements.txt .
RUN pip install "$(grep '^rpy2' requirements.txt)"

# ── Runtime image without R ──
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

# ── Runtime image with R ──
FROM r-runtime AS app-r
COPY --from=r-build /usr/local/lib/R/site-library /usr/local/lib/R/site-library
COPY --from=r-build /opt/venv /opt/venv
COPY alembic.ini .
COPY app ./app
COPY --from=web /web/dist ./web/dist
RUN mkdir -p data_cache && chown pyqxel:pyqxel data_cache
USER pyqxel
ENV PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health', timeout=4)"]
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port \"$PORT\" --proxy-headers --forwarded-allow-ips '*'"]
