# Features and Models

How each part of PyQxel works, and what Claude is given. Every route, with its options, is in the API's interactive docs at `/docs`. Returns and volatilities are decimals (0.25 = 25%).

## Stock pages

* **Price and volatility**: daily and intraday bars, a GARCH(1,1) volatility forecast fitted on five years of returns (with the `arch` package), and an EWMA estimate beside it.
* **Factor exposures**: a regression of the stock's excess returns on the Fama-French factors (three-factor, Carhart four-factor, or five-factor), with Newey-West t-statistics, alpha, and each factor's share of the risk. Where Ken French's data is not used, the market factor is estimated from SPY, and the others roughly from ETFs.
* **Funds**: an ETF's or mutual fund's costs, holdings (from its SEC N-PORT filings), asset mix, and the figures weighted from the companies it holds.

### Fundamentals and valuation

Financial statements come from the company's XBRL filings on SEC EDGAR (US GAAP or IFRS, foreign issuers in their own currency), with Finnhub's and Alpha Vantage's relays filling gaps: trailing-twelve-month revenue, profits, cash flow, and EBITDA, each with growth and five years of history, and the balance sheet. With the market cap they give P/E, P/S, EV/EBITDA, free cash flow yield, margins, and return on equity. A ratio that means nothing (EV/EBITDA for a bank) is left out and says why.

### Company news

Two weeks of a company's news from Finnhub (or Alpha Vantage's paid feed), with counts by day and publisher; copies of a syndicated story count once.

### Analysts, earnings, and insiders

From Finnhub: analysts' ratings by month, earnings surprises and the next report date, insiders' open-market trades and sentiment, key metrics and peers, and public records (federal lobbying, government contracts, patents, recent SEC filings). Revenue growth and price to free cash flow for banks come from the filings, since Finnhub's are wrong for them.

### AI stock reports

An investment thesis or a risk summary, written by Claude from all of the above, plus the market backdrop, the stock's screener figures and sector ranks, its sector's medians, its recent 8-Ks, the Risk Factors and MD&A sections of its latest 10-K and 10-Q, 60 headlines, and the 20 most important articles in full ([what Claude is given](#what-claude-is-given)). Reports stream as Claude writes, are stored, and are reused for a day unless asked anew.

## Markets

FOMC meetings (from the Federal Reserve, no key), market holidays, earnings reports of large companies, IPOs, and market news by category, with the Treasury yield curve and the BLS's inflation and jobs figures behind every AI report.

## Stock screener

The screener filters and ranks a stored universe of US stocks on valuation, growth, margins, returns, momentum, volatility, drawdown, and factor betas, by sector and index, so a screen answers at once. A shared universe is built each weekday from the SEC's data and a licensed FMP key (`python -m app.jobs.screener`).

### Each user's own screener

Free data plans are for personal use, so a user can build a universe of their own on their own keys: on the Screener's Refresh Schedule tab, they choose what it rebuilds (every US stock, every US ETF, Nasdaq, the S&P 500, the Nasdaq-100, international stocks, and up to 100 tickers of their own) and when, and the nightly job (`python -m app.jobs.nightly`) refreshes it. Their market data keys are stored encrypted for it, and deleted when it is turned off. It costs its server time, [paid at cost](accounts-and-billing.md#paying-for-the-nightly-refresh).

## Claude's Picks

Claude picks five to ten securities to buy now and hold for a period from a week to sixty years, from the screener's leaders on each measure, with the profile you give (risk tolerance, amount, kinds of security, notes, holdings to consider). It has its own tab; the Screener and Portfolio link to it.

* **Each pick** has its weight, conviction, thesis, main risk, and an order to place: market, limit, or stop-limit, how long to leave it open, what to do if it never fills, and an optional stop-loss and profit target. The page writes them as steps for a Fidelity account: whole shares take the order as given, and the fraction left over is bought with a day limit entered each morning, since Fidelity takes fractional orders only at market or as day limits.
* **Performance** replays each list's orders on daily bars: a pick counts as bought only if and when its order would have filled, at that price, and one that never fills counts as cash. Lists are measured against SPY, QQQ, DIA, IWM, or VTI until their period ends.
* **Keeping picks past the period**: picks are still chosen for the holding period, but Claude may say to hold some longer, or until a condition is met; for recurring picks it writes a handoff to paste into the next list.
* **Scheduled picks** are made weekly or monthly while the browser is closed, on the stored keys.

## Portfolio

Up to five portfolios of up to 40 holdings, each with its account type and contribution room.

* **Monte Carlo simulation**: outcomes over a horizon, with copula or historical dependence and empirical or Student t returns, and VaR, CVaR, drawdowns, and a fan chart.
* **Copulas**: Gaussian and Student t fits of how holdings move together, with each pair's tail dependence and how often they actually had their worst days together.
* **AI review**: Claude reviews concentration, risk contributions, and diversification, given each holding's screener figures, news, filings, and signals, the market backdrop, and your latest picks, and sets a target for every holding; the server turns the targets into exact orders at the latest prices. Several portfolios can be reviewed as one, or each with its own plan.

## Backtesting

Built-in strategies (moving average crossover, time-series and dual momentum, mean reversion, RSI, Bollinger Bands, Donchian breakouts, volatility targeting, MACD) on one or two symbols against buy-and-hold, with costs, cash interest from Treasury bills, and factor attribution. *Across tickers* ranks 3 to 60 symbols by momentum, trend, reversal, or low volatility and holds the best. Custom strategies in Python (Pyodide) or C++ (Clang to WebAssembly) run in the browser, and only their positions reach the server. A coding assistant, on Claude Sonnet 5.5, explains and rewrites the code.

## What Claude is given

* **Documents as Markdown**: SEC filing sections keep their tables as Markdown tables.
* **Full articles**: the stories that matter most come with the article itself, as Markdown: read by Anthropic's web fetch tool on your Anthropic key, where Claude Sonnet 5.5 writes up each article's facts in its own words (about two cents an article). Every headline is still given, and the full articles outweigh them.
* **Waiting for more data**: a free Finnhub plan answers 60 requests a minute, so a longer wait gathers more. Reviews and picks spend the wait in priority order (for picks, your added tickers and the 25 likeliest picks in the order you choose, then your held stocks, then the rest; for reviews, your holdings, then your latest picks), and full articles are shared out the same way, 40 to 120 in all. The "i" beside *Wait for more data* shows what each wait gathers.
* **Additional context**: a box on every AI report for articles, research, or a picks handoff to weigh, kept apart from the notes, which say what to tailor to.
* **Web search**: optional; Anthropic's web search tool on your key, with every finding tied to its source.
* **Model and effort**: every report records the model and effort that wrote it. Times are given to Claude in UTC and shown, and written by Claude, in Eastern Time.

### Claude outside the app

Every AI report can instead hand over its exact prompt, with all the same data, to paste into Claude yourself (on claude.ai, for instance), with no API cost. Paste Claude's answer back and it is checked and saved like any other, marked as written outside the app, with the model Claude names and the effort you note.
