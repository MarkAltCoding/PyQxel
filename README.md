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
* **Market Data Feeds**: `yfinance`, Financial Modeling Prep (FMP) / Alpha Vantage API, SEC EDGAR Scraper
* **Client Interface Target**: Cross-platform REST & WebSocket API servicing macOS and iOS clients (Swift/React Native compatible)

---

## Quickstart & Local Setup

### Prerequisites

1. **Python 3.14+** installed on your system.
2. **R 4.3+** installed (required for advanced statistical and econometric modules).
3. **Claude Code** CLI configured locally (`npm install -g @anthropic-ai/claude-code`).

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
   pip install -r requirements.txt
   ```
   Then install the R packages the statistical models use:
   ```bash
   Rscript -e 'install.packages("rugarch", repos = "https://cloud.r-project.org")'
   ```

3. **Configure Environment Variables**:
   ```bash
   cp .env.example .env
   ```
   Then fill in `.env`:
   * `ANTHROPIC_API_KEY` — from the [Anthropic Console](https://console.anthropic.com/).
   * `FINANCIAL_DATA_API_KEY` — optional; a [Financial Modeling Prep](https://site.financialmodelingprep.com/developer/docs) key, used as a fallback when `yfinance` fails.
   * `SEC_USER_AGENT` — optional; your name and contact email (e.g. `PyQxel jane@example.com`), which the [SEC requires](https://www.sec.gov/os/accessing-edgar-data) of automated clients. When set, the fundamentals endpoint works, and AI analyses also get the company's financial statements and read the Risk Factors and MD&A sections of its latest 10-K and 10-Q.
   * `R_HOME` — the output of `R RHOME` (e.g. `/Library/Frameworks/R.framework/Resources` on macOS).

4. **Run the API Server**:
   ```bash
   uvicorn app.main:app --reload
   ```
   The API is served at `http://127.0.0.1:8000`, with interactive docs at `http://127.0.0.1:8000/docs`. Check it with `curl http://127.0.0.1:8000/health`.

   Two endpoints stream:
   * `POST /api/v1/stocks/{symbol}/analysis/stream` sends the AI analysis as Server-Sent Events while Claude writes it: `context`, then `thinking` and `report` fragments, then the final `result` (or an `error`). Try it with `curl -N -X POST http://127.0.0.1:8000/api/v1/stocks/AAPL/analysis/stream`.
   * `ws://127.0.0.1:8000/api/v1/ws/quotes?symbols=AAPL,MSFT` streams live quotes. Change what it follows by sending `{"action": "subscribe", "symbols": ["TSLA"]}` or `{"action": "unsubscribe", ...}`. Quotes are polled every 15 seconds by default (`interval`, 5 to 300 seconds), and only new or changed quotes are sent.

   Backtests (`POST /api/v1/stocks/{symbol}/backtest`) are stored and returned with an `id`. List them with `GET /api/v1/backtests` (filter by `symbol` or `strategy`, page with `limit` and `offset`), fetch one in full with `GET /api/v1/backtests/{id}`, and remove it with `DELETE /api/v1/backtests/{id}`. They go to SQLite at `data_cache/pyqxel.db` by default; to use Postgres, `pip install asyncpg` and set `DATABASE_URL=postgresql+asyncpg://user:pass@host/pyqxel`. If the database is unreachable, backtests still run and are returned with a null `id`.

   The schema is managed with [Alembic](https://alembic.sqlalchemy.org/) migrations in `app/db/migrations/`, applied automatically at startup. After changing a table in `app/db/tables.py`, generate a migration with `alembic revision --autogenerate -m "describe the change"`, review it, and commit it; `alembic upgrade head` applies it by hand. The tests fail if the models and migrations disagree.

   AI analyses are stored in the same database, so you only pay for a report once. For 24 hours (`ANALYSIS_CACHE_TTL_SECONDS`), a request with the same symbol, `kind`, `period` and `include_filings`, under the same model and effort, gets the stored report back without fetching data or calling Claude. Identical requests that arrive at the same time are written once. Reused reports have `"cached": true` and keep their original `generated_at`; send `"refresh": true` to pay for a new one. Every report stays readable for free: list them with `GET /api/v1/analyses` (filter by `symbol` or `kind`) and fetch one with `GET /api/v1/analyses/{id}`. Each analysis also gives Claude the stock's Carhart factor exposures and its fundamentals and valuation (see below), so reports can say how much of its risk is market, size, value or momentum, and weigh the price against the company's earnings and cash flow.

   Set `REDIS_URL` (e.g. `redis://localhost:6379/0`) to also cache ticker info and price history for 5 minutes (1 minute for intraday bars), shared by every worker. Without Redis, or while it is down, those requests go straight to the data providers.

5. **Run the Tests**:
   ```bash
   pytest                # offline; no network or API calls
   pytest --live         # also call yfinance, FMP, SEC EDGAR, Ken French's library and R
   pytest --live --paid  # also make two billed Claude requests (about $0.15-0.25)
   ```

---

## Quantitative Models

Returns and volatilities are decimals (0.25 = 25%), annualized where noted. Every endpoint below returns 404 naming unknown symbols, 422 when there is too little history, and 502 when a data provider fails.

### Fundamentals and valuation

`GET /api/v1/stocks/{symbol}/fundamentals` reads the company's financial statements from its XBRL filings on [SEC EDGAR](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) (US GAAP, in US dollars; needs `SEC_USER_AGENT`). It returns trailing-twelve-month (TTM) revenue, gross profit, operating income, net income, operating cash flow, capital expenditure, free cash flow, depreciation and EBITDA, each with year-on-year growth and five fiscal years of history, and the latest cash, debt, equity and shares outstanding. Combined with the current market cap, they give P/E, P/S, EV/EBITDA, free-cash-flow yield, gross, operating, net and FCF margins, and return on equity.

Companies tag the same item differently and change tags over time, so each item uses the tag the company reported most recently, and the response names it. Periods come from each figure's own dates, so fiscal years ending in any month line up, and TTM figures are built from the last fiscal year and the year to date. An item the company stopped reporting is left out rather than shown stale, and a ratio that is missing or not meaningful (a P/E on a loss, EV/EBITDA for a bank) is null, with `valuation.notes` saying why. Companies filing under IFRS, such as many foreign issuers, return 404. Normalized figures are reused for six hours.

### Stock screener

The screener searches a precomputed universe of US-listed stocks, so a screen reads the database and answers at once. Build and refresh the universe with:

```bash
python -m app.jobs.screener                 # daily, after the US close
python -m app.jobs.screener --fundamentals  # also rebuild financials now
```

Each run reads every common stock listed on NYSE, Nasdaq and Cboe from the SEC (leaving out preferreds, notes, warrants, units and rights), downloads about 13 months of prices for all of them, and keeps those trading above $2 with at least $1M a day and a market cap above $50M (`SCREENER_MIN_PRICE`, `SCREENER_MIN_DOLLAR_VOLUME`, `SCREENER_MIN_MARKET_CAP`). Market caps, sectors and industries come from Nasdaq's public screener feed, with SEC share counts and yfinance filling gaps. Financials are rebuilt from the SEC's bulk company facts archive (a 1.3 GB download) once a week, or every `SCREENER_FUNDAMENTALS_REFRESH_DAYS`, and companies new to the universe are fetched individually in between. A run takes about two minutes, plus a few on the days financials are rebuilt; schedule it with cron or launchd, e.g. `30 17 * * 1-5 cd /path/to/PyQxel && venv/bin/python -m app.jobs.screener`. Run during market hours, it leaves out the unfinished day.

For each stock it stores price, market cap, average dollar volume, P/E, P/S, EV/EBITDA, FCF yield, margins, ROE, revenue and earnings growth, 1- and 6-month returns, 12-1 month momentum, volatility, maximum drawdown, and Carhart market, size, value and momentum betas.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/screener \
  -H 'Content-Type: application/json' \
  -d '{"universe": "large_cap",
       "filters": [{"field": "pe_ratio", "max": 20}, {"field": "momentum_12_1", "min": 0},
                   {"field": "beta_market", "max": 1.2}],
       "sort": {"field": "beta_market", "descending": false}, "limit": 25}'
