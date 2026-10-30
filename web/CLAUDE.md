# The web app (`web/`)

React 19 + TypeScript on Vite, TanStack Query for data, ECharts for charts, CodeMirror 6
for the strategy editor. Routes are in `src/routes.tsx`, inside `components/Layout.tsx`.
Styles are one file, `src/styles/global.css`, with color tokens on `:root` and dark-mode
overrides. Use the tokens (`--surface`, `--text-secondary`, `--good-mark`, …), never raw
colors.

## Data

- `api/endpoints.ts` has one function per API route. Use them, not `fetch`.
- `api/client.ts` sends the API key and the browser's provider keys
  (`lib/providerKeys.ts`) with every request, and throws `ApiError(status, detail)`.
- `api/types.ts` names the generated `api/schema.d.ts` types. Never edit the schema:
  `scripts/check.sh` regenerates it from the API.
- Claude's streams: `api/sse.ts` reads Server-Sent Events, and `api/aiStream.ts` turns
  them into progress plus a result.

## Pages

| Page | Main pieces |
| --- | --- |
| `StockPage` | `PriceChart`, `FactorChart`, `VolatilityChart`, `MetricsCard`, `SignalsCard`, `NewsCard` (paged by `Pager`), `AlternativeCard`, `AnalysisPanel` (AI report) |
| `MarketsPage` | market news (paged), event calendar (`lib/calendar.ts`) |
| `PortfolioPage` | holdings table (2 portfolios, up to 40 holdings), late-listing cutoff, `SimulationCard` (`FanChart`), `CopulaCard` (`CopulaHeatmap`), `PortfolioReviewCard` |
| `BacktestPage` | Single stock: built-in rules or custom code, with "Also trade" for several symbols. Across tickers: `CrossSectionalBacktest` (rank by a signal), or the same component with `across` (code that trades every ticker it names, `lib/tickers.ts`) |
| `ScreenerPage`, `SettingsPage`, `SignInPage`, `PrivacyPage` | |

Each card has an id, so a link can end in `#id` (`lib/scrollToHash.ts` scrolls to it,
even through signing in): Stock `price`, `factors`, `volatility`, `news`, `signals`,
`metrics`, `alternative-data`, `ai-report`; Portfolio `holdings`, `late-listings`,
`simulation`, `comovement`, `ai-review`; Markets `calendar`, `market-news`; Backtest
`backtest-form` (or `ranking-form`), `strategy-code`, `result`, `performance`,
`allocation`, `stocks`, `saved-backtests`; Screener `screen`, `matches`; Settings
`key-coverage`, `provider-keys`, `ai-limits`, `admin`, `delete-account`. Give a new card
one too (`ChartCard` takes `id`).

AI reports (`AnalysisPanel`, `PortfolioReviewCard`) share `components/ReportParts.tsx`: a
verdict with meters, tabs, and a Visual / Full text switch remembered by
`lib/reportView.ts`.

## Custom strategies in the browser

`runners/index.ts: runnerFor(language)` returns a runner (`runners/types.ts`) with
`run(code, bars)` for one symbol and `runUniverse(code, aligned)` for several, both in a
Web Worker that goes off the network once loaded (`runners/lockdown.ts`).

- **Python:** Pyodide; the harness `runners/python/harness.py` validates what
  `strategy()` returns.
- **C++:** YoWASP Clang to WebAssembly. The API given to the code is
  `runners/cpp/pyqxel.h`, compiled with `PYQXEL_UNIVERSE` for several symbols.
- **Starter code:** `editor/starters/`, by `starterCode(language, kind)`, where kind is
  one, several or across. Drafts are kept per kind in localStorage.

## Conventions

- **Every stat, chart, table column and badge gets an explanation:** an `InfoTip` keyed
  to `lib/glossary.ts`. `Stat` and `ChartCard` require `info`.
- **Reuse the controls:**
  - `Segmented` for a few exclusive choices.
  - `PresetOrCustom` for presets plus a typed number.
  - `MultiToggle` for combinable choices.
  - `Pager` with `lib/paging.ts` for lists.
  - `Notice` and `ErrorNotice` for messages.
  - `SearchBox` for picking a ticker.
- **Formatting** goes through `lib/format.ts` (`percent`, `money`, `number`,
  `signedPercent`, `tone`). Decimals are fractions (0.25 = 25%).
- **Browser storage** only through `lib/storage.ts` (`readStored`/`writeStored`, which
  never throw), under `pyqxel.*` keys.
- **Lint:** oxlint fails on exporting non-components from a component file. Put shared
  constants and hooks in `lib/`.
- **Tests:** vitest + Testing Library, next to the code (`*.test.tsx`). Fake the API with
  `vi.spyOn(globalThis, 'fetch')`. Mock `charts/EChart`, since jsdom has no
  `ResizeObserver` (see `BacktestPage.test.tsx`). `test/setup.ts` clears localStorage
  after each test.
