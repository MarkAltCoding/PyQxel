# Market data sources

Which provider can supply each kind of market data PyQxel uses, on free and paid plans,
and in what order to try them. It was written to replace yfinance and Nasdaq's public
screener feed, which are unlicensed, with licensed sources.

Checked on **November 2, 2026**: every free plan was tested with a real free key, and
paid plans were read from the providers' pricing pages where those could be read. ETFs,
funds, and listings abroad were tested again on **November 5, 2026** (VOO, QQQM, SPY,
SAP.DE, VOD.L, 7203.T, and ASML), with the yfinance fallback off; FMP could not be, since
the free key had used its day's requests.
Prices and coverage change, so recheck before relying on a figure, especially the ones
marked _unverified_.

## How PyQxel may use each source

Every plan below, free or paid, is for **personal use**. Each provider forbids showing
its data to anyone but the key's owner without a separate commercial agreement.

So PyQxel follows these rules:

- **Each user brings their own keys.** A user's requests run on their own keys, and
  data one user's key fetched is never shown to another user. The app already works
  this way, and must keep doing so.
- **Shared data uses only public sources.** Anything every account sees, such as the
  screener's shared first stage, may come only from public sources:
  - **SEC EDGAR:** US government data, free to reuse.
  - **European Central Bank reference rates:** free to reuse, as long as the ECB is
    credited.

## Keyless public sources

Data that needs no key never spends one: PyQxel asks these sources first, wherever they
can answer. Their terms were read on **November 6, 2026**, with a for-profit app in mind.
None of this is legal advice; recheck before launching commercially.

| Source | Key? | Reuse, including commercial | Used for | Status |
| --- | --- | --- | --- | --- |
| **SEC EDGAR** | None; at most 10 requests a second, with a User-Agent naming the app and a contact | "Information presented on sec.gov is considered public information and may be copied or further distributed ... without the SEC's permission"; citation requested. "SEC" and "EDGAR" are trademarks | Financial statements, filings text, the screener's listings and industries, fund holdings (N-PORT), **a stock's sector and industry**, **its list of recent filings** | In use; the last two added November 6, 2026 |
| **US Treasury** daily bill rates and par yield curve (home.treasury.gov) and **FiscalData** API | None | A work of the US government, so public domain; FiscalData: "available to copy, adapt, redistribute, or otherwise use for non-commercial or commercial purposes" | **The interest cash earns in backtests** (4-week bill, daily since 2002); **the yield curve given to Claude's reports, picks, and reviews** (`app/data/economy.py`; tested November 20, 2026) | Bills added November 6, the curve November 20, 2026 |
| **Federal Reserve Board** H.10 exchange rates and H.15 interest rates | None | "Information on Board's website is in the public domain and may be copied and distributed without permission"; cite the Board | **Exchange rates for currencies the ECB does not publish** (23 currencies, such as the Taiwan dollar; released weekly) | Added November 6, 2026 |
| **European Central Bank** reference rates | None | Free reuse, commercial or not, quoting "Source: ECB statistics" and leaving the data unchanged | Exchange rates | In use |
| **Federal Reserve** FOMC calendar | None | Public domain, as above | Markets page | In use |
| **BLS** (inflation, jobs) | None for API v1: 25 requests a day per server; a free key gives 500 | Public domain; cite BLS and the date retrieved, and say BLS cannot vouch for derived analyses (`EconomyContext.sources` carries both) | **Consumer prices, core prices, unemployment, and payrolls for Claude's reports, picks, and reviews**: one query for all four series, kept 12 hours (tested November 20, 2026) | Added November 20, 2026 |
| **World Bank** indicators | None; "reasonable request volume" | CC BY 4.0 with attribution "The World Bank: Dataset name: Data source"; some indicators belong to third parties | Nothing yet: global macro data | Candidate |
| **USAspending.gov** | None | US government data; no license stated | Could replace Finnhub's government contracts | Candidate |
| **Senate lobbying disclosures** (lda.gov) | None at 15 requests a minute; a free key gives 120 | "Data ... do not, and should not, include controls over its end use"; cite the date retrieved | Could replace Finnhub's lobbying data | Candidate |
| **FRED** (St. Louis Fed) | The API needs a free key; keyless downloads are for personal use only | **Not for a for-profit app:** "use the FRED Services for any commercial use" needs "the prior written consent of the Bank"; storing or caching FRED data is prohibited; third-party series need their owners' permission | Nothing | **Not used** |
| **Ken French's data library** | None | Copyright Kenneth R. French; no license stated | Factor models; cash interest only before 2002 | In use; **a commercial launch needs his permission or another source** (below) |

### FRED and backtesting

FRED cannot back a for-profit app's backtests. Its terms allow commercial use only with
the Bank's written consent, forbid storing or caching its data (which a backtest needs),
and leave each third-party series to its owner: the S&P 500 and Nasdaq index series, for
example, are their publishers' and need their permission. The parts a backtest needs come
from FRED's own sources instead, which allow it:

- **Cash interest:** the Treasury's daily bill rates, current to the last business day,
  where Ken French's one-month rate is a month or more behind.
- **Benchmarks and index history:** no keyless source allows commercial use; the S&P 500
  and Nasdaq indexes are licensed by their publishers. A benchmark is an ETF (SPY, QQQ) on
  the user's own price-history key.

