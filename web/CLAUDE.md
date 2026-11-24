# The web app (`web/`)

React 19 + TypeScript on Vite, TanStack Query for data, ECharts for charts, CodeMirror 6
for the strategy editor. Routes are in `src/routes.tsx`, inside `components/Layout.tsx`.
Styles are one file, `src/styles/global.css`, with color tokens on `:root` and dark-mode
overrides. Use the tokens (`--surface`, `--text-secondary`, `--good-mark`, …), never raw
colors. Blue text (links, labels) uses `--accent-ink`, not `--accent`, which is too light for
text; `e2e/accessibility.spec.ts` runs axe's WCAG AA checks on every page in both themes.
`scripts/third-party-licenses.mjs` writes the open-source notices the terms link to, on each build.

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
| `StockPage` | `PriceChart`, `FactorChart`, `VolatilityChart`, `FundCard` (an ETF or mutual fund's costs, holdings, asset mix, bond figures, and the figures weighted from its companies, in place of a company's; a mutual fund has no news or metrics cards), `MetricsCard`, `SignalsCard`, `NewsCard` (paged by `Pager`), `AlternativeCard`, `AnalysisPanel` (AI report) |
| `MarketsPage` | market news (paged), event calendar (`lib/calendar.ts`) |
| `PortfolioPage` | `HoldingsTable` (5 portfolios, up to 40 holdings, a holding at 0 shares dropped on Save; sortable, two gain/loss windows and a 52-week range per holding from `lib/holdings.ts`, and an account total), late-listing cutoff, `SimulationCard` (`FanChart`), `CopulaCard` (`CopulaHeatmap`), `AccountSettingsFields` (account type and contribution room, with `ContributionLimitsNote`; `lib/accounts.ts`), `PortfolioReviewCard` (one portfolio, or 2 to 5 chosen together as one or each with its own plan, goals shared or per account, per-account cash; remembered as `pyqxel.reviewGroup`; every saved review in `SavedReviews`, a row opening it in place of the latest; "Let the review see my latest Claude's Picks", `include_picks`), and beneath it a link card (`link-card`) to Claude's Picks |
| `ScreenerPage`, `RefreshSchedulePage` | Under `/screener`, joined by `ScreenerTabs`. `RefreshSchedulePage` (`/screener/refresh`) is the one home of `NightlyRefreshCard`; Settings links to it, and it links to Settings' `BillingCard`. |
| `PicksPage` | `/picks`, a main tab (the old `/screener/picks` redirects, `MovedTo`): a header around `components/ClaudesPicks.tsx`, which the Screener and Portfolio link to: the holding period in years, months, and weeks and the rest of the profile (`lib/picks.ts`), the latest or an earlier list of Claude's Picks as `PickCard`s with the screener's figures and the orders to place at a broker (`lib/pickOrders.ts: orderSteps`), how the shown list has done against a benchmark (`components/PicksPerformance.tsx: PicksPerformanceCard`, the backtest's `EquityChart`), every saved list with its return in `SavedPicksCard` (a row opens it; the benchmark is remembered by `lib/picks.ts`), the holdings of chosen portfolios shown to Claude (`holdings_portfolios`), tickers added to the candidates and every candidate searchable (`components/PicksCandidates.tsx`, `added_symbols`, `POST /picks/candidates`), `PicksSchedule` (weekly and monthly picks, with a conflict check against the refresh), and "Show in the screener", which opens `ScreenerPage` with `?picks=` on its `picks` universe choice (a screen with `symbols`) |
| `BacktestPage` | Tabs (links, `?mode=universe`): Single Ticker (or Two) (`mode` absent; a stock, ETF or fund): built-in rules or custom code, with "Also trade" for several symbols. Across Tickers: `CrossSectionalBacktest` (rank by a signal), or the same component with `across` (code that trades every ticker it names, `lib/tickers.ts`) |
| `SettingsPage`, `SignInPage`, `PrivacyPage` | Settings shows each key's plan usage (`KeyUsage`, `lib/usage.ts`; plan choices kept in the browser), each key's parts of the app with a checkbox and a daily limit (`KeyFeatures`, `lib/keyFeatures.ts`: `api/client.ts` sends `X-PyQxel-Feature` and `X-PyQxel-Key-Limits`, leaves out keys turned off, and adds why to an error when the server names a key held back in `X-PyQxel-Held-Back`) and which pages ask before loading; `SettingsNav` lists its sections, beside the cards or above them on a narrow screen. `NightlyRefreshCard` (Refresh Schedule tab) opts in to the nightly refresh of the user's own screener (`lib/nightlyRefresh.ts`) on a schedule (`ScheduleFields`: how often, a calendar of dates, per-weekday times; `lib/schedule.ts`), with the keys it may use and each price-history key's own nightly limit (`PRICE_LIMIT_CHOICES`), what it rebuilds (a set ticks and locks the sets it holds, `SET_HOLDS`) (any of every US stock, every US ETF, Nasdaq, the S&P 500, the Nasdaq-100, and international stocks, plus tickers added with `SearchBox`), and what one refresh spends, and re-sends the keys after one changes. `BillingCard` pays for the refresh at cost (`lib/billing.ts`): credit bought on Stripe's Checkout, a weekly, monthly, or yearly usage subscription with what each would lose to fees, and the history; nothing shows on a server that does not bill, and the native app links to the website or, in an App Store build, offers no way to pay (`paymentPlace`). `SignInPage` also signs in with an email and password (`auth/PasswordSignIn`, in the page itself, the native app too), and `SetPasswordPage` (`/signin/password#token=…`) is where an emailed link chooses the password. In the native app, `SignInPage` signs in with GitHub or Google through the system browser (`auth/nativeSignIn.ts`); `auth/NativeSignInLinks` takes the `pyqxel://signin` link back to it |

Pages that call providers as soon as they open (Stock, Markets, Portfolio) ask first:
`LoadGate` (or `useLoadGate` in `lib/loadGate.ts` with `LoadGatePanel`) posts what the
page would fetch to `/usage/estimate` and shows the requests each key would spend, unless
nothing would be spent, the user agreed earlier in the tab, or chose to load that page
without asking. Backtests that a plan cut short come back as 409 or `short_history`, and
`ShortHistoryNotice` asks before running on the shorter history (`lib/shortHistory.ts`).

Each card has an id, so a link can end in `#id` (`lib/scrollToHash.ts` scrolls to it,
even through signing in): Stock `price`, `factors`, `volatility`, `fund`, `news`, `signals`,
`metrics`, `alternative-data`, `ai-report`; Claude's Picks `picks-form`, `picks`, `picks-performance`, `saved-picks`; Portfolio `holdings`, `late-listings`,
`simulation`, `comovement`, `ai-review`, `claudes-picks`; Markets `calendar`, `market-news`; Backtest
`backtest-form` (or `ranking-form`), `strategy-code`, `result`, `performance`,
`allocation`, `stocks`, `saved-backtests`; Screener `screen`, `matches`; Refresh Schedule `nightly-refresh`;
Settings `key-coverage`, `provider-keys`, `data-coverage`, `plan-usage`, `nightly-refresh` (a link
card), `billing`, `ai-limits`, `delete-account`. Give a new card one too (`ChartCard` takes `id`).

AI reports (`AnalysisPanel`, `PortfolioReviewCard`, `PicksPage`) share
`components/ReportParts.tsx`: `AskedFor` (what a report was made for, `lib/askedFor.ts`, and
the model that wrote it, `lib/modelName.ts`), a verdict with meters, tabs, and a Visual / Full text switch
remembered by `lib/reportView.ts`. Each offers `WebSearchOption` (remembered by
`lib/webSearch.ts`) and `WaitForDataOption` (its "i" lists what each wait gathers, from the `/wait-plans` routes) (how long to wait for data a provider's per-minute
limit holds back, sent as `wait_seconds`; `lib/waitForData.ts`, five minutes by default), asks the effort used outside the app in `ExternalClaude` (`askEffort`, `lib/outsideClaude.ts`), shows searches as they happen with `AiProgressDetail`, and lists what
the web search found, with sources, in `WebSources`.

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

- **Every stat, chart, table column, and badge gets an explanation:** an `InfoTip` keyed
  to `lib/glossary.ts`. `Stat` and `ChartCard` require `info`. An entry's `what` is a
  sentence or two, its `formula` uses `_sub` and `^sup` (or `_{…}`, `^{…}`), and detail
  goes in `more`, shown on "More" or when the tip is pinned; a test caps the lengths.
- **Keep text short:** a card's subtitle is one line or two; put the rest behind
  `ReadMore` (a summary, with `more` shown on "Read more").
- **One look:** a page's tabs are `.subtabs` links in its `.page-header` (an underlined
  strip); the accent marks where you are (main tab, page tab) and info notices; a card
  that stands for another page is a `.link-card`; main pages' icons come from `PageIcon`.
