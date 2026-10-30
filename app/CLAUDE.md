# The API (`app/`)

FastAPI, async throughout. A request runs **endpoint → `data/` fetch → `stats/` compute →
`models/` schema**. Endpoints hold no math, and `stats/` makes no network calls.

## How a request flows

- **Auth:** every `/api/v1` route takes `user: CurrentUser` (`api/auth.py`), from the
  `Authorization: Bearer <key>` header, rate-limited per user. Provider keys come per
  request (`core/credentials.py`: `current_provider_keys()`), never from the database.
- **Market data:** `data/fetcher.py`. `fetch_price_history`, `fetch_ticker_info` and
  quotes try `available_providers()` in order (Finnhub for quotes or info where its free
  plan covers the symbol, then FMP, Alpha Vantage, then yfinance when
  `YFINANCE_FALLBACK` or the admin's switch is on) through `first_success()`. Results
  are cached in Redis when `REDIS_URL` is set (`core/cache.py`); without it every lookup
  goes to the provider. Several symbols at once:
  `data/panel.py: fetch_close_panel` (pass `tolerate_failures=True` to leave out the
  symbols that fail instead of raising).
- **Errors:** providers raise `DataFetchError` subclasses. Endpoints turn them into HTTP
  errors with `stocks.upstream_error()`: no key → 403, plan → 402, unknown symbol → 404,
  else 502. Too little data (`stats/volatility.InsufficientDataError`) → 422. Database
  down → 503, while the computed result is still returned, unsaved.
- **Claude:** `ai/agent.py` (stock reports), `ai/portfolio.py` (reviews),
  `ai/assistant.py` (strategy code), prompts in `ai/prompts.py`. Each streams over SSE
  (`fastapi.sse.EventSourceResponse`) with `thinking`/`report`/`result`/`error` events,
  uses the user's `X-Anthropic-Key`, counts against AI limits (`research.check_ai_limits`)
  and is cached for `ANALYSIS_CACHE_TTL_SECONDS`. `ai/external.py` builds the same
  prompt for pasting into claude.ai, and parses the answer brought back.

## Where things are

| Feature | Endpoint | Data | Stats | Models | Storage |
| --- | --- | --- | --- | --- | --- |
| Quotes, history, volatility | `stocks.py`, `quotes.py` (WebSocket) | `fetcher.py` | `garch.py` (R), `ewma.py`, `volatility.py`, `indicators.py` | `stock.py`, `volatility.py`, `quote.py` | |
| Factor regression | `factors.py` | `data/factors.py` (Ken French) | `stats/factors.py` | `factors.py` | |
| Fundamentals, valuation | `fundamentals.py` | `sec_edgar.py`, `fundamentals.py`, `xbrl_instance.py`, `alpha_vantage.py`, `finnhub_financials.py`, `exchange_rates.py` | `valuation.py` | `fundamentals.py` | |
| News, signals, metrics, alternative data | `news.py`, `signals.py`, `metrics.py`, `alternative.py` | `news.py`, `finnhub.py`, `alternative.py` | `stats/news.py`, `signals.py`, `alternative.py` | same names | |
| Markets page | `markets.py` | `data/markets.py`, `fomc.py` | `stats/markets.py` | `markets.py` | |
| Screener, search | `screener.py`, `search.py` | `universe.py` | `screening.py`, `peers.py` | `screener.py` | `db/screener.py`; refreshed by `jobs/screener.py` |
| AI stock report | `research.py` | everything above | | `research.py` | `db/analyses.py` |
| Backtests | `backtest.py` | `fetcher.py`, `panel.py`, `factors.py` | `backtest.py` (one asset, a portfolio, replayed custom weights), `cross_section.py` (rank a universe), `metrics.py` | `backtest.py` | `db/backtests.py` |
| Portfolio | `portfolio.py` | `panel.py`, `cash.py` (money-market funds) | `panel.py` (aligned returns, `usable_return_panel` drops what it cannot use), `copulas.py`, `monte_carlo.py`, `portfolio_risk.py`, `rebalance.py` (orders) | `portfolio.py`, `simulation.py`, `portfolio_analysis.py` | `db/holdings.py` (2 portfolios per account), `simulations.py`, `portfolio_analyses.py` |
| Coding assistant | `assistant.py` | |  | `assistant.py` | counted in `db/portfolio_analyses.py` |
| Accounts, sign-in | `account.py`, `signin.py` | |  | `account.py` | `db/users.py`, `app_settings.py`; `core/oauth.py`, `core/security.py` |

Custom strategies never run here. The browser runs them (see `web/CLAUDE.md`) and posts
positions or weights to `/backtests/custom`. The server only scores them. The Python
harness the browser runs lives at `web/src/runners/python/harness.py` and is tested by
`tests/test_strategy_harness.py`.

## Conventions

- **Schemas:** one Pydantic model per request and response, every field with a
  `description` (it becomes the API docs and the frontend's types). Shared types such as
  `PortfolioPeriod`, `SymbolList` and `MAX_SYMBOLS` (40) live in `models/portfolio.py`.
- **Route docstrings:** they become the OpenAPI description. Say what the route does and
  every error status it returns.
- **Database:** add the table to `db/tables.py`, add a migration, add the user-owned
  table to `db/users.delete_user`. A test checks that every table with `user_id` is
  emptied there.
- **CPU-heavy stats:** run in `asyncio.to_thread`. Simulations are limited by
  `_simulation_slots`.
- **Tests:** offline, one file per feature (`tests/test_<feature>*.py`). Fakes are
  patched on the endpoint module. `conftest.py` gives every test a fresh SQLite
  database and a signed-in user. Tests marked `live` and `paid` call real services and
  need `--live` or `--paid`.
