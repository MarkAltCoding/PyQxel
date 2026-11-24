# Local Setup and Deployment

## Running PyQxel Locally

### Prerequisites

1. **Python 3.14+** installed on your system.
2. **R 4.3+** installed (required for advanced statistical and econometric modules).
3. **Claude Code** CLI configured locally (`npm install -g @anthropic-ai/claude-code`).
4. **Node.js 24+** to build or develop the web frontend.
5. **Docker** (optional) to run the API with Postgres and Redis in containers instead; see [Docker and Deployment](#docker-and-deployment).

### Installation

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/MarkAltCoding/PyQxel.git
   cd PyQxel
   ```

2. **Create a Virtual Environment and Install Dependencies**:
   ```bash
   python3.14 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt -r requirements-dev.txt
   ```
   `requirements-dev.txt` adds the test, type-checking, and lint tools; servers need only `requirements.txt`.
   Then install the R packages the statistical models use:
   ```bash
   Rscript -e 'install.packages("rugarch", repos = "https://cloud.r-project.org")'
   ```

3. **Configure Environment Variables**:
   ```bash
   cp .env.example .env
   ```
   Then fill in `.env`:
   * `ANTHROPIC_API_KEY`, `FINANCIAL_DATA_API_KEY`, `ALPHA_VANTAGE_API_KEY` and `FINNHUB_API_KEY` — optional; your own [Anthropic](https://console.anthropic.com/), [Financial Modeling Prep](https://site.financialmodelingprep.com/developer/docs), [Alpha Vantage](https://www.alphavantage.co/support/#api-key) and [Finnhub](https://finnhub.io/register) keys, read only by tests marked `live` or `paid`. The server never uses them to serve requests: every account pays for its own usage with the keys its requests carry.
   * `SCREENER_FMP_API_KEY` — the FMP key the [stock screener](models.md#stock-screener)'s nightly refresh runs on. Every account sees the universe it builds, so this is the server's key, not an account's, and its plan must allow showing FMP data to your users (FMP's individual plans are for personal use; ask FMP about a data display license). Set `SCREENER_FMP_REQUESTS_PER_MINUTE` to the plan's rate limit (default 300).
   * `CREDENTIALS_ENCRYPTION_KEY` — optional; encrypts the market data keys of users who opt in to the [nightly refresh of their own screener](models.md#each-users-own-screener), the only provider keys PyQxel stores. Generate one with `python -m app.jobs.users generate-encryption-key`, and keep it outside the database: stored keys cannot be read without it. Without it, no one can opt in.
   * `YFINANCE_FALLBACK` — `true` for development: use `yfinance` for every account when a request carries no FMP key, or FMP fails, or the account's plan does not cover a request (FMP's free plan excludes ETFs, indices, foreign listings, and intraday bars), and for the screener when `SCREENER_FMP_API_KEY` is unset, with Nasdaq's public feed for its sectors. These sources are unlicensed, so leave it `false` on a deployment others use.
   * `SEC_USER_AGENT` — optional; your name and contact email (e.g. `PyQxel jane@example.com`), which the [SEC requires](https://www.sec.gov/os/accessing-edgar-data) of automated clients. When set, financial statements come straight from EDGAR, and AI analyses also read the Risk Factors and MD&A sections of the company's latest 10-K and 10-Q. Without it, accounts with a Finnhub key still get financial statements, relayed by Finnhub, but no filing text.
   * `R_HOME` — the output of `R RHOME` (e.g. `/Library/Frameworks/R.framework/Resources` on macOS).

4. **Create Your Account**:
   ```bash
   python -m app.jobs.users create you@example.com --claim-unowned
   export PYQXEL_API_KEY=pq_...   # the key it prints, shown only once
   ```
   `--claim-unowned` gives it any results stored before accounts existed. Create other people's accounts without it, and send them their key; they add their own provider keys (see [Accounts](accounts-and-billing.md#accounts-and-billing)).

5. **Run the API Server**:
   ```bash
   uvicorn app.main:app --reload
   ```
   The API is served at `http://127.0.0.1:8000`, with interactive docs at `http://127.0.0.1:8000/docs`. Check it with `curl http://127.0.0.1:8000/health`, or `/health/ready` to also check the database and Redis. Every `/api/v1` request needs your API key: `curl -H "Authorization: Bearer $PYQXEL_API_KEY" http://127.0.0.1:8000/api/v1/me`.

   Two endpoints stream:
   * `POST /api/v1/stocks/{symbol}/analysis/stream` sends the AI analysis as Server-Sent Events while Claude writes it: `context`, then `thinking` and `report` fragments, then the final `result` (or an `error`). Try it with `curl -N -X POST -H "Authorization: Bearer $PYQXEL_API_KEY" http://127.0.0.1:8000/api/v1/stocks/AAPL/analysis/stream`.
   * `ws://127.0.0.1:8000/api/v1/ws/quotes?symbols=AAPL,MSFT` streams live quotes. Send the key in the `Authorization` header, or from a browser, which cannot set WebSocket headers, as a subprotocol: `new WebSocket(url, ["pyqxel", key])`. Keys in the URL are refused, since hosting platforms record URLs in their request logs. Change what it follows by sending `{"action": "subscribe", "symbols": ["TSLA"]}` or `{"action": "unsubscribe", ...}`. Quotes are polled every 15 seconds by default (`interval`, 5 to 300 seconds), and only new or changed quotes are sent.

   Backtests (`POST /api/v1/stocks/{symbol}/backtest`) are stored and returned with an `id`. List them with `GET /api/v1/backtests` (filter by `symbol` or `strategy`, page with `limit` and `offset`), fetch one in full with `GET /api/v1/backtests/{id}`, and remove it with `DELETE /api/v1/backtests/{id}`. They go to SQLite at `data_cache/pyqxel.db` by default; to use Postgres, create a database (`createdb pyqxel`) and set `DATABASE_URL=postgresql+asyncpg://localhost/pyqxel`, adding a user and password if your server needs them. If the database is unreachable, backtests still run and are returned with a null `id`.

   The schema is managed with [Alembic](https://alembic.sqlalchemy.org/) migrations in `app/db/migrations/`, applied automatically at startup. After changing a table in `app/db/tables.py`, generate a migration with `alembic revision --autogenerate -m "describe the change"`, review it, and commit it; `alembic upgrade head` applies it by hand. The tests fail if the models and migrations disagree.

   AI analyses are stored in the same database, so you only pay for a report once. For 24 hours (`ANALYSIS_CACHE_TTL_SECONDS`), your request with the same symbol, `kind`, `period`, and `include_filings`, under the same model and effort, gets the stored report back without fetching data or calling Claude. Identical requests that arrive at the same time are written once. Reused reports have `"cached": true` and keep their original `generated_at`; send `"refresh": true` to pay for a new one. Every report stays readable for free: list them with `GET /api/v1/analyses` (filter by `symbol` or `kind`) and fetch one with `GET /api/v1/analyses/{id}`. Each analysis also gives Claude the stock's Carhart factor exposures and its fundamentals and valuation (see below), so reports can say how much of its risk is market, size, value, or momentum, and weigh the price against the company's earnings and cash flow.

   Every lookup is cached in the process's memory (192 MB at most, the least recently used leaving first): ticker info and price history for 5 minutes (1 minute for intraday bars), exchange rates, factor data, and normalized financials. Rate limits and Finnhub's per-minute pacing are counted in memory too. Set `REDIS_URL` (e.g. `redis://localhost:6379/0`, or an [Upstash](https://upstash.com) `rediss://` URL) only to share those caches and counts among several instances; with one instance, or two at most as deployed, it adds little, and a Redis outage changes nothing but that sharing.

6. **Run the Web App**:
   ```bash
   cd web
   npm ci
   npm run dev     # http://localhost:5173, forwarding API calls to the server above
   npm run build   # or build it once; the API server then serves it at http://127.0.0.1:8000
   ```
   Sign in with an email and password (see [signing in with an email](#signing-in-with-an-email-and-password)), GitHub, or Google (see [signing in](#signing-in-with-github-and-google)), or your API key. The app has:
   * **Markets**: a calendar of FOMC meetings, major companies' earnings, IPOs, and market holidays, and market news by category (see [Markets](models.md#markets)).
   * **Search** by ticker or company name, over the screened universe (`GET /api/v1/search?q=`) and, with a Finnhub key, Finnhub's US listings, which adds ETFs, ADRs, and stocks outside the universe; any other ticker can be typed in directly.
   * **Stock pages** with price and volume, factor exposures with confidence intervals, GARCH volatility with its forecast, and the AI research report, written on request and streamed as Claude writes it.
   * **Portfolio**: up to two named portfolios of up to 40 holdings each, saved to your account (`GET` and `PUT /api/v1/portfolio/holdings?portfolio=1` or `2`, as symbols and share counts valued at the latest prices), a Monte Carlo fan chart of outcomes, and a heatmap of how the holdings move together, from the copula fit. Money-market funds such as SPAXX count as cash, and holdings with too little history are left out of the simulation and heatmap with a note saying which and why, rather than blocking them.
   * **Backtest**: under *Single ticker (or two)*, run a built-in strategy on any symbol, or your own code on it and up to 19 more added under *Also trade*, compare it with buy-and-hold, and reopen or delete saved runs. *Across tickers* ranks a universe you type in, or a preset such as the sector ETFs or your holdings, and holds the best of it; or, with *Write your own code*, runs your Python or C++ on every ticker the code names, with no symbol to pick first.
   * **Screener**: filter and sort the universe on any metric, by sector and universe.
   * **Settings**: store your Anthropic and market data keys, see what each kind of market data comes from, and set your AI limits.

   Every chart has a table view, and the app follows the system's light or dark mode. The frontend's TypeScript types are generated from the API: after changing an endpoint, run `python -m app.jobs.openapi`, then `npm run gen:api` in `web/` (the tests fail until both are current).

7. **Run the Tests**:
   ```bash
   pytest                # offline; no network or API calls
   pytest --live         # also call yfinance, FMP, Alpha Vantage, SEC EDGAR, Ken French's library, and R
   pytest --live --paid  # also make two billed Claude requests (about $0.15-0.25)
   cd web && npm test    # the web frontend; npm run typecheck, lint, and format:check too
   ```

## Docker and Deployment

### Docker Compose

`docker-compose.yml` runs the API with Postgres 18 and Redis 8, reading settings and secrets from `.env` as above:
```bash
docker compose up --build                                  # http://localhost:8000
docker compose run --rm api python -m app.jobs.users create you@example.com
docker compose run --rm api python -m app.jobs.screener    # refresh the screened universe
```
The images include the built web frontend, at `http://localhost:8000`. The `Dockerfile` builds two images: `app`, the default, without R, where GARCH returns 503; and `app-r`, with R and `rugarch`, for GARCH (`PYQXEL_TARGET=app-r docker compose up --build`). Both run as an unprivileged user and contain no secrets. The containers' Postgres and Redis are reachable only from the API container, so they do not clash with servers already running locally; set `POSTGRES_PASSWORD` in `.env` to change the local database's password (`pyqxel` by default), and `PYQXEL_PORT` to serve on another port.

### Health checks

Neither needs an API key, and neither reveals error details.

* `GET /health` is a liveness check: it answers whenever the process does, so a database outage does not get the API restarted. The images' Docker health check calls it.
* `GET /health/ready` is a readiness check: it reports `database` (`ok`, `unavailable`, or `outdated` when migrations are pending), `redis` (`ok`, `unavailable`, or `not_configured`) and `r` (`ok`, `unavailable`, or `not_started`). It returns 503 with `"status": "unavailable"` when the database cannot serve, so load balancers send traffic elsewhere until it recovers. Redis and R are optional: a Redis outage only marks the service `degraded`, since each instance then caches and counts rate limits in its own memory.

### Google Cloud Run

`deploy/cloud-run.sh` deploys the image with R to [Cloud Run](https://cloud.google.com/run), which scales to zero and bills only while requests run, so a personal deployment fits in the free tiers: Cloud Run's monthly allowance covers about 50 hours of request time, [Neon](https://neon.com)'s free Postgres holds the database, and [Upstash](https://upstash.com)'s free Redis is optional. An idle instance is let go after about 15 minutes, and starting another takes about 20 seconds, so a free Cloud Scheduler job requests `/health` every 10 minutes to keep one warm (below). Secrets live in Google Secret Manager, never in the repository or the image.

One-time setup:
1. Create a [Google Cloud project](https://console.cloud.google.com/projectcreate) and link a billing account (required even within the free tier), then [set a budget alert](https://console.cloud.google.com/billing/budgets) of a dollar or two. Install the [gcloud CLI](https://cloud.google.com/sdk/docs/install) and run `gcloud auth login`.
2. Create a free Neon project and copy its **pooled** connection string (the host contains `-pooler`). Redis is optional and best left out at this size: each instance caches in its own memory, and only with two instances running at once could an account make up to twice `RATE_LIMIT_PER_MINUTE`. To share caches among many instances, create an Upstash Redis database in the same region as Cloud Run, turn on **Eviction** so a full database drops old entries instead of refusing new ones, and copy its `rediss://` URL; every API request then spends a few Redis commands on its rate limit, so check that the plan's command allowance covers your traffic.
3. Store the secrets. Each command waits for you to paste the value and press Ctrl-D, so it never lands in your shell history:
   ```bash
   gcloud config set project YOUR_PROJECT_ID
   gcloud services enable secretmanager.googleapis.com
   gcloud secrets create pyqxel-database-url --data-file=-      # the Neon URL
   gcloud secrets create pyqxel-redis-url --data-file=-         # optional: an Upstash rediss:// URL
   gcloud secrets create pyqxel-screener-fmp-key --data-file=-  # the screener's licensed FMP key
   venv/bin/python -m app.jobs.users generate-encryption-key | \
     gcloud secrets create pyqxel-credentials-key --data-file=-  # for nightly refreshes, if missing
   ```
   Read a secret back with `gcloud secrets versions access latest --secret pyqxel-database-url`. Provider keys need no secret: the server never stores them.
4. Optionally, bill users for their nightly refreshes ([below](accounts-and-billing.md#paying-for-the-nightly-refresh)). In the [Stripe Dashboard](https://dashboard.stripe.com), create a restricted key (Developers → API keys) that can write Customers, Checkout Sessions, Customer portal, Billing meter events, Refunds, Subscriptions, Invoice items, PaymentIntents (to capture or release a held top-up), Products, and Prices, and read Meters, Charges, PaymentMethods, and Tax settings (or use the secret key while testing), then make the catalog and the webhook endpoint, and store both secrets:
   ```bash
   STRIPE_SECRET_KEY=rk_... PUBLIC_URL=https://YOUR_SERVICE_URL venv/bin/python -m app.jobs.billing setup \
     --webhook-url https://YOUR_SERVICE_URL/webhooks/stripe   # prints the webhook's signing secret once
   gcloud secrets create pyqxel-stripe-secret-key --data-file=-      # the rk_ or sk_ key
   gcloud secrets create pyqxel-stripe-webhook-secret --data-file=-  # the whsec_ secret
   ```
   Run `setup` once in test mode and again with live keys when you start charging; it makes only what is missing. Billing is on only once both secrets exist.

Deploy, and redeploy after each change:
```bash
PROJECT_ID=YOUR_PROJECT_ID SEC_USER_AGENT="PyQxel you@example.com" deploy/cloud-run.sh
```
The script enables the APIs it needs, builds the image on Cloud Build, and deploys:
* the `pyqxel` service: 1 CPU and 2 GiB, at most 2 instances (`MAX_INSTANCES`), with the yfinance fallback off so market data comes only from each account's FMP plan. While you are its only user, deploy with `YFINANCE_FALLBACK=true` to fill the gaps in FMP's free plan and refresh the screener without a paid key. Once others use it, deploy with `YFINANCE_FALLBACK=false`; the screener then needs the `pyqxel-screener-fmp-key` secret to refresh. A new revision takes traffic only once `/health/ready` passes, so it never serves from an unreachable or unmigrated database; migrations run at startup, one instance at a time.
* the `pyqxel-screener` job, which Cloud Scheduler runs at 6:30 pm New York time on weekdays (`SCREENER_SCHEDULE`; run it now with `gcloud run jobs execute pyqxel-screener --region us-central1`). Only the job reads `pyqxel-screener-fmp-key`, at `SCREENER_FMP_REQUESTS_PER_MINUTE` (default 300); without it, the job runs only the SEC first stage, which users' own refreshes start from, and builds no shared universe.
* the `pyqxel-nightly` job, which refreshes the own screeners of users who opted in, each at the time they chose. Cloud Scheduler calls the service every hour (`NIGHTLY_SCHEDULE`, UTC), and the service starts the job only when someone's refresh is due, so it does not start every hour to find nothing to do. It is deployed only once the `pyqxel-credentials-key` secret exists, and only it and the service read that secret.
* the `pyqxel-keep-warm` Cloud Scheduler job, which requests `/health` every 10 minutes (`KEEP_WARM_SCHEDULE`; `""` removes it) so visitors rarely wait for an instance to start. Cloud Scheduler's free tier covers three jobs, this being the third, and the requests cost a few milliseconds of CPU each. `/health` does not touch the database, so Neon still sleeps when nobody is using PyQxel. Cloud Run may still replace an instance now and then, so an occasional slow first request remains.
* housekeeping: Artifact Registry keeps the two newest images, for rolling back (about $0.03 a month over the 0.5 GB free tier), and Cloud Build's copies of the uploaded source are deleted after 7 days.

**Rolling back.** Each deploy is a new revision; to send traffic back to the one before (its image is still kept), list them and pick it:
```bash
gcloud run revisions list --service pyqxel --region us-central1
gcloud run services update-traffic pyqxel --region us-central1 --to-revisions REVISION_NAME=100
```
The next `deploy/cloud-run.sh` sends all traffic to the new revision again. A rollback does not undo a database migration, so a revision older than the latest migration may fail its readiness check.

Set `CORS_ORIGINS='["https://your-client.example"]'` for browser clients, and `REGION` to deploy elsewhere than `us-central1`. Don't store `ANTHROPIC_API_KEY` or `FINANCIAL_DATA_API_KEY` on the server; accounts send their own.

Create your account from your own machine, against the production database, so the API key is printed only in your terminal:
```bash
DATABASE_URL="$(gcloud secrets versions access latest --secret pyqxel-database-url)" \
DATABASE_PGBOUNCER=true python -m app.jobs.users create you@example.com
```
Then check the deployment with `curl https://<service-url>/health/ready`.

#### Signing in with GitHub and Google

Anyone can then make an account by signing in with GitHub or Google. Each needs an OAuth client of your own, registered with the service's address; set up either or both. Cloud Run serves the service at two addresses (`gcloud run services describe pyqxel --region us-central1 --format='value(metadata.annotations."run.googleapis.com/urls")'` lists them), and a sign-in comes back to the one it started from, so register the callback at each:

* **GitHub**: in GitHub, go to *Settings → Developer settings → OAuth Apps → New OAuth App*. Set the homepage URL to `https://<service-url>`, and add `https://<service-url>/api/v1/auth/github/callback` under *Redirect URIs* for each address, leaving wildcard matching and the device flow off. Copy the client ID, generate a client secret, and store the secret:
  ```bash
  printf %s 'the-client-secret' | gcloud secrets create pyqxel-github-client-secret --data-file=-
  ```
* **Google**: in the Google Cloud console, under *APIs & Services*, set up the *OAuth consent screen* (external, with the `openid`, `email` and `profile` scopes) and publish it, since while it is in testing only the test users you list can sign in. Then under *Credentials*, create an *OAuth client ID* for a *web application* with the authorized redirect URI `https://<service-url>/api/v1/auth/google/callback` for each address, and store its secret:
  ```bash
  printf %s 'the-client-secret' | gcloud secrets create pyqxel-google-client-secret --data-file=-
  ```

Deploy once with the client IDs, `GITHUB_CLIENT_ID=... GOOGLE_CLIENT_ID=... deploy/cloud-run.sh`; later deploys keep them. The sign-in page shows a button for each provider that has both its ID and secret. To try it locally, register a second client whose callback is `http://127.0.0.1:8000/api/v1/auth/<provider>/callback`, and set `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET` (or the `GOOGLE_` pair) in `.env`.

The native app signs in with the same clients, through the system browser: when the sign-in finishes, the browser opens the app with a `pyqxel://signin` link carrying a one-time code, which the app trades for its key together with a secret it kept. Nothing more needs registering.

#### Signing in with an email and password

People can also make an account, and sign in on the website and in the native app (with no browser), with an email address and a password of at least 12 characters, hashed with Argon2id. Making an account and **Forgot password?** work the same way: PyQxel emails a link, good for an hour and only once, and the page it opens is where the password is chosen. Nothing is attached to an address before then, so typing someone else's address reaches neither their account nor one made in their name for them to join later, and asking for a link or a wrong password gets the same answer whether or not the address has an account. Choosing a new password signs out every device the old one signed in. Attempts are limited per IP address (30 sign-ins a minute, 20 links an hour, generous since many people can share one address), per email address (10 sign-ins in 15 minutes, 3 links an hour), and for the whole server (90 links a day).

It needs a way to send email, and PyQxel sends from a Gmail account of its own, through Gmail's mail server, which Cloud Run can reach (on port 587, with TLS). It is free, and Gmail allows about 500 emails a day, well above the 90 a day PyQxel sends at most. To set it up:

1. On the Gmail account (PyQxel uses `pyqxel.app@gmail.com`), turn on **2-Step Verification** in its Google Account's **Security** settings; Google allows app passwords only then.
2. At [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords), make an app password named `PyQxel`. Google shows it once, as 16 letters; it lets PyQxel send as the account and nothing else, and can be revoked there at any time without changing the account's own password.
3. Store it as a secret (paste it when asked, then press Ctrl-D), and deploy with the account and the website's address, which every emailed link points to (it is set, not read from requests, so a forged `Host` header cannot redirect a link):
   ```bash
   gcloud secrets create pyqxel-smtp-password --data-file=-
   SMTP_HOST=smtp.gmail.com SMTP_USERNAME=pyqxel.app@gmail.com \
   EMAIL_FROM='PyQxel <pyqxel.app@gmail.com>' PUBLIC_URL=https://<service-url> deploy/cloud-run.sh
   ```
   Later deploys keep these. Without them, the sign-in page leaves email out.

[Resend](https://resend.com) is built in as well but not in use: it sends from a domain of your own (a few dollars a year), verified there, and its key goes in the `pyqxel-resend-api-key` secret. With both set up, Gmail is used. To try it locally without sending anything, set `EMAIL_TO_CONSOLE=true` in `.env`: emails, links included, go to the server's log, with links to `http://localhost:5173`.

**Sign in with Apple** is built but off. It needs a paid Apple Developer account: a *Services ID* with Sign in with Apple on and the return URL `https://<service-url>/api/v1/auth/apple/callback`, and a Sign in with Apple key (a `.p8` file). Set `APPLE_SIGN_IN_ENABLED=true`, `APPLE_CLIENT_ID` (the Services ID), `APPLE_TEAM_ID`, `APPLE_KEY_ID` and `APPLE_PRIVATE_KEY` (the `.p8` file's text) to turn it on.