- **Reuse the controls:**
  - `Segmented` for a few exclusive choices.
  - `PresetOrCustom` for presets plus a typed number.
  - `MultiToggle` for combinable choices.
  - `Pager` with `lib/paging.ts` for lists.
  - `Notice` and `ErrorNotice` for messages.
  - `SearchBox` for picking a ticker.
- **Formatting** goes through `lib/format.ts` (`percent`, `money`, `number`,
  `signedPercent`, `tone`). Decimals are fractions (0.25 = 25%). Every moment is shown in
  Eastern Time (`dateTime`, `clockTime`, `marketDay`), whatever the device's zone.
- **Billing warnings:** a run the credit would not cover is warned of with
  `lib/billing.ts: creditShortfall` wherever it is started or scheduled.
- **Links that leave PyQxel:** write them as usual, `target="_blank" rel="noopener noreferrer"`.
  In the native app `ExternalLinkGuard` (mounted in `App.tsx`) catches every one, asks with
  `OpenInBrowserDialog`, and opens the system browser; a web view cannot open them itself.
- **Every page has a way in and out:** public pages (the privacy policy) sit in
  `PublicLayout`, which is the app's `Layout` once signed in and a bar with Back and Sign
  in before.
- **Phones and touch:** under 760px the main links are a bottom tab bar (`Layout.tsx`),
  and the bars are padded by `env(safe-area-inset-*)`. Touch screens (`pointer: coarse`)
  get 44px controls and 16px fields, since iOS zooms into smaller text: give a new
  control's small size an override in that block of `global.css`. Nothing may scroll sideways at
  375px: a wide table becomes cards there (`HoldingsTable` with `useMediaQuery(PHONE_QUERY)`,
  `.holdings-cards`), and every placeholder must fit its field (keep them short and put
  guidance in the label or a note).
