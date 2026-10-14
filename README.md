# AI Equity Research & Quantitative Investing Engine

An AI-native research and quantitative analysis platform that helps investors choose the right securities. It combines equity analysis, statistical modeling, factor research, and automated thesis generation so an investor can see what drives a security's returns, how much risk it carries, and how it would behave alongside the rest of a portfolio before buying it. Built with a high-performance Python engine, specialized R statistical subroutines, and an API-first architecture designed for seamless deployment across macOS (MacBook) and iOS (iPhone).

**Author:** Mark Altreuter ([@MarkAltCoding](https://github.com/MarkAltCoding))  
**Institution:** University of Illinois Urbana-Champaign (UIUC), B.S. Computer Science  

---

## Key Features

* **AI-Assisted Fundamental Research**: Automated ingestion of financial filings, earnings call transcripts, and news feeds using LLM agents (Claude / Anthropic API) to synthesize actionable investment theses and downside risk factors.
* **Quantitative & Econometric Modeling**: Hybrid Python and R statistical architecture leveraging time-series analysis, GARCH models, multi-factor risk regression, and Monte Carlo portfolio simulation.
* **Automated Stock Screening & Backtesting**: Vectorized backtesting framework for custom long/short equity strategies, technical indicator analysis, and performance attribution (Sharpe, Sortino, Max Drawdown).
* **Cross-Platform API Architecture**: Lightweight, high-throughput RESTful and WebSocket API built with FastAPI, optimized for consumption by native macOS applications, iOS mobile apps, or web-based dashboards.

---

## Tech Stack & System Architecture

* **Primary Backend Engine**: Python 3.14+ (FastAPI, Pandas, NumPy, Statsmodels, Scikit-Learn)
* **Statistical Modeling**: R 4.3+ (`rpy2` integration for GARCH modeling, time-series analysis, and econometric estimation)
* **AI Engine & NLP**: Anthropic API (Claude Opus 5.5, `claude-opus-5-5`) through the official `anthropic` SDK, BeautifulSoup4 for SEC filing text
* **Market Data Feeds**: Financial Modeling Prep (FMP, the licensed primary source, on each user's own plan, and on the server's own licensed plan for the shared screener), `yfinance` (development fallback), Alpha Vantage API, SEC EDGAR
* **Client Interface Target**: Cross-platform REST & WebSocket API servicing macOS and iOS clients (Swift/React Native compatible)
* **Web Frontend**: React and TypeScript (Vite), with ECharts for charts, served by the API at `/`

---

## Quickstart & Local Setup

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
   `requirements-dev.txt` adds the test, type-checking and lint tools; servers need only `requirements.txt`.
   Then install the R packages the statistical models use:
   ```bash
   Rscript -e 'install.packages("rugarch", repos = "https://cloud.r-project.org")'
   ```

3. **Configure Environment Variables**:
   ```bash
   cp .env.example .env
   ```
   Then fill in `.env`:
   * `CREDENTIALS_ENCRYPTION_KEY` — required; encrypts the provider keys accounts store. Generate one with `python -m app.jobs.users generate-encryption-key`, and keep it: changing it makes stored keys unreadable.
   * `ANTHROPIC_API_KEY` and `FINANCIAL_DATA_API_KEY` — optional; your own [Anthropic](https://console.anthropic.com/) and [Financial Modeling Prep](https://site.financialmodelingprep.com/developer/docs) keys, read only to copy them into your own account (step 4). The server never uses them to serve requests: every account pays for its own usage with the keys it stores.
   * `SCREENER_FMP_API_KEY` — the FMP key the [stock screener](#stock-screener)'s nightly refresh runs on. Every account sees the universe it builds, so this is the server's key, not an account's, and its plan must allow showing FMP data to your users (FMP's individual plans are for personal use; ask FMP about a data display license). Set `SCREENER_FMP_REQUESTS_PER_MINUTE` to the plan's rate limit (default 300).
   * `YFINANCE_FALLBACK` — `true` for development: use `yfinance` when an account has no FMP key, or FMP fails or the account's plan does not cover a request (FMP's free plan excludes ETFs, indices, foreign listings and intraday bars), and for the screener when `SCREENER_FMP_API_KEY` is unset. Leave it off in production, where data comes only from licensed FMP plans.
   * `SEC_USER_AGENT` — optional; your name and contact email (e.g. `PyQxel jane@example.com`), which the [SEC requires](https://www.sec.gov/os/accessing-edgar-data) of automated clients. When set, the fundamentals endpoint works, and AI analyses also get the company's financial statements and read the Risk Factors and MD&A sections of its latest 10-K and 10-Q.
   * `R_HOME` — the output of `R RHOME` (e.g. `/Library/Frameworks/R.framework/Resources` on macOS).

4. **Create Your Account**:
   ```bash
   python -m app.jobs.users create you@example.com --import-env-keys --claim-unowned
   export PYQXEL_API_KEY=pq_...   # the key it prints, shown only once
   ```
   `--import-env-keys` stores your `.env` provider keys in your account; `--claim-unowned` gives it any results stored before accounts existed. Create other people's accounts without either flag, and send them their key; they store their own provider keys (see [Accounts](#accounts-and-billing)).

5. **Run the API Server**:
   ```bash
   uvicorn app.main:app --reload
   ```
   The API is served at `http://127.0.0.1:8000`, with interactive docs at `http://127.0.0.1:8000/docs`. Check it with `curl http://127.0.0.1:8000/health`, or `/health/ready` to also check the database and Redis. Every `/api/v1` request needs your API key: `curl -H "Authorization: Bearer $PYQXEL_API_KEY" http://127.0.0.1:8000/api/v1/me`.

   Two endpoints stream:
   * `POST /api/v1/stocks/{symbol}/analysis/stream` sends the AI analysis as Server-Sent Events while Claude writes it: `context`, then `thinking` and `report` fragments, then the final `result` (or an `error`). Try it with `curl -N -X POST -H "Authorization: Bearer $PYQXEL_API_KEY" http://127.0.0.1:8000/api/v1/stocks/AAPL/analysis/stream`.
   * `ws://127.0.0.1:8000/api/v1/ws/quotes?symbols=AAPL,MSFT&api_key=...` streams live quotes; browsers cannot set WebSocket headers, so this route also takes the key in the URL. Change what it follows by sending `{"action": "subscribe", "symbols": ["TSLA"]}` or `{"action": "unsubscribe", ...}`. Quotes are polled every 15 seconds by default (`interval`, 5 to 300 seconds), and only new or changed quotes are sent.

   Backtests (`POST /api/v1/stocks/{symbol}/backtest`) are stored and returned with an `id`. List them with `GET /api/v1/backtests` (filter by `symbol` or `strategy`, page with `limit` and `offset`), fetch one in full with `GET /api/v1/backtests/{id}`, and remove it with `DELETE /api/v1/backtests/{id}`. They go to SQLite at `data_cache/pyqxel.db` by default; to use Postgres, create a database (`createdb pyqxel`) and set `DATABASE_URL=postgresql+asyncpg://localhost/pyqxel`, adding a user and password if your server needs them. If the database is unreachable, backtests still run and are returned with a null `id`.

   The schema is managed with [Alembic](https://alembic.sqlalchemy.org/) migrations in `app/db/migrations/`, applied automatically at startup. After changing a table in `app/db/tables.py`, generate a migration with `alembic revision --autogenerate -m "describe the change"`, review it, and commit it; `alembic upgrade head` applies it by hand. The tests fail if the models and migrations disagree.

   AI analyses are stored in the same database, so you only pay for a report once. For 24 hours (`ANALYSIS_CACHE_TTL_SECONDS`), your request with the same symbol, `kind`, `period` and `include_filings`, under the same model and effort, gets the stored report back without fetching data or calling Claude. Identical requests that arrive at the same time are written once. Reused reports have `"cached": true` and keep their original `generated_at`; send `"refresh": true` to pay for a new one. Every report stays readable for free: list them with `GET /api/v1/analyses` (filter by `symbol` or `kind`) and fetch one with `GET /api/v1/analyses/{id}`. Each analysis also gives Claude the stock's Carhart factor exposures and its fundamentals and valuation (see below), so reports can say how much of its risk is market, size, value or momentum, and weigh the price against the company's earnings and cash flow.

   Set `REDIS_URL` (e.g. `redis://localhost:6379/0`, or an [Upstash](https://upstash.com) `rediss://` URL) to share caches and rate limits between every worker and every instance: ticker info and price history for 5 minutes (1 minute for intraday bars), exchange rates, factor data and normalized financials, and each account's per-minute request count. Without Redis, or while it is down, those requests go straight to the data providers and each process counts requests on its own. Quotes stay cached for 5 seconds in each process, and the SEC's ticker list for a day, since sharing them would cost more Redis commands than the provider requests it saves.

6. **Run the Web App**:
   ```bash
   cd web
   npm ci
   npm run dev     # http://localhost:5173, forwarding API calls to the server above
   npm run build   # or build it once; the API server then serves it at http://127.0.0.1:8000
   ```
   Sign in with your API key. The app has:
   * **Search** by ticker or company name, over the screened universe (`GET /api/v1/search?q=`); any other ticker, such as an ETF, can be typed in directly.
   * **Stock pages** with price and volume, factor exposures with confidence intervals, GARCH volatility with its forecast, and the AI research report, written on request and streamed as Claude writes it.
   * **Portfolio**: holdings saved to your account (`GET` and `PUT /api/v1/portfolio/holdings`, as symbols and share counts valued at the latest prices), a Monte Carlo fan chart of outcomes, and a heatmap of how the holdings move together, from the copula fit.
   * **Backtest**: run strategies on any symbol, compare them with buy-and-hold, and reopen or delete saved runs.
   * **Screener**: filter and sort the universe on any metric, by sector and universe.
   * **Settings**: store your Anthropic and FMP keys and set your AI limits.

   Every chart has a table view, and the app follows the system's light or dark mode. The frontend's TypeScript types are generated from the API: after changing an endpoint, run `python -m app.jobs.openapi`, then `npm run gen:api` in `web/` (the tests fail until both are current).

7. **Run the Tests**:
   ```bash
   pytest                # offline; no network or API calls
   pytest --live         # also call yfinance, FMP, SEC EDGAR, Ken French's library and R
   pytest --live --paid  # also make two billed Claude requests (about $0.15-0.25)
   cd web && npm test    # the web frontend; npm run typecheck, lint and format:check too
   ```

---

## Docker and Deployment

### Docker Compose

`docker-compose.yml` runs the API with Postgres 18 and Redis 8, reading settings and secrets from `.env` as above:
```bash
docker compose up --build                                  # http://localhost:8000
docker compose run --rm api python -m app.jobs.users create you@example.com --import-env-keys
docker compose run --rm api python -m app.jobs.screener    # refresh the screened universe
```
The images include the built web frontend, at `http://localhost:8000`. The `Dockerfile` builds two images: `app`, the default, without R, where GARCH returns 503; and `app-r`, with R and `rugarch`, for GARCH (`PYQXEL_TARGET=app-r docker compose up --build`). Both run as an unprivileged user and contain no secrets. The containers' Postgres and Redis are reachable only from the API container, so they do not clash with servers already running locally; set `POSTGRES_PASSWORD` in `.env` to change the local database's password (`pyqxel` by default), and `PYQXEL_PORT` to serve on another port.

### Health checks

Neither needs an API key, and neither reveals error details.

* `GET /health` is a liveness check: it answers whenever the process does, so a database outage does not get the API restarted. The images' Docker health check calls it.
* `GET /health/ready` is a readiness check: it reports `database` (`ok`, `unavailable`, or `outdated` when migrations are pending), `redis` (`ok`, `unavailable` or `not_configured`) and `r` (`ok`, `unavailable` or `not_started`). It returns 503 with `"status": "unavailable"` when the database cannot serve, so load balancers send traffic elsewhere until it recovers. Redis and R are optional: a Redis outage only marks the service `degraded`, since caching then stops and rate limits are counted per process.

### Google Cloud Run

`deploy/cloud-run.sh` deploys the image with R to [Cloud Run](https://cloud.google.com/run), which scales to zero and bills only while requests run, so a personal deployment fits in the free tiers: Cloud Run's monthly allowance covers about 50 hours of request time, [Neon](https://neon.com)'s free Postgres holds the database, and [Upstash](https://upstash.com)'s free Redis is optional. The first request after a quiet spell waits 10 to 20 seconds while an instance starts. Secrets live in Google Secret Manager, never in the repository or the image.

One-time setup:
1. Create a [Google Cloud project](https://console.cloud.google.com/projectcreate) and link a billing account (required even within the free tier), then [set a budget alert](https://console.cloud.google.com/billing/budgets) of a dollar or two. Install the [gcloud CLI](https://cloud.google.com/sdk/docs/install) and run `gcloud auth login`.
2. Create a free Neon project and copy its **pooled** connection string (the host contains `-pooler`). Then create an Upstash Redis database in the same region as Cloud Run, turn on **Eviction** in its settings so a full database drops old entries instead of refusing new ones, and copy its `rediss://` URL. Redis is optional, but without it each instance keeps its own caches and counts rate limits on its own, so with two instances an account could make twice `RATE_LIMIT_PER_MINUTE`. Every API request spends a few Redis commands on its rate limit, so check that the plan's command allowance covers your traffic.
3. Store the secrets. Each command waits for you to paste the value and press Ctrl-D, so it never lands in your shell history:
   ```bash
   gcloud config set project YOUR_PROJECT_ID
   gcloud services enable secretmanager.googleapis.com
   gcloud secrets create pyqxel-database-url --data-file=-      # the Neon URL
   python -m app.jobs.users generate-encryption-key | gcloud secrets create pyqxel-credentials-key --data-file=-
   gcloud secrets create pyqxel-redis-url --data-file=-         # the Upstash rediss:// URL
   gcloud secrets create pyqxel-screener-fmp-key --data-file=-  # the screener's licensed FMP key
   ```
   Read a secret back with `gcloud secrets versions access latest --secret pyqxel-credentials-key`. Never change the encryption key: the provider keys accounts stored can only be read with it.

Deploy, and redeploy after each change:
```bash
PROJECT_ID=YOUR_PROJECT_ID SEC_USER_AGENT="PyQxel you@example.com" deploy/cloud-run.sh
```
The script enables the APIs it needs, builds the image on Cloud Build, and deploys:
* the `pyqxel` service: 1 CPU and 2 GiB, at most 2 instances (`MAX_INSTANCES`), with the yfinance fallback off so market data comes only from each account's FMP plan. A new revision takes traffic only once `/health/ready` passes, so it never serves from an unreachable or unmigrated database; migrations run at startup, one instance at a time.
* the `pyqxel-screener` job, which Cloud Scheduler runs at 6:30 pm New York time on weekdays (`SCREENER_SCHEDULE`; run it now with `gcloud run jobs execute pyqxel-screener --region us-central1`). Only the job reads `pyqxel-screener-fmp-key`, at `SCREENER_FMP_REQUESTS_PER_MINUTE` (default 300); without it, refreshes fail and the screener reports why.

Set `CORS_ORIGINS='["https://your-client.example"]'` for browser clients, and `REGION` to deploy elsewhere than `us-central1`. Don't store `ANTHROPIC_API_KEY` or `FINANCIAL_DATA_API_KEY` on the server; accounts store their own.

Create your account from your own machine, against the production database, so the API key is printed only in your terminal:
```bash
DATABASE_URL="$(gcloud secrets versions access latest --secret pyqxel-database-url)" \
CREDENTIALS_ENCRYPTION_KEY="$(gcloud secrets versions access latest --secret pyqxel-credentials-key)" \
DATABASE_PGBOUNCER=true python -m app.jobs.users create you@example.com --import-env-keys
```
Then check the deployment with `curl https://<service-url>/health/ready`.

## Accounts and Billing

Every `/api/v1` request is made by an account, identified by its PyQxel API key in `Authorization: Bearer <key>` (or `X-API-Key`). Only a hash of each key is stored. Accounts pay for their own usage, and nothing one account does is billed to another or to the server:

* **AI analyses** run on the account's own Anthropic key, and **market data** comes from Financial Modeling Prep on its own FMP key. Store, replace or remove them with `PUT /api/v1/me/credentials` (`{"anthropic_api_key": "...", "fmp_api_key": "..."}`; omit a field to keep it, `null` removes it). Keys are encrypted at rest and never returned; `GET /api/v1/me` shows only their last four characters. Without an Anthropic key, analyses return 403; without an FMP key (and with the development fallback off), market data returns 403, and a request the account's FMP plan does not cover returns 402.
* **Stored results**, meaning backtests, analyses and simulations, belong to the account that made them; listing, reading and deleting see only its own. Reports are never reused across accounts, and FMP data cached for one account is not served to accounts without their own FMP key.
* **Limits**: each account can cap its new AI analyses with `PUT /api/v1/me/limits` (`{"ai_requests_per_hour": 10, "ai_requests_per_day": 50}`, `null` for no cap); reused reports are free and do not count, and `GET /api/v1/me` shows recent usage. Every account may also make `RATE_LIMIT_PER_MINUTE` requests a minute (120 by default) across the API, counted in Redis when it is configured.
* **API keys**: create more with `POST /api/v1/me/api-keys` (`{"name": "laptop"}`; the key is shown once), list them with `GET /api/v1/me/api-keys`, and revoke one with `DELETE /api/v1/me/api-keys/{id}`. If an account loses every key, `python -m app.jobs.users add-key you@example.com` issues a new one.

## Quantitative Models

Returns and volatilities are decimals (0.25 = 25%), annualized where noted. Every endpoint below returns 404 naming unknown symbols, 422 when there is too little history, and 502 when a data provider fails.

### Fundamentals and valuation

`GET /api/v1/stocks/{symbol}/fundamentals` reads the company's financial statements from its XBRL filings on [SEC EDGAR](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) (US GAAP or IFRS, in the company's reporting currency; needs `SEC_USER_AGENT`). It returns trailing-twelve-month (TTM) revenue, gross profit, operating income, net income, operating cash flow, capital expenditure, free cash flow, depreciation and EBITDA, each with year-on-year growth and five fiscal years of history, and the latest cash, debt, equity and shares outstanding. Combined with the current market cap, they give P/E, P/S, EV/EBITDA, free-cash-flow yield, gross, operating, net and FCF margins, and return on equity.

Companies tag the same item differently and change tags over time, so each item uses the tag the company reported most recently, and the response names it. Periods come from each figure's own dates, so fiscal years ending in any month line up, and TTM figures are built from the last fiscal year and the year to date. An item the company stopped reporting is left out rather than shown stale, and a ratio that is missing or not meaningful (a P/E on a loss, EV/EBITDA for a bank) is null, with `valuation.notes` saying why. Foreign issuers filing 20-F or 40-F reports under IFRS are read too, in the currency they report in (`financials.currency`, e.g. `TWD` for TSMC), and valuation converts their figures to the listing's currency at the latest exchange rate from FMP, on the account's own plan (`valuation.exchange_rate`; null when the plan does not quote the pair, which FMP's free plan does only for major currencies, unless `YFINANCE_FALLBACK` is on). They file only annual reports with EDGAR, so their TTM figures are their last fiscal year and are flagged as stale only after a missed year. When the SEC's company facts dataset leaves out a periodic filing, as it did TSMC's 2025 20-F, the figures are read from that filing's own XBRL, and the screener refetches companies whose financials look a period behind. Companies with neither US GAAP nor IFRS data return 404. Normalized figures are reused for six hours.

### Stock screener

The screener searches a precomputed universe of US-listed stocks, so a screen reads the database and answers at once. Build and refresh the universe with:

```bash
python -m app.jobs.screener                 # daily, after the US close
python -m app.jobs.screener --fundamentals  # also rebuild financials now
```

Each run reads every common stock listed on NYSE, Nasdaq and Cboe from the SEC (leaving out preferreds, notes, warrants, units and rights), downloads about 13 months of prices for all of them, and keeps those trading above $2 with at least $1M a day and a market cap above $50M (`SCREENER_MIN_PRICE`, `SCREENER_MIN_DOLLAR_VOLUME`, `SCREENER_MIN_MARKET_CAP`). Market caps, sectors and industries come from FMP's company screener, with SEC share counts and FMP company profiles filling gaps, and prices from FMP's dividend-adjusted daily bars, all on `SCREENER_FMP_API_KEY` (see [Installation](#installation)); stocks the company screener already puts below the market cap floor are not priced. Financials are rebuilt from the SEC's bulk company facts archive (a 1.3 GB download) once a week, or every `SCREENER_FUNDAMENTALS_REFRESH_DAYS`, and companies new to the universe are fetched individually in between. A run makes one FMP request per remaining stock, about 20 minutes at 300 requests a minute, plus a few minutes on the days financials are rebuilt; schedule it with cron or launchd, e.g. `30 17 * * 1-5 cd /path/to/PyQxel && venv/bin/python -m app.jobs.screener`. Run during market hours, it leaves out the unfinished day.

For each stock it stores price, market cap, average dollar volume, P/E, P/S, EV/EBITDA, FCF yield, margins, ROE, revenue and earnings growth, 1- and 6-month returns, 12-1 month momentum, volatility, maximum drawdown, and Carhart market, size, value and momentum betas.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/screener \
  -H "Authorization: Bearer $PYQXEL_API_KEY" -H 'Content-Type: application/json' \
  -d '{"universe": "large_cap",
       "filters": [{"field": "pe_ratio", "max": 20}, {"field": "momentum_12_1", "min": 0},
                   {"field": "beta_market", "max": 1.2}],
       "sort": {"field": "beta_market", "descending": false}, "limit": 25}'
```

`universe` is `all`, `large_cap` (the 500 largest, standing in for the S&P 500), `broad_market` (the 3,000 largest, like the Russell 3000) or `liquid` ($5M or more a day); index memberships themselves are licensed. Filters take `min`, `max` or both and drop stocks without a value, `sectors` and `industries` narrow by FMP's classification (`GET /api/v1/screener/status` lists the sectors), and sorting puts missing values last. Sort on a beta to rank by factor exposure. `GET /api/v1/screener/status` reports the universe's size, when it was refreshed, and why the last refresh failed, if it did. Foreign markets are not covered, but US-listed foreign issuers are, with their IFRS financials converted to dollars.

### Factor exposures

`GET /api/v1/stocks/{symbol}/factors?model=ff3&period=5y` regresses a stock's daily excess returns on [Fama-French factors](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html): `ff3` (market, size, value), `carhart4` (plus momentum) or `ff5` (plus profitability and investment), over `1y`, `2y`, `5y` or `10y`. It returns annualized alpha, each factor's beta with Newey-West t-statistics and p-values, R², idiosyncratic volatility, and each factor's share of the return variance.

Ken French publishes the factors monthly, about a month behind, so the regression ends at `factor_data_end` and `notice` says how many recent days were left out. Downloads are reused for 24 hours, and if the library cannot be reached a copy up to 30 days old is used.

### Copulas: how assets move together

```bash
curl -X POST http://127.0.0.1:8000/api/v1/portfolio/copula \
  -H "Authorization: Bearer $PYQXEL_API_KEY" -H 'Content-Type: application/json' \
  -d '{"symbols": ["SPY", "QQQ", "TLT"], "period": "5y"}'
```

Fits Gaussian and Student t copulas to 2-20 assets' daily returns, aligned on the days all of them traded, and compares them by AIC. The t copula's degrees of freedom measure how much more often the assets have extreme days together than a Gaussian allows. `pairs` gives each pair's rank correlation, its tail dependence under the t copula, and how often the two were actually in their worst (and best) 5% of days together. A warning appears when assets trade in time zones hours apart, since their same-date returns do not cover the same hours.

### Monte Carlo portfolio simulation

```bash
curl -X POST http://127.0.0.1:8000/api/v1/portfolio/simulate \
  -H "Authorization: Bearer $PYQXEL_API_KEY" -H 'Content-Type: application/json' \
  -d '{"holdings": [{"symbol": "SPY", "weight": 0.6}, {"symbol": "TLT", "weight": 0.4}],
       "horizon": 252, "paths": 10000, "initial_value": 100000}'
```

Simulates the portfolio `horizon` trading days ahead (default 21) over `paths` paths (default 10,000), fitted on `period` of history (default `5y`):

* `dependence`: how holdings move together. `student_t` (default) or `gaussian` copulas, or `empirical`, which resamples whole historical days and so keeps each pair's own tendency to crash together.
* `marginals`: each holding's daily returns from its own history (`empirical`, default) or a fitted Student t, which can produce days worse than any on record.
* `rebalancing`: `daily` (default) resets the target weights every day; `none` buys once and holds, so weights drift, reported as `mean_final_weights`.
* `seed`: reproduces a previous result exactly; every result reports the seed it used.

The result gives the distribution of final value and return, expected return, probability of loss, VaR and CVaR at 95% and 99%, the distribution of maximum drawdowns, and daily percentiles for a fan chart. `tail_checks` compares how often each pair crashes together in the simulation with history, and warns when a copula understates it by more than sampling noise explains; `empirical` dependence avoids that.

Runs are limited to 2.6 million path-days and 15 million draws (paths × horizon × assets), and at most two compute at once. Results are stored: list them with `GET /api/v1/portfolio/simulations` (filter by `symbol`), fetch one with `GET /api/v1/portfolio/simulations/{id}`, and remove it with `DELETE`.

### Backtesting

`POST /api/v1/stocks/{symbol}/backtest` scores a strategy on one stock against buy-and-hold, and `POST /api/v1/portfolio/backtest` runs it on every holding of a portfolio against holding the same weights. Strategies (`strategy.type`):

* `buy_and_hold`.
* `sma_crossover`: long while the `fast`-bar moving average is above the `slow` one (default 50/200).
* `time_series_momentum`: long while the stock's own return from `lookback` bars ago (default 252, a year) to `skip` bars ago is positive.
* `mean_reversion`: buy when the close is `entry_z` rolling standard deviations below its `window`-bar average (default 2 below a 20-day average), and sell once it is back within `exit_z` of it.

Each takes `allow_short` to go short instead of flat. A position set at a close is held over the next bar, and trades pay `cost_bps` per unit of turnover. With `cash_interest`, capital not invested earns the risk-free rate (the one-month T-bill from Ken French's data, compounded over each bar), as do a short's sale proceeds; otherwise it earns nothing.

In a portfolio, each holding is traded on its own prices on the days every holding traded, its position is scaled by its weight, and weights are reset daily. `assets` gives each holding's contribution, exposure and trades.

Both return `attribution` for daily bars: a regression of the strategy's daily excess returns on `carhart4` factors (or `ff3`, `ff5`, or `null` to skip). `attribution.fit.alpha` is the annualized return the factors leave unexplained, with a Newey-West t-statistic, and the betas show how much of the result was simply market, size, value or momentum exposure. Single-stock backtests are stored as described above; portfolio backtests are not.

## License

Copyright (c) 2026 Mark Altreuter. All rights reserved. See [`LICENSE`](LICENSE).

The code is public so that it can be read and evaluated, not reused. You may view it, and use a PyQxel instance I host through your own account, but you may not copy, modify, distribute, deploy or otherwise use the code, in whole or in part, without my written permission, which may require attribution and payment. To ask, contact me through [GitHub](https://github.com/MarkAltCoding).