### Factors without Ken French's library

No provider on a user's keys publishes factor returns, and other free factor libraries
(such as AQR's) carry the same kind of copyright. So when Ken French's library is not
used, or cannot be reached, the factors are estimated from ETF prices on the user's own
price-history key (`app/data/etf_factors.py`), with the Treasury's bill rate as the
risk-free rate: market SPY, size IWM over IVV, value IWD over IWF, momentum MTUM over IVV
(since 2013), profitability QUAL over IVV (since 2013). No ETF stands in for the
investment factor, so the five-factor model falls back to the market factor alone. Results
say they are ETF estimates. Since size, value, and momentum estimates are rough, a server
can offer the market factor alone instead, which SPY tracks closely: fits then report a
market beta, alpha, and R², and say the other factors are left out.

Checked November 6, 2026, five years of daily returns, Carhart model:

| Stock | Source | Market | Size | Value | Momentum | R² |
| --- | --- | --- | --- | --- | --- | --- |
| AAPL | Ken French | 1.11 | −0.35 | −0.16 | −0.20 | 0.53 |
| AAPL | ETF estimate | 1.09 | −0.10 | −0.31 | −0.59 | 0.58 |
| VTV | Ken French | 0.79 | −0.04 | 0.41 | −0.06 | 0.88 |
| VTV | ETF estimate | 0.93 | 0.00 | 0.54 | 0.07 | 0.97 |

Market betas agree closely; the others point the same way but differ in size, since the
academic factors are long-short portfolios and the ETFs long-only funds. The market factor
itself is near-identical: over the year to November 2026, SPY's daily return over the
bill rate correlated 0.993 with Ken French's, and AAPL's market-only beta was 0.59 from SPY
against 0.64 from Ken French's.

### Not replaced, and why

- **Insider trades:** the SEC's Form 4 filings are keyless, but each trade is its own
  filing, so a stock's 90 days take dozens of requests and seconds to read, where
  Finnhub's free plan answers in one with no daily cap. Finnhub stays first.
- **Prices, quotes, and market caps:** no public source publishes them.

## What yfinance and Nasdaq supply today

| Kind of data | Where PyQxel uses it | Source today |
| --- | --- | --- |
| Daily prices, US stocks | Charts, volatility, factors, backtests, portfolio models | yfinance when no key covers the symbol |
| Daily prices, ETFs | Same | **Done:** Tiingo on the user's key (FMP's free plan refuses most ETFs) |
| Daily prices, mutual and money-market funds | Portfolio (e.g. VFIAX, SPAXX) | **Done:** Tiingo on the user's key |
| Daily index levels (e.g. ^GSPC) | Stock page, comparisons | yfinance |
| Daily prices, international listings | Any non-US symbol | **Done where a free plan lists the exchange:** EODHD (a year), then Alpha Vantage (100 days); see [Listings abroad](#listings-abroad) |
| Intraday bars (1–90 min) | Stock page chart, intraday backtests | yfinance |
| Quotes | Holdings, stock page | Finnhub (free plan, US); yfinance otherwise |
| Company info: name, sector, industry, market cap, type | Stock page, portfolio sectors, cash detection | FMP or Finnhub where covered; Tiingo's search says whether a listing is a stock, ETF, mutual fund, or money-market fund; yfinance otherwise |
| Exchange rates | Converting foreign filers' financials to dollars | **Done:** the European Central Bank's reference rates since November 2, 2026; the user's own keys for currencies the ECB does not publish |
| Screener: market caps, sectors, industries | The nightly screener refresh | Nasdaq's public feed for the shared universe. **Done for each user's own screener** since November 3, 2026: Finnhub on their own key for market caps, and sectors and industries from each company's SEC industry (SIC) code |
| Screener: about 13 months of daily prices per stock | Momentum, volatility, drawdown, factor betas | yfinance for the shared universe. **Done for each user's own screener:** their own price-history key, for as many stocks as its plan allows a night |
| Financial statements | Fundamentals, valuation, screener | SEC EDGAR (public; no change needed) |

## What each free plan covers (tested)

The cells below record what happened when each free key requested that kind of data.

| Kind of data | FMP | Finnhub | Alpha Vantage | Tiingo | EODHD |
| --- | --- | --- | --- | --- | --- |
| Daily, US stock | Popular large caps only (AAPL, MSFT, PLTR, KO, F); refused for small caps (IJR, AEHR, SMTC, CRCL). 5 years, adjusted. | None | Last 100 days, unadjusted | **Any symbol, 30+ years, adjusted** | Any symbol, last 1 year, adjusted |
| Daily, ETF | SPY yes; VOO and IJR refused | None | Last 100 days | **Yes, 30+ years** | Last 1 year |
| Daily, mutual fund | Refused | None | Last 100 days | **Yes** | Last 1 year |
| Daily, money-market fund | Refused | None | Not tested | **Yes** | Last 1 year |
| Daily, index (S&P 500 level) | Refused (quote only) | None | Premium | None (use an ETF such as SPY) | **Last 1 year** |
| Daily, international | Refused | None | Last 100 days, unadjusted, on its own suffixes (SAP.DEX, VOD.LON); none for Tokyo | Chinese A-shares only | **Last 1 year on the exchanges it lists** (SAP.XETRA, VOD.LSE); it lists none in Tokyo, Hong Kong, or India |
| Intraday bars | None | None | Premium | **5-minute bars from IEX** (IEX volume only) | None |
| Quote, US | Large-cap sample | **Yes, real time** (ETFs and ADRs too: VOO, ASML) | End of day only | Yes (Tiingo's last price) | Yes, 15–20 min delayed |
| Quote, international | Refused | Refused (SAP.DE) | Yes on its suffixes (SAP.DEX) | None | Delayed (VOD.LSE) |
| Company info, US | Sector, industry, and market cap for the sample (ETFs included) | **Industry and market cap, any US stock**; for an ADR, the home listing's (ASML.AS, in euros); nothing for ETFs | Premium | Name for any US stock, ETF, or fund; **whether it is a stock, ETF, or mutual fund** (its search); sector, industry, and market cap only for the Dow 30 | Name, exchange, currency, and type from its search (1 call); fundamentals refused |
| Company info, international | Refused | Refused (SAP.DE) | Name and currency from its search | None | **Name, exchange, currency, and type** from its search |
| ETF and fund holdings and costs | Not tested | Refused (`etf/profile`, `mutual-fund/profile`) | **Yes for ETFs (`ETF_PROFILE`): net assets, expense ratio, yield, turnover, inception, leverage, sectors, and every holding**; nothing for mutual funds (an empty answer for VFIAX and FXAIX, November 11, 2026) | Refused (mutual fund add-on) | Refused (fundamentals) |
| Quote, mutual fund | Not tested | Refused (VFIAX) | Not tested | **Yes (last NAV, e.g. VFIAX)** | Not tested |
| List of every US ETF | Not tested | **Yes (`stock/symbol`): 6,626 exchange-traded products, 5,700 of them on US exchanges, in one request** | None | Its public ticker file | None |
| Exchange rates | Quote only | None | **Current rate**, plus 100 days of history | **Daily history, 5+ years** | Last 1 year |
| Screener: bulk market caps or sectors | Refused | One call per stock (see below) | None | None | None |
| Screener: per-stock metrics | None | **Yes: market cap, P/E, P/S, beta, 13/26/52-week returns, 3-month return volatility, average volume, 52-week high and low, margins, ROE**; for an ETF, the returns, volatility, beta, volume, and 52-week range | None | None | None |
| News, company and market | Not tested | **Yes: two weeks of any US company's news, and the market's by category** | Premium only: `NEWS_SENTIMENT` answered "This is a premium endpoint" on a free key that served `GLOBAL_QUOTE` (November 20, 2026) | Not tested (an add-on) | Not tested |

**Free-plan limits:**

| Provider | Limit | When it resets |
| --- | --- | --- |
| FMP | 250 requests a day. The audit plus a day of use reached it. | Not published; PyQxel counts days from midnight UTC |
| Finnhub | 60 requests a minute, no daily cap; at most 30 a second on every plan. Its US market data plans allow 150 (Basic, $49.99 a month), 300 (Standard, $129.99), or 900 (Professional, $199.99) a minute, and All-In-One 300 a minute for fundamentals, as search results quote its pricing page (the page itself needs a browser). | Every minute |
| Alpha Vantage | 25 requests a day. | Not published; PyQxel counts days from midnight UTC |
| Tiingo | 50 requests an hour, 1,000 a day, 500 different symbols a month, 1 GB of data a month. | Hourly; daily at midnight EST; monthly on the 1st at midnight EST. "We do not rate limit to minute or second." |
| EODHD | 20 calls a day (an intraday request costs 5 calls, fundamentals 10), plus a one-time bonus of 500 extra calls; 1,000 requests a minute. | Daily at midnight GMT, zeroed by the first request after it; every minute |

## Usage each provider reports

Settings shows each key's use against its plan's limits. Where a provider reports usage,
those figures count every use of the key, inside PyQxel or not; where it does not, PyQxel
shows its own count of the requests it sent, labeled as such (`app/data/usage.py`).
Checked against the providers' documentation on November 5, 2026:

| Provider | What it reports |
| --- | --- |
| EODHD | `GET /api/user` (costs no calls): `apiRequests` on `apiRequestsDate`, `dailyRateLimit`, `extraLimit`. Every answer carries `X-RateLimit-Limit` and `X-RateLimit-Remaining` for the current minute; a 429 carries `Retry-After`. |
| Finnhub | `X-Ratelimit-Limit`, `X-Ratelimit-Remaining`, and `X-Ratelimit-Reset` headers for the minute, as seen on live answers; its rate-limit page could not be read. PyQxel paces each key by the limit these headers give (92% of it for pages, 85% for the nightly refresh), so a paid plan's key is used at its own pace and the nightly refresh finishes, and costs, that much less. Until a key's first answer, it is paced as a free one. |
| Anthropic | `anthropic-ratelimit-requests-*`, `-input-tokens-*`, and `-output-tokens-*` headers (`limit`, `remaining`, and `reset` as an RFC 3339 time). They refill continuously. Monthly spend is shown only in its Console. |
| Tiingo | Nothing: no usage endpoint or headers. Its account page shows usage. |
| FMP | Not known: its documentation refuses automated reading. PyQxel counts its own requests. |
| Alpha Vantage | Nothing: no usage endpoint or headers. |

### Each part of the app's share of a key

Under each key in Settings, a dropdown lists the parts of the app that can use it (Stock
pages, Markets, Portfolio, Backtest, Claude's Picks, and ticker search), each with a
checkbox, on by default, and a daily limit on its requests. The browser tags each request
with its part (`X-PyQxel-Feature`), leaves out a key turned off for it, and sends that
part's limits (`X-PyQxel-Key-Limits`). The server counts each part's requests on each key
by the provider's own day (`provider_feature_usage`), leaves a key out once that part has
made its day's limit as each request starts, and names it in `X-PyQxel-Held-Back`, so the
error the page shows says why. The nightly refresh and scheduled picks run on the keys
stored for them, with their own limits, and are not counted.

A key that has used its plan's requests for the moment answers 429 with `Retry-After`, and
its message says which key, why (for Finnhub: everything PyQxel asks on the key shares its
requests a minute, including the nightly refresh while it runs), and when it works again.
The browser waits that long and tries once more by itself when it is a minute or less.

## Listings abroad

A symbol abroad carries its exchange's Yahoo-style suffix (SAP.DE, VOD.L, 7203.T), and
each provider names it its own way: EODHD as SAP.XETRA and VOD.LSE, Alpha Vantage as
SAP.DEX and VOD.LON. PyQxel asks a provider only for the exchanges it lists
(`app/data/fetcher.py: provider_covers`), so no request, nor any of a free plan's few, is
spent on one that cannot answer:

| Exchanges | EODHD | Alpha Vantage |
| --- | --- | --- |
| London, XETRA, Toronto, TSX Venture, Shanghai, Shenzhen | Yes | Yes |
| Lisbon | Yes | Yes |
| India (BSE) | No | Yes |
| Frankfurt, Paris, Amsterdam, Brussels, Swiss, Madrid, Nordic, Dublin, Vienna, Warsaw, Prague, Athens, Budapest, Cboe Canada, Mexico, São Paulo, Buenos Aires, Santiago, Korea, Taiwan, Jakarta, Thailand, Malaysia, Australia, Johannesburg | Yes | No |
| Tokyo, Hong Kong, India (NSE), Milan, Singapore, New Zealand, Istanbul, Tel Aviv, Saudi Arabia | No | No |

EODHD's list is from its exchange list, read with the free key; Alpha Vantage's from its
documentation and search (a free key quoted SAP.DEX and nothing for 7203.TYO). Prices
abroad are in the exchange's currency, London's in pence (GBX); a portfolio converts them
to dollars at the latest ECB reference rate.

A depositary receipt such as ASML trades in the US, in dollars, so the US providers cover
it. Finnhub's profile of one describes the home listing (ASML.AS, in euros), so PyQxel
keeps its name and industry, prices it in dollars, and converts its market cap.

## ETFs and funds

- **Prices:** Tiingo's free plan covers US ETFs, mutual funds, and money-market funds,
  30+ years, adjusted. Its company profile has no price, so the snapshot takes the price
  from the cheapest quote (Finnhub's free real-time quote covers ETFs).
- **Kind:** Tiingo's search tells a stock from an ETF or a mutual fund, and its fund names
  mark money-market funds, which the portfolio counts as cash.
- **What a fund holds and costs:** Alpha Vantage's free `ETF_PROFILE` gives net assets,
  expense ratio, yield, turnover, inception, leverage, sector weights, and holdings. A
  company's figures (market cap, sector, earnings, analyst ratings, insider trades) do not
  apply to a fund, and the app says so instead of showing blanks.
- **Screener:** each user's nightly refresh adds the ETFs on US exchanges from Finnhub's
  free symbol list, with Finnhub's free basic financials for each, one request per ETF.
  Once a week it checks all of them (about two more hours at 50 a minute); the other nights
  only those already in the user's universe (about 30 to 50 minutes), so a new or newly
  traded ETF joins within the week. Those that could clear the dollar volume floor even at
  their 52-week high are kept and share the price histories with the stocks.
- **Mutual funds:** Tiingo's free plan prices them (its last NAV) and gives their history;
  Finnhub's free plan refuses them. Their holdings come from their SEC filings (below).

## What funds hold: SEC N-PORT filings

Every US mutual fund and ETF reports its whole portfolio to the SEC on Form N-PORT, and
the SEC publishes the quarter-end reports about 60 days later. They are public, so PyQxel
reads them with no key and may show them to every account (`app/data/fund_filings.py`):

- **Ticker to fund:** the SEC's `company_tickers_mf.json` maps each share class to its fund
  ("series"); VOO and VFIAX are two classes of one fund. Unit investment trusts such as
  SPY do not file N-PORT.
- **Latest filing:** EDGAR's feed of the series' `NPORT-P` filings, then the filing's
  `primary_doc.xml`: every holding's name, CUSIP, value, share of net assets, kind of asset
  (stock, bond, mortgage-backed, cash) and issuer (company, Treasury, agency,
  municipality), and a bond's maturity and coupon. Holdings carry no tickers, so they are
  matched to listed companies by name.
- **Size and age:** about 0.5 MB for an S&P 500 fund, 20 MB and 17,000 holdings for a
  total bond market fund; parsed in under a second and summarized, the summary kept for a
  week. Tested November 6, 2026: VFIAX (as of June 30), FXAIX (August 31), and VBTLX
  (June 30, 98.8% bonds).

The same filings give the **S&P 500's and the Nasdaq-100's companies**, whose official
lists their owners license: IVV holds the S&P 500 and QQQM the Nasdaq-100 (SPY and QQQ are
unit investment trusts, which file none). A user's nightly refresh of one of those indexes
keeps only its companies (`app/data/index_members.py`). Names are matched without EDGAR's
state suffixes (`APPLIED MATERIALS INC /DE`), punctuation, and legal endings, run together
or in either order. Tested November 9, 2026: 492 of IVV's 503 companies (June 30) and 101
of QQQM's 102 (August 31) matched; those left out had changed their names since filing
(GE Aerospace, still General Electric at the SEC). The filing does not say which of a
company's listings it holds, so the refresh keeps as many of its listings as the fund holds
share classes, the most traded first (GOOG and GOOGL, not the GOOGM notes). A company that
joined or left the index shows up a quarter or two late.

**International stocks** in a user's nightly refresh are foreign companies listed in the US:
those whose latest report with the SEC is a 20-F or 40-F (a foreign private issuer's, as
behind every ADR), or whose financials are in a currency other than the dollar
(`app/db/screener.py: foreign_companies`). Both come from the weekly bulk rebuild of the
SEC's company facts, so they need no request of their own. Listings on exchanges abroad
are not included: Finnhub's free plan has no figures for them, and a ticker abroad a user
adds (7203.T) is refreshed through its company's US listing (TM) when Finnhub's and
Tiingo's searches find one.

From the holdings, PyQxel weights the companies' own figures into the fund's
(`app/stats/fund_stats.py`): P/E and price to sales as harmonic means, margins, return on
equity, growth, and market cap as weighted means, and sectors from the companies' SEC
industry codes. The figures come from the user's own screener where it has the company,
and otherwise from Finnhub's free basic financials for the 25 largest holdings.

## Keeping each user's price history

PyQxel keeps the daily prices it fetched on a user's own keys, for that user alone
(`app/db/price_store.py`), so a page or backtest does not spend a request on prices it
already has. The first fetch asks for ten years (or more, for the longest window), and
later fetches ask only for the days since, with some overlap to follow dividends and
splits. What the terms say about keeping data:

| Provider | Terms on keeping data |
| --- | --- |
| EODHD | "Non-Professional Users are permitted to store, manipulate, and analyze the data for private, non-commercial purposes." |
| Tiingo | Data "is for internal and personal use only. You may not redistribute the data in any form." Storing is not addressed; its terms page could not be read. |
| Finnhub | "All data must be deleted should your subscription to that data ends." Its free plan serves no history, so none is kept. |
| FMP | Not known: its terms page refuses automated reading. |
| Alpha Vantage | Not known: its terms could not be read. |

So stored prices are shown only to the user whose key fetched them, and only while their
requests carry a key for that provider; rows unread for 30 days are deleted, as is
everything when the user deletes it in Settings or deletes the account. Nightly screener
refreshes, which fetch thousands of histories, keep none.

## Paid plans that fill the gaps

These are monthly prices for individuals. Like the free plans, they are for personal use
only.

| Provider | Plan | Price a month | Adds |
| --- | --- | --- | --- |
| EODHD | Historian (EOD All-World) | $19.99, or $16.58 billed yearly | 30+ years of daily prices for stocks, ETFs, funds, indices, and FX on every exchange. A bulk call returns a whole exchange's day at once. Delayed quotes. |
| EODHD | Active trader (EOD + Intraday) | $29.99 | The above, plus intraday bars (1-minute US bars since 2004), a screener with market cap and sector, and technical indicators. |
| EODHD | Fundamentals | $59.99 | Sector, industry, market cap, and financials worldwide. Prices not included. |
| EODHD | All-In-One | $99.99 | Everything above. |
| Tiingo | Power | $30 ($50 for internal business use) | 110,000+ symbols a month and 10,000 requests an hour. Fundamentals beyond the Dow 30 are a separate add-on. |
| FMP | Starter | $29 _(unverified)_ | US stocks, 5 years of history, 300 requests a minute. |
| FMP | Premium | $69 _(unverified)_ | 30 years of history; UK and Canada. |
| FMP | Ultimate | $139 _(unverified)_ | Global coverage and bulk data. |
| Alpha Vantage | Premium | From $49.99 | Full adjusted history, intraday bars, and company overview. Real-time US data needs an extra entitlement. |
| Finnhub | Market data | From about $49.99 _(unverified)_ | Historical candles and more. |

## The cheapest order for each kind of data

For each kind of data, PyQxel should try the user's keys from cheapest to most expensive,
and use the first one that covers the request. "Free" means the provider's free plan.

| Kind of data | Order to try | With no paid key |
| --- | --- | --- |
| Daily, US stocks and ETFs | Tiingo (free) → FMP (free sample, or Starter) → EODHD (free 1 year, or Historian) → Alpha Vantage (free, 100 days unadjusted) | **Fully covered by a free Tiingo key**, within 500 symbols a month |
| Daily, mutual and money-market funds | Tiingo (free) → EODHD (free 1 year, or Historian) → Alpha Vantage (free, 100 days) | **Covered by Tiingo** |
| Daily, indices | EODHD (free 1 year, or Historian) → FMP (Starter or above) | **One year only.** For longer history, use an ETF that tracks the index (SPY for the S&P 500) through Tiingo |
| Daily, international | EODHD (free 1 year, or Historian $19.99) → Alpha Vantage (free, 100 days unadjusted) → FMP Premium or Ultimate → Finnhub (paid), each only for the exchanges it lists | **One year, at 20 requests a day,** on EODHD's exchanges. Full history needs EODHD Historian. Tokyo, Hong Kong, India (NSE), Milan, and Singapore have no free source |
| Intraday bars | Tiingo IEX (free) → EODHD Active trader ($29.99) → Alpha Vantage premium | **5-minute bars from Tiingo** (IEX's volume only). Other bar sizes and full volume need a paid key |
| Quotes, US | Finnhub (free, real time) → Tiingo (free) → FMP (free sample) → EODHD (free, delayed) → Alpha Vantage (free, end of day) | **Covered** |
| Quotes, international | EODHD (free, delayed; Historian for volume) → FMP (paid) → Alpha Vantage (free, on its exchanges) → Finnhub (paid) | 20 requests a day, then 25 |
| Kind of listing: stock, ETF, mutual, or money-market fund | FMP profile flags (free sample) → Tiingo search (free, any US listing; reused for a week) → EODHD search (free) | **Covered** |
| ETF and fund holdings and costs | Alpha Vantage `ETF_PROFILE` (free; reused for a day) for costs and current holdings, beside the SEC's N-PORT filings (public, no key) for every holding, the asset mix and bond figures | **Covered:** holdings for any mutual fund or ETF that files N-PORT, with no key; costs at one of Alpha Vantage's 25 requests a day per fund |
| Figures of a fund's companies (for holdings-weighted P/E, margins, growth, size, sectors) | The user's own screener (no request) → Finnhub basic financials (free, one request per company, the 25 largest) | **Covered** |
| Screener: the US ETFs and their metrics | Each user's own Finnhub key (symbol list, then basic financials per ETF) | **Covered for that user** |
| Company info, US: name, sector, industry | FMP profile (free sample) → Finnhub profile (free: industry, any US stock) → Tiingo meta (free: the Dow 30's sectors) → EODHD Fundamentals ($59.99) | **Name and industry covered**; a sector name for most stocks needs FMP's sample or a paid key |
| Market cap, US | Finnhub (free) → FMP (free sample) | **Covered** |
| News, company and market | Finnhub (free; one request per company, cached ten minutes) → Alpha Vantage (paid; one market-wide feed of the latest 1,000 articles, kept fifteen minutes, serves every company and the market at once) | **Covered by Finnhub.** Alpha Vantage's feed reaches the most talked-about companies only; a free Alpha Vantage key has no news, is asked at most once a day to find out, and not at all once known to be free |
| Company info, international | FMP (paid) → EODHD (Fundamentals $59.99 for sector and market cap; free search for name, currency, and type) → Alpha Vantage (free search) → Finnhub (paid) | **Name, exchange, and currency covered**; sector and market cap need a paid key |
| Exchange rates | ECB reference rates (public) → Federal Reserve H.10 rates (public) → FMP → Alpha Vantage | **Covered, with no key at all**, for every currency either publishes |
| Risk-free rate (cash interest in backtests) | US Treasury daily bill rates (public, since 2002) → Ken French's one-month rate (before 2002) | **Covered, with no key** |
| Sector and industry, US companies | SEC industry codes (public) → FMP → Finnhub → Tiingo → Alpha Vantage | **Covered, with no key**, for every SEC filer |
| A company's SEC filings | SEC EDGAR (public) | **Covered, with no key** |
| Screener: listings, sectors, financials | SEC EDGAR (public), shared by every account | **Covered** |
| Screener: market cap, returns, beta, volatility, volume, and ratios the SEC financials lack | Each user's own Finnhub key (basic financials, 1 request per stock, about 70 minutes for 4,000 stocks); the SEC financials come first for valuation, margins, and growth, since Finnhub's are wrong for banks | **Covered for that user** |
| Screener: daily prices for every stock (12-1 momentum, max drawdown, factor betas) | EODHD Historian ($19.99; a bulk call returns a whole exchange's day) → FMP Starter ($29; one request per stock) | **No free source** |

## What has no free source

| Missing | Cheapest paid fix |
| --- | --- |
| Index history beyond a year | EODHD Historian ($19.99). An index ETF on Tiingo is a free stand-in. |
| International prices beyond a year or 100 days, and international company info | EODHD Historian ($19.99) for prices; EODHD Fundamentals ($59.99) for company info |
| Any price in Tokyo, Hong Kong, India (NSE), Milan, Singapore, New Zealand, Istanbul, Tel Aviv, or Saudi Arabia | FMP Ultimate (about $139) or a Finnhub global plan; EODHD lists none of these exchanges on any plan. Free stand-in: the company's US listing, which Tiingo prices (Toyota's TM for 7203.T); PyQxel names it when it cannot price the listing abroad |
| Intraday bars other than Tiingo's 5-minute IEX bars, and full-market intraday volume | EODHD Active trader ($29.99) |
| Daily prices for the whole screened universe | EODHD Historian ($19.99) |
| Daily market caps for stocks outside the Dow 30 | Finnhub's free per-stock metrics give today's market cap; history needs a paid key |

## The articles behind the headlines

Finnhub and Alpha Vantage give a story's headline, a short summary, and a link. For the
stories that matter most (20 for a stock report; for portfolio reviews and Claude's
Picks, 40 to 120 shared among the stocks weighed, by how long the person will wait, and
the five most market-moving market and three deal stories), the article itself is given
to Claude as Markdown.

It is read by Anthropic's web fetch tool, on the user's own Anthropic key, as the web
search is: a cheaper model (Claude Sonnet 5.5 at low effort) fetches each link and
writes the article's facts, figures, and attributed statements in its own words, so
PyQxel's server never downloads the publisher's page and no article's text is copied.
Each article costs the user's key roughly two cents of tokens on Sonnet 5.5's prices. A
provider's redirecting link (Finnhub's) is first resolved by asking Finnhub where it
leads. A page behind a paywall, or one the tool cannot read, is left at its headline
and summary.

## Web search for Claude's reports

Stock reports, portfolio reviews, and Claude's Picks can let Claude search the web
first ("Let Claude search the web"), for what no key here supplies: the latest news,
guidance, analyst actions, and the economic backdrop. It is Anthropic's web search tool
(`web_search_20250305`, `app/ai/web.py`), run on the user's own Anthropic key, so it
follows the same rule as every other source: one user's key, one user's results.

- **Cost:** $10 per 1,000 searches on top of the tokens, billed by Anthropic to the
  user's key, and nothing for a search that fails. PyQxel allows up to
  `WEB_SEARCH_MAX_USES` (6) a report, so a few cents in all; the checkbox says so.
- **Plans:** every Anthropic API key, with no plan needed, unless the organization's
  admin turned web search off in the Claude Console. Then the report is written without
  it and says so (`WEB_FAILED_NOTICE`).
- **Terms:** Anthropic requires showing the sources of what a search returns. Only
  statements the API tied to a page are kept (`findings_in`), each listed under the
  report with its links, and the report cites them by number. Results are kept with the
  report, for its owner alone, like the rest of its data.
- **Default:** the box starts checked, and the person can uncheck it.
- **Without an Anthropic key:** the report can still be taken to claude.ai ("Use Claude
  outside the app"), which has its own web search. PyQxel makes no search for that
  prompt; it tells Claude whether it may search the web itself (the same checkbox), and
  to name the publisher and date of anything it finds, since there are no numbered
  findings outside the app.

## Retirement account contribution limits

The yearly limits for IRAs, employer plans, SIMPLE and SEP IRAs, and HSAs
(`app/data/contribution_limits.py`) come from the IRS's own documents: Notice 2026-66 and
news release IR-2026-136 for 2027 (published November 12, 2026, restating the 2026
figures), and Revenue Procedure 2026-24 for 2027's HSA limits. No provider here (Tiingo,
FMP, EODHD, Finnhub, Alpha Vantage) and no IRS service publishes them as data to fetch,
so they are entered by hand: add the next year each October or November, when the IRS
announces it (HSA limits come in the spring), and update `CHECKED_ON`. Once the calendar
year passes the newest year there, `contribution_limits()` sets `outdated` and the app
warns that the figures are likely too low.

## What Claude's Picks are given, and what it costs

Each list of picks is made from data that is nearly all on hand already:

- **The screener (no requests):** every candidate's figures, exchange, sector,
  industry, market cap rank, the end of its ratios' period, and its percentile within
  its sector on eight figures, plus each sector's medians, all read from the stored
  screener.
- **The S&P 500's members (keyless):** from the SEC's filings of a fund that tracks it,
  cached for a day and shared with the screener's S&P 500 choice.
- **Finnhub (the user's key):** the company news of the 30 stocks likeliest to be picked
  (`events_shortlist`), six at a time, and what the Markets page shows: one weekly
  earnings calendar per week, which also gives every candidate's next report date, the
  IPOs, the holidays, whether the market is open, and the news in all four of its
  categories (general, deals, currencies, and crypto). News comes first in the key's
  minute (`plan_requests`): every stock wanted gets its own, and the calendar takes the
  weeks left, at least four and up to the holding period or a quarter. On a free plan's 55
  a minute, picks get a full quarter's calendar, and a review of 40 holdings gets six
  weeks with every holding's news. FOMC meetings, from the Federal Reserve with no key,
  cover the whole period either way. Company news is cached for ten minutes and shared
  with the stock page, and the calendars for six hours with the Markets page. Portfolio
  reviews get the same, for up to 40 holdings and the person's horizon (a year when none
  is given).
- **Waiting for more (`wait_seconds`):** the person chooses how long they will wait,
  from none to fifteen minutes (five by default in the app). `plan_requests` spends that
  many minutes of the key's limit where each request adds the most, step by step: the
  calendar's first four weeks (every stock's report date in the next month); the news of
  the 25 likeliest picks or largest holdings; the signals (analysts' ratings, earnings
  surprises, insiders' trades, three requests each) of the top five; the rest of the
  calendar, up to a quarter; the other stocks' news; then their signals. Calendar weeks
  already cached cost nothing. On a free plan, no waiting gives 25 stocks' news, five
  stocks' signals, and six weeks of calendar; one minute, every stock's news and a
  quarter's calendar; five minutes, all of it for 60 picks or 40 holdings. Requests beyond
  a minute wait for the next one (`fetcher.finnhub_patience`) instead of being refused,
  and the notice says what more waiting would add. The copied prompt gathers the same.
- **What Claude is given of the news:** 480 company headlines shared evenly among the
  stocks whose news was fetched, from 6 to 20 each (10 holdings get 20, 30 picks
  candidates 16, 40 holdings 12, 60 picks candidates 8, and 100 holdings 6), the
  ones that matter most for each company's shares; and the most market-moving 80
  general, 30 deal, 20 currency, and 10 crypto headlines. They cost tokens, about 70 a
  headline, not requests: the articles are already fetched.
- **Alpha Vantage (a paid plan), without Finnhub:** the one shared news feed stands in for
  both the market's news and each company's, a single request however many companies.
  Without either key the picks are still made, and the context's notice says what is
  missing.

## Notes and gaps

- **FMP's free history covers a fixed sample of popular symbols.** The app's code comment
  says the free plan has no price history at all. That is wrong: the free plan has
  history, but only for symbols in the sample.
- **Tiingo's free plan includes fundamentals only for the Dow 30.** Its
  `fundamentals/meta` lookup gives any US ticker's name, but answers "Field not available
  for free/evaluation" for the sector and industry of stocks outside the Dow 30.
- **Tiingo's IEX feed has gaps since IEX changed its rules in February 2025.** Bid, ask,
  and last sale come back empty without a direct agreement with IEX. Tiingo's own last
  price and its 5-minute bars work.
- **Not read directly:**
  - FMP's pages refuse automated reading, and Finnhub's pricing pages are drawn by
    JavaScript. Their prices above come from third-party listings checked in October
    2026.
  - Alpha Vantage's and Tiingo's terms pages could not be read. The personal-use rule
    above comes from their pricing pages.
- **Commercial display of data:**

  | Provider | What it takes |
  | --- | --- |
  | EODHD | Enterprise plan, from $2,499 a month |
  | FMP | Through its sales team |
  | Finnhub | Written approval |
  | Tiingo | Its redistribution pricing |
  | Alpha Vantage | Through its sales team |

## Sources

- **Free-plan results:** tested with real free keys on November 2, 2026, and for ETFs,
  funds, and listings abroad on November 5, 2026. The request scripts are not kept in the
  repository.
- **EODHD's exchanges:** its `exchanges-list` endpoint, read with the free key.
- **Alpha Vantage:** [documentation](https://www.alphavantage.co/documentation/) for its
  exchange suffixes and `ETF_PROFILE`.
- **EODHD:** [pricing](https://eodhd.com/pricing),
  [commercial pricing](https://eodhd.com/commercial-pricing),
  [personal vs commercial use](https://eodhd.com/financial-apis/commercial-vs-personal-license-use),
  [terms](https://eodhd.com/financial-apis/terms-conditions),
  [User API](https://eodhd.com/financial-apis/user-api),
  [API limits](https://eodhd.com/financial-apis/api-limits)
- **Tiingo:** [pricing](https://www.tiingo.com/about/pricing),
  [general documentation](https://www.tiingo.com/documentation/general) (limits and resets),
  [IEX documentation](https://www.tiingo.com/documentation/iex),
  [fundamentals](https://www.tiingo.com/documentation/fundamentals)
- **Alpha Vantage:** [premium plans](https://www.alphavantage.co/premium/),
  [documentation](https://www.alphavantage.co/documentation/),
  [real-time data policy](https://www.alphavantage.co/realtime_data_policy/)
- **Finnhub:** [terms of service](https://finnhub.io/terms-of-service);
  prices from [TradingDataCompare](https://tradingdatacompare.com/providers/finnhub/)
  and [apicostcalc](https://apicostcalc.com/finnhub.html)
- **FMP:** prices from
  [APIs.io's copy of FMP's pricing page](https://apis.io/plans/financial-modeling-prep/financial-modeling-prep-plans-pricing/)
  and [Stockfit](https://developer.stockfit.io/blog/financial-modeling-prep-alternatives)
- **Anthropic:** [rate limits](https://platform.claude.com/docs/en/api/rate-limits)
- **European Central Bank:** [reference rates feed](https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml)
- **Keyless sources' terms** (read November 6, 2026):
  [FRED legal](https://fred.stlouisfed.org/legal/),
  [FRED API terms](https://fred.stlouisfed.org/docs/api/terms_of_use.html),
  [Federal Reserve Board disclaimer](https://www.federalreserve.gov/disclaimer.htm),
  [FiscalData API](https://fiscaldata.treasury.gov/api-documentation/),
  [Treasury interest rate files](https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics/interest-rate-xml-files),
  [SEC website policies](https://www.sec.gov/privacy.htm),
  [SEC developer resources](https://www.sec.gov/about/developer-resources),
  [BLS API FAQ](https://www.bls.gov/developers/api_faqs.htm),
  [BLS copyright](https://www.bls.gov/bls/linksite.htm),
  [World Bank dataset terms](https://www.worldbank.org/ext/en/legal/terms-conditions/datasets),
  [USAspending API](https://api.usaspending.gov/),
  [LDA API terms](https://lda.gov/api/tos/)
