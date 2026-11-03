# Market data sources

Which provider can supply each kind of market data PyQxel uses, on free and paid plans,
and in what order to try them. It was written to replace yfinance and Nasdaq's public
screener feed, which are unlicensed, with licensed sources.

Checked on **November 2, 2026**: every free plan was tested with a real free key, and
paid plans were read from the providers' pricing pages where those could be read.
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

## What yfinance and Nasdaq supply today

| Kind of data | Where PyQxel uses it | Source today |
| --- | --- | --- |
| Daily prices, US stocks | Charts, volatility, factors, backtests, portfolio models | yfinance when no key covers the symbol |
| Daily prices, ETFs | Same | yfinance (FMP's free plan refuses most ETFs) |
| Daily prices, mutual and money-market funds | Portfolio (e.g. VFIAX, SPAXX) | yfinance |
| Daily index levels (e.g. ^GSPC) | Stock page, comparisons | yfinance |
| Daily prices, international listings | Any non-US symbol | yfinance |
| Intraday bars (1–90 min) | Stock page chart, intraday backtests | yfinance |
| Quotes | Holdings, stock page | Finnhub (free plan, US); yfinance otherwise |
| Company info: name, sector, industry, market cap, type | Stock page, portfolio sectors, cash detection | FMP or Finnhub where covered; yfinance otherwise |
| Exchange rates | Converting foreign filers' financials to dollars | **Done:** the European Central Bank's reference rates since November 2, 2026; the user's own keys for currencies the ECB does not publish |
| Screener: market caps, sectors, industries | The nightly screener refresh | Nasdaq's public feed |
| Screener: about 13 months of daily prices per stock | Momentum, volatility, drawdown, factor betas | yfinance |
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
| Daily, international | Refused | None | Last 100 days, unadjusted (e.g. SHEL.LON) | Chinese A-shares only | **Any exchange, last 1 year** |
| Intraday bars | None | None | Premium | **5-minute bars from IEX** (IEX volume only) | None |
| Quote, US | Large-cap sample | **Yes, real time** | End of day only | Yes (Tiingo's last price) | Yes, 15–20 min delayed |
| Quote, international | Refused | None | Not tested | None | Delayed |
| Company info, US | Sector, industry and market cap for the sample (ETFs included) | **Industry and market cap, any US stock** | Premium | Name for any US stock; sector, industry and market cap only for the Dow 30 | None (paid add-on) |
| Exchange rates | Quote only | None | **Current rate**, plus 100 days of history | **Daily history, 5+ years** | Last 1 year |
| Screener: bulk market caps or sectors | Refused | One call per stock (see below) | None | None | None |
| Screener: per-stock metrics | None | **Yes: market cap, P/E, P/S, beta, 13/26/52-week returns, 3-month return volatility, average volume, 52-week high and low, margins, ROE** | None | None | None |

**Free-plan limits:**

| Provider | Limit |
| --- | --- |
| FMP | 250 requests a day. The audit plus a day of use reached it. |
| Finnhub | 60 requests a minute, no daily cap. |
| Alpha Vantage | 25 requests a day. |
| Tiingo | 500 different symbols a month, 50 requests an hour, 1,000 a day. |
| EODHD | 20 requests a day, plus a one-time bonus of 500. |

## Paid plans that fill the gaps

These are monthly prices for individuals. Like the free plans, they are for personal use
only.

| Provider | Plan | Price a month | Adds |
| --- | --- | --- | --- |
| EODHD | Historian (EOD All-World) | $19.99, or $16.58 billed yearly | 30+ years of daily prices for stocks, ETFs, funds, indices and FX on every exchange. A bulk call returns a whole exchange's day at once. Delayed quotes. |
| EODHD | Active trader (EOD + Intraday) | $29.99 | The above, plus intraday bars (1-minute US bars since 2004), a screener with market cap and sector, and technical indicators. |
| EODHD | Fundamentals | $59.99 | Sector, industry, market cap and financials worldwide. Prices not included. |
| EODHD | All-In-One | $99.99 | Everything above. |
| Tiingo | Power | $30 ($50 for internal business use) | 110,000+ symbols a month and 10,000 requests an hour. Fundamentals beyond the Dow 30 are a separate add-on. |
| FMP | Starter | $29 _(unverified)_ | US stocks, 5 years of history, 300 requests a minute. |
| FMP | Premium | $69 _(unverified)_ | 30 years of history; UK and Canada. |
| FMP | Ultimate | $139 _(unverified)_ | Global coverage and bulk data. |
| Alpha Vantage | Premium | From $49.99 | Full adjusted history, intraday bars and company overview. Real-time US data needs an extra entitlement. |
| Finnhub | Market data | From about $49.99 _(unverified)_ | Historical candles and more. |

## The cheapest order for each kind of data

For each kind of data, PyQxel should try the user's keys from cheapest to most expensive,
and use the first one that covers the request. "Free" means the provider's free plan.

| Kind of data | Order to try | With no paid key |
| --- | --- | --- |
| Daily, US stocks and ETFs | Tiingo (free) → FMP (free sample, or Starter) → EODHD (free 1 year, or Historian) → Alpha Vantage (free, 100 days unadjusted) | **Fully covered by a free Tiingo key**, within 500 symbols a month |
| Daily, mutual and money-market funds | Tiingo (free) → EODHD (free 1 year, or Historian) → Alpha Vantage (free, 100 days) | **Covered by Tiingo** |
| Daily, indices | EODHD (free 1 year, or Historian) → FMP (Starter or above) | **One year only.** For longer history, use an ETF that tracks the index (SPY for the S&P 500) through Tiingo |
| Daily, international | EODHD (free 1 year, or Historian $19.99) → Alpha Vantage (free, 100 days unadjusted) → FMP Premium or Ultimate | **One year, at 20 requests a day.** Full history needs EODHD Historian |
| Intraday bars | Tiingo IEX (free) → EODHD Active trader ($29.99) → Alpha Vantage premium | **5-minute bars from Tiingo** (IEX's volume only). Other bar sizes and full volume need a paid key |
| Quotes, US | Finnhub (free, real time) → Tiingo (free) → FMP (free sample) → EODHD (free, delayed) → Alpha Vantage (free, end of day) | **Covered** |
| Quotes, international | EODHD (free, delayed; Historian for volume) | 20 requests a day |
| Company info, US: name, sector, industry | FMP profile (free sample) → Finnhub profile (free: industry, any US stock) → Tiingo meta (free: the Dow 30's sectors) → EODHD Fundamentals ($59.99) | **Name and industry covered**; a sector name for most stocks needs FMP's sample or a paid key |
| Market cap, US | Finnhub (free) → FMP (free sample) | **Covered** |
| Company info, international | EODHD Fundamentals ($59.99) → FMP Premium | No free source |
| Exchange rates | ECB reference rates (public) → Tiingo FX (free) → Alpha Vantage (free) | **Covered, with no key at all** |
| Screener: listings, sectors, financials | SEC EDGAR (public), shared by every account | **Covered** |
| Screener: market cap, P/E, returns, beta, volatility, volume | Each user's own Finnhub key (basic financials, 1 request per stock, about 70 minutes for 4,000 stocks) | **Covered for that user** |
| Screener: daily prices for every stock (12-1 momentum, max drawdown, factor betas) | EODHD Historian ($19.99; a bulk call returns a whole exchange's day) → FMP Starter ($29; one request per stock) | **No free source** |

## What has no free source

| Missing | Cheapest paid fix |
| --- | --- |
| Index history beyond a year | EODHD Historian ($19.99). An index ETF on Tiingo is a free stand-in. |
| International prices beyond a year or 100 days, and international company info | EODHD Historian ($19.99) for prices; EODHD Fundamentals ($59.99) for company info |
| Intraday bars other than Tiingo's 5-minute IEX bars, and full-market intraday volume | EODHD Active trader ($29.99) |
| Daily prices for the whole screened universe | EODHD Historian ($19.99) |
| Daily market caps for stocks outside the Dow 30 | Finnhub's free per-stock metrics give today's market cap; history needs a paid key |

## Notes and gaps

- **FMP's free history covers a fixed sample of popular symbols.** The app's code comment
  says the free plan has no price history at all. That is wrong: the free plan has
  history, but only for symbols in the sample.
- **Tiingo's free plan includes fundamentals only for the Dow 30.** Its
  `fundamentals/meta` lookup gives any US ticker's name, but answers "Field not available
  for free/evaluation" for the sector and industry of stocks outside the Dow 30.
- **Tiingo's IEX feed has gaps since IEX changed its rules in February 2025.** Bid, ask
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

- **Free-plan results:** tested with real free keys on November 2, 2026. The request
  script is not kept in the repository.
- **EODHD:** [pricing](https://eodhd.com/pricing),
  [commercial pricing](https://eodhd.com/commercial-pricing),
  [personal vs commercial use](https://eodhd.com/financial-apis/commercial-vs-personal-license-use),
  [terms](https://eodhd.com/financial-apis/terms-conditions)
- **Tiingo:** [pricing](https://www.tiingo.com/about/pricing),
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
- **European Central Bank:** [reference rates feed](https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml)
