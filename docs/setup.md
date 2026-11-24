# Local Setup and Deployment

## Running PyQxel locally

### Prerequisites

Python 3.14, Node.js 24, and, for GARCH volatility, R 4.3 with `rugarch`. Docker is optional.

### Installation

```bash
git clone https://github.com/MarkAltCoding/PyQxel.git && cd PyQxel
python3.14 -m venv venv && venv/bin/pip install -r requirements.txt -r requirements-dev.txt
Rscript -e 'install.packages("rugarch", repos = "https://cloud.r-project.org")'
cp .env.example .env
```

Settings in `.env` (all optional for a first run):

* `SEC_USER_AGENT`: your name and email, which the [SEC asks](https://www.sec.gov/os/accessing-edgar-data) of automated clients; it turns on financial statements and filing text from EDGAR.
* `YFINANCE_FALLBACK=true`: fills gaps in free data plans with yfinance during development. It is unlicensed, so leave it off where others use the server.
* `CREDENTIALS_ENCRYPTION_KEY`: encrypts the keys of users who turn on the nightly refresh or scheduled picks (`python -m app.jobs.users generate-encryption-key`); without it, neither can be turned on.
* `SCREENER_FMP_API_KEY`: a licensed FMP key for the shared screener every account sees.
* `DATABASE_URL`: Postgres (`postgresql+asyncpg://localhost/pyqxel`); SQLite in `data_cache/` otherwise. Migrations run at startup.
* `REDIS_URL`: optional; it only shares the in-memory caches and rate limits among several instances.
* Provider keys in `.env` (`ANTHROPIC_API_KEY`, `FINNHUB_API_KEY`, ...) are read only by tests marked `live` or `paid`: accounts always send their own.

### Run it

```bash
venv/bin/python -m app.jobs.users create you@example.com   # prints an API key to sign in with
YFINANCE_FALLBACK=true venv/bin/uvicorn app.main:app --port 8000
cd web && npm ci && npm run dev                              # http://localhost:5173
```

The API's interactive docs are at `http://localhost:8000/docs`; every `/api/v1` request takes `Authorization: Bearer <key>`. AI reports stream as Server-Sent Events, and live quotes come over a WebSocket at `/api/v1/ws/quotes`.

### Checks

`scripts/check.sh` runs what CI runs: formatting, lint, types, and the Python and web tests, after regenerating the API schema and the frontend's types. `scripts/check.sh e2e` adds the browser tests. `pytest --live` also calls the real providers, and `--paid` makes two billed Claude requests.

## Docker and Deployment

### Docker Compose

```bash
docker compose up --build    # the API and web app at http://localhost:8000, with Postgres and Redis
```

`PYQXEL_TARGET=app-r` builds the image with R. Both images run unprivileged and hold no secrets.

### Health checks

`GET /health` answers whenever the process does. `GET /health/ready` also reports the database, Redis, and R, and returns 503 only when the database cannot serve.

### Google Cloud Run

`deploy/cloud-run.sh` deploys to [Cloud Run](https://cloud.google.com/run), which bills only while requests run, with Postgres on [Neon](https://neon.com)'s free plan; a personal deployment fits in the free tiers. Secrets live in Google Secret Manager.

One-time setup:

1. Create a Google Cloud project with billing and a small budget alert, install the [gcloud CLI](https://cloud.google.com/sdk/docs/install), and `gcloud auth login`.
2. Store the secrets (each command reads the value you paste, then Ctrl-D):
   ```bash
   gcloud secrets create pyqxel-database-url --data-file=-       # Neon's pooled connection string
   gcloud secrets create pyqxel-screener-fmp-key --data-file=-   # optional: the shared screener's FMP key
   venv/bin/python -m app.jobs.users generate-encryption-key | \
     gcloud secrets create pyqxel-credentials-key --data-file=-  # for nightly refreshes and scheduled picks
   gcloud secrets create pyqxel-redis-url --data-file=-          # optional: an Upstash rediss:// URL
   ```
3. Optionally, [bill for nightly refreshes](accounts-and-billing.md#paying-for-the-nightly-refresh): with a Stripe restricted key, `STRIPE_SECRET_KEY=rk_... PUBLIC_URL=https://YOUR_URL venv/bin/python -m app.jobs.billing setup --webhook-url https://YOUR_URL/webhooks/stripe` makes the catalog and the webhook; store the key and the printed signing secret as `pyqxel-stripe-secret-key` and `pyqxel-stripe-webhook-secret`.

Deploy, and redeploy after each change:

```bash
PROJECT_ID=YOUR_PROJECT_ID SEC_USER_AGENT="PyQxel you@example.com" deploy/cloud-run.sh
```

It deploys the service (1 CPU, 2 GiB, at most 2 instances; a revision takes traffic only once it is ready), the shared screener's weekday job, the nightly job that runs users' own refreshes when one is due, and a scheduler that keeps an instance warm. To roll back, send traffic to an earlier revision with `gcloud run services update-traffic`; a rollback does not undo a migration.

#### Signing in with GitHub and Google

Register an OAuth client with each, with the callback `https://YOUR_URL/api/v1/auth/github/callback` (or `/google/callback`) for each address Cloud Run serves the service at; Google's consent screen must be published. Store each client secret as `pyqxel-github-client-secret` or `pyqxel-google-client-secret`, and deploy once with `GITHUB_CLIENT_ID=... GOOGLE_CLIENT_ID=...`. The apps sign in through the system browser with the same clients.

Sign in with Apple is built but off. It needs a paid Apple Developer account: a Services ID with the return URL `https://YOUR_URL/api/v1/auth/apple/callback` and a Sign in with Apple key (`.p8`). Set `APPLE_SIGN_IN_ENABLED=true`, `APPLE_CLIENT_ID` (the Services ID), `APPLE_TEAM_ID`, `APPLE_KEY_ID`, and `APPLE_PRIVATE_KEY` (the key's text).

#### Signing in with an email and password

Accounts made with an email are proven by an emailed link, which is also where the password is chosen. Email goes out through Gmail with an app password:

```bash
gcloud secrets create pyqxel-smtp-password --data-file=-
SMTP_HOST=smtp.gmail.com SMTP_USERNAME=you@gmail.com EMAIL_FROM='PyQxel <you@gmail.com>' \
  PUBLIC_URL=https://YOUR_URL deploy/cloud-run.sh
```

Locally, `EMAIL_TO_CONSOLE=true` writes the emails to the server's log instead.
