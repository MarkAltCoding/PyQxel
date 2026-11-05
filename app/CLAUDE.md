# The API (`app/`)

FastAPI, async throughout. A request runs **endpoint → `data/` fetch → `stats/` compute →
`models/` schema**. Endpoints hold no math, and `stats/` makes no network calls.

## How a request flows

- **Auth:** every `/api/v1` route takes `user: CurrentUser` (`api/auth.py`), from the
  `Authorization: Bearer <key>` header, rate-limited per user. Provider keys come per
  request (`core/credentials.py`: `current_provider_keys()`), never from the database.
  The one exception is the nightly refresh of a user's own screener (`jobs/nightly.py`),
  which runs with no request: users who opt in have their market data keys stored,
  Fernet-encrypted under `CREDENTIALS_ENCRYPTION_KEY` (`core/security.py`), in
  `nightly_refreshes`, deleted on opt-out or account deletion (`db/nightly.py`).
- **Market data:** `data/fetcher.py`. `fetch_price_history`, `fetch_ticker_info` and
  quotes try `available_providers(kind, symbol)`: the user's keys in the order
  `PROVIDER_ORDER` sets for each kind of data (daily, intraday, quote, profile, exchange
  rate) and kind of symbol (US, index, abroad), cheapest first as `docs/data-sources.md`
  explains, then yfinance (see `CLAUDE.local.md`), through `first_success()`. A plan's
  refusal or reached limit is remembered per key and passes the request on; a free plan's
  partial history (a `HISTORY_NOTICE`) is kept only if no later key does better. Providers:
  Tiingo, FMP, EODHD, Finnhub, Alpha Vantage. `web/src/lib/keyCoverage.ts` mirrors the order
  for Settings, and a test checks they match. Results
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
| Screener, search | `screener.py`, `search.py` | `universe.py` | `screening.py`, `peers.py` | `screener.py` | `db/screener.py`; refreshed by `jobs/screener.py` (shared SEC first stage, then the shared universe) |
| A user's own screener (nightly refresh) | `account.py` (`/me/nightly-refresh`); `screener.py` reads it | `screener_metrics.py` (Finnhub metrics, price histories) | `screening.py: user_screen_metrics` | `nightly.py`, `screener.py` | `db/nightly.py`, `user_screener_*` tables; run hourly by `jobs/nightly.py` |
| AI stock report | `research.py` | everything above | | `research.py` | `db/analyses.py` |
| Backtests | `backtest.py` | `fetcher.py`, `panel.py`, `factors.py` | `backtest.py` (one asset, a portfolio, replayed custom weights), `cross_section.py` (rank a universe), `metrics.py` | `backtest.py` | `db/backtests.py` |
| Portfolio | `portfolio.py` (also each holding's gain over a window, `/changes`, and 52-week range, `/ranges`; reviews of one portfolio or both together, `combined`) | `panel.py`, `cash.py` (money-market funds) | `panel.py` (aligned returns, `usable_return_panel` drops what it cannot use), `copulas.py`, `monte_carlo.py`, `portfolio_risk.py`, `rebalance.py` (orders), `performance.py` (window changes, 52-week ranges) | `portfolio.py`, `simulation.py`, `portfolio_analysis.py` | `db/holdings.py` (2 portfolios per account), `simulations.py`, `portfolio_analyses.py` |
| Coding assistant | `assistant.py` | |  | `assistant.py` | counted in `db/portfolio_analyses.py` |
| Accounts, sign-in | `account.py`, `signin.py` (website and native app; the app's one-time codes) | |  | `account.py` | `db/users.py` (also `app_sign_ins`), `app_settings.py`; `core/oauth.py` (GitHub, Google, Apple, off by default), `core/security.py` |

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