```

`universe` is `all`, `large_cap` (the 500 largest, standing in for the S&P 500), `broad_market` (the 3,000 largest, like the Russell 3000) or `liquid` ($5M or more a day); index memberships themselves are licensed. Filters take `min`, `max` or both and drop stocks without a value, `sectors` and `industries` narrow by Nasdaq's classification (which places some companies unexpectedly, such as Altria in Health Care), and sorting puts missing values last. Sort on a beta to rank by factor exposure. `GET /api/v1/screener/status` reports the universe's size, when it was refreshed, and why the last refresh failed, if it did. Foreign markets are not covered: most foreign companies report under IFRS, outside the SEC's US GAAP data.

### Factor exposures

`GET /api/v1/stocks/{symbol}/factors?model=ff3&period=5y` regresses a stock's daily excess returns on [Fama-French factors](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html): `ff3` (market, size, value), `carhart4` (plus momentum) or `ff5` (plus profitability and investment), over `1y`, `2y`, `5y` or `10y`. It returns annualized alpha, each factor's beta with Newey-West t-statistics and p-values, R², idiosyncratic volatility, and each factor's share of the return variance.

Ken French publishes the factors monthly, about a month behind, so the regression ends at `factor_data_end` and `notice` says how many recent days were left out. Downloads are reused for 24 hours, and if the library cannot be reached a copy up to 30 days old is used.

### Copulas: how assets move together

```bash
curl -X POST http://127.0.0.1:8000/api/v1/portfolio/copula \
  -H 'Content-Type: application/json' \
  -d '{"symbols": ["SPY", "QQQ", "TLT"], "period": "5y"}'
```

Fits Gaussian and Student t copulas to 2-20 assets' daily returns, aligned on the days all of them traded, and compares them by AIC. The t copula's degrees of freedom measure how much more often the assets have extreme days together than a Gaussian allows. `pairs` gives each pair's rank correlation, its tail dependence under the t copula, and how often the two were actually in their worst (and best) 5% of days together. A warning appears when assets trade in time zones hours apart, since their same-date returns do not cover the same hours.

### Monte Carlo portfolio simulation

```bash
curl -X POST http://127.0.0.1:8000/api/v1/portfolio/simulate \
  -H 'Content-Type: application/json' \
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

## License

Distributed under the MIT License. See `LICENSE` for details.
