# PyQxel

A personal investment-research web app: a FastAPI backend (`app/`, Python 3.14, with R
for GARCH) and a React + TypeScript frontend (`web/`, Vite) that FastAPI serves in
production. Market data comes from FMP, Finnhub and Alpha Vantage on each user's own
keys, with yfinance as a fallback. Claude writes stock reports, portfolio reviews and
strategy code. Custom strategies run in the browser (Pyodide for Python, Clang compiled
to WebAssembly for C++). Only their positions go to the server, which scores them.

`app/CLAUDE.md` and `web/CLAUDE.md` map each side module by module. Read the one for the
side you are changing before searching the code.

## Layout

| Path | What it holds |
| --- | --- |
| `app/api/v1/endpoints/` | One FastAPI router per feature; thin, calls `data/` and `stats/` |
| `app/data/` | Fetching from providers (async, with fallbacks) |
| `app/stats/` | Pure computation on pandas: models, backtests, risk; R in `stats/r_scripts/` |
| `app/ai/` | Claude: prompts (`prompts.py`), streaming, the coding assistant |
| `app/models/` | Pydantic request/response schemas, the API contract |
| `app/db/` | SQLAlchemy async tables (`tables.py`), storage helpers, Alembic `migrations/` |
| `app/jobs/` | CLIs: `users` (accounts, keys), `screener` (nightly refresh), `openapi` |
| `tests/` | pytest, offline: every provider is monkeypatched |
| `web/src/pages/` | One component per route: Stock, Markets, Portfolio, Backtest, Screener, Settings |
| `web/src/runners/` | In-browser Python/C++ strategy runners (Web Workers) |
| `web/openapi.json`, `web/src/api/schema.d.ts` | Generated from the API; never edit by hand |
| `deploy/` | Cloud Run deploy script; production DB is Neon Postgres |

## Commands

- **All checks, as CI runs them:** `scripts/check.sh` (or `py` / `web`; extra args go to
  pytest, e.g. `scripts/check.sh py -k portfolio`). It regenerates the OpenAPI schema
  and the frontend types and formats the code first, then prints only failures.
- **Run locally:**
  - API: `YFINANCE_FALLBACK=true venv/bin/uvicorn app.main:app --port 8000`
  - Web: `cd web && npm run dev`, at `http://localhost:5173`. Vite listens on IPv6
    localhost only, so `127.0.0.1:5173` is refused.
  - A throwaway account: `venv/bin/python -m app.jobs.users create someone@example.invalid`
    prints a key. A browser takes it as `localStorage['pyqxel.apiKey']`. Delete it after
    with `... users delete someone@example.invalid --yes`.
- **Migrations:** `venv/bin/alembic upgrade head` (local Postgres from `.env`). A test
  fails if the models and migrations differ, so every table change needs a new revision
  in `app/db/migrations/versions/` (numbered `00NN_...`; use `batch_alter_table`, since
  tests run on SQLite).
- **Deploy:** manual, from the local checkout:
  `PROJECT_ID=<project> SEC_USER_AGENT="PyQxel <email>" YFINANCE_FALLBACK=true deploy/cloud-run.sh`
  (the real values are in the git-ignored `CLAUDE.local.md`). Migrations run when the
  new version starts. Back up Neon first when one drops data.

## Workflow

- Commit and push straight to `main`; no feature branches or PRs. CI runs on the push:
  watch it with `/opt/homebrew/bin/gh run list --branch main` (`gh` is not on the PATH).
- After changing a Pydantic model or a route, regenerate the frontend's types (the check
  script does), then fix what `tsc` reports.
- Check a UI change in a real browser before calling it done. Playwright is not in the
  repo: install it in a scratch directory.

## Traps

- The virtualenv is `venv/`, not `.venv/`. Python 3.14 allows `except A, B:` without
  parentheses, and the code uses it; it is not a bug.
- The shell is zsh: an unquoted `$VAR` holding several words is not split into arguments.
- Provider keys are never stored on the server. The browser sends them as
  `X-FMP-Key`, `X-Finnhub-Key`, `X-Alpha-Vantage-Key` and `X-Anthropic-Key` on each
  request.
- Everything must work on a free FMP plan, which prices only a sample of symbols, so
  production runs with the yfinance fallback on.
- Tests patch names in the module that uses them, e.g.
  `monkeypatch.setattr(portfolio, "fetch_close_panel", fake)` on
  `app.api.v1.endpoints.portfolio`, not in `app.data.panel`.
- Write like the surrounding code: docstrings and comments are full, plain sentences
  about what something is for; user-facing text explains itself to a beginner investor.

## 4. Architecture Guidelines for Claude Code

When generating code and scaffolding this codebase, strictly adhere to the following principles:

1. **Strict Type Annotations & Pydantic Validation**:
   - All Python functions must include type hints (`typing` module / standard modern Python typing).
   - API endpoints must leverage Pydantic schemas for request validation and response serialization.

2. **Modular Architecture**:
   - Keep AI logic (`app/ai/`), financial ingestion (`app/data/`), statistical modeling (`app/stats/`), and API endpoints (`app/api/`) completely decoupled.
   - R code should reside in `app/stats/r_scripts/` with dedicated Python wrapper functions handling data conversion and execution.

3. **Asynchronous Execution**:
   - External market data calls (`yfinance`, FMP, SEC EDGAR) and Anthropic API requests must use async/await patterns (`httpx2`, `AsyncAnthropic`) to maintain responsiveness.

4. **Error Handling & Resilience**:
   - Implement graceful fallback mechanisms for rate limits or network issues with financial APIs.
   - Validate market data inputs to handle missing values or non-trading day gaps before passing data into quantitative models.

5. **Explain Every Stat, Model and Graph**:
   - Every stat, model, chart, metric row or table column added to the web app gets an information icon (`<InfoTip topic=... />`, the circled "i") with a beginner-friendly explanation: what it measures, a rule-of-thumb range of good and worrying values where one applies, and any caveat.
   - Write the explanation as an entry in `web/src/lib/glossary.ts`, and reference it by key. `Stat` and `ChartCard` require an `info` prop, so the build fails until one is given.
   - Keep the "i" beside a heading (in a `heading-with-info` wrapper), not inside it, so the heading's accessible name stays clean.