- **Updates:** `UpdateBanner` (mounted in `App.tsx`) offers a newer signed release in the
  desktop app and installs it (`lib/appUpdates.ts`); it shows nothing in a browser, on a
  phone, or until a newer release is published.
- **Browser storage** only through `lib/storage.ts` (`readStored`/`writeStored`, which
  never throw), under `pyqxel.*` keys. Every such key follows the account
  (`lib/savedState.ts`: loaded by `RequireKey` before any page renders, each write sent to
  `/me/saved` a moment later, removed from the browser on signing out) unless it is in
  `LOCAL_ONLY`: add a key there if it is a secret, a sign-in, or truly this device's own. In the native app, `pyqxel.apiKey` and
  `pyqxel.providerKeys` go to secure storage instead (`lib/secureStorage.ts`, loaded before
  the first render; the Keychain on iOS, a private file on macOS, `src-tauri/src/secrets.rs`),
  whose header holds the app's threat model. `AuthContext` renews a sign-in key in use.
- **Lint:** oxlint fails on exporting non-components from a component file. Put shared
  constants and hooks in `lib/`.
- **Browser tests:** Playwright in `e2e/` (`npm run e2e` after `npm run build`), against
  the real API and a seeded database, at a desktop and a phone's width; `e2e/fixtures.ts`
  signs a page in and fails a test on any error thrown in the page. They reach no market
  data provider, so they cover what the seeded account and universe can show.
- **Tests:** vitest + Testing Library, next to the code (`*.test.tsx`). Fake the API with
  `vi.spyOn(globalThis, 'fetch')`. Mock `charts/EChart`, since jsdom has no
  `ResizeObserver` (see `BacktestPage.test.tsx`). `test/setup.ts` clears localStorage
  after each test.
