# AI Equity Research & Quantitative Investing Engine

An AI-native research and quantitative analysis platform designed for equity stock analysis, statistical modeling, factor research, and automated thesis generation. Built with a high-performance Python engine, specialized R statistical subroutines, and an API-first architecture designed for seamless deployment across macOS (MacBook) and iOS (iPhone).

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

* **Primary Backend Engine**: Python 3.11+ (FastAPI, Pandas, NumPy, Statsmodels, Scikit-Learn)
* **Statistical Modeling**: R 4.3+ (`rpy2` integration for GARCH modeling, time-series analysis, and econometric estimation)
* **AI Engine & NLP**: Anthropic API (Claude Opus 5.5, `claude-opus-5-5`), LangChain / LlamaIndex, BeautifulSoup4, PyPDF
* **Market Data Feeds**: `yfinance`, Financial Modeling Prep (FMP) / Alpha Vantage API, SEC EDGAR Scraper
* **Client Interface Target**: Cross-platform REST & WebSocket API servicing macOS and iOS clients (Swift/React Native compatible)

---

## Quickstart & Local Setup

### Prerequisites

1. **Python 3.11+** installed on your system.
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
   python3.11 -m venv venv
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
   * `SEC_USER_AGENT` — optional; your name and contact email (e.g. `PyQxel jane@example.com`), which the [SEC requires](https://www.sec.gov/os/accessing-edgar-data) of automated clients. When set, AI analyses also read the Risk Factors and MD&A sections of the company's latest 10-K and 10-Q.
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

   AI analyses are stored in the same database, so you only pay for a report once. For 24 hours (`ANALYSIS_CACHE_TTL_SECONDS`), a request with the same symbol, `kind`, `period` and `include_filings`, under the same model and effort, gets the stored report back without fetching data or calling Claude. Identical requests that arrive at the same time are written once. Reused reports have `"cached": true` and keep their original `generated_at`; send `"refresh": true` to pay for a new one. Every report stays readable for free: list them with `GET /api/v1/analyses` (filter by `symbol` or `kind`) and fetch one with `GET /api/v1/analyses/{id}`.

   Set `REDIS_URL` (e.g. `redis://localhost:6379/0`) to also cache ticker info and price history for 5 minutes (1 minute for intraday bars), shared by every worker. Without Redis, or while it is down, those requests go straight to the data providers.

5. **Run the Tests**:
   ```bash
   pytest
   ```

## License

Distributed under the MIT License. See `LICENSE` for details.
