// Plain-language explanations of every stat, model and chart in the app, shown by the
// "i" beside each (components/InfoTip). Every new stat, model or chart gets an entry here.
//
// Ranges are rules of thumb for a typical US stock or a long-only portfolio; they vary by
// industry, market and era, and are a starting point for judgement, not a verdict.

export interface Info {
  /** What it is called. */
  title: string
  /** What it measures, for someone new to investing. */
  what: string
  /** What a reassuring value looks like, where there is one. */
  good?: string
  /** What a worrying value looks like, where there is one. */
  bad?: string
  /** A caveat or context. */
  note?: string
}

const entries = {
  // ── Prices and returns ──
  price: {
    title: 'Price',
    what: 'The latest price of one share, adjusted for splits.',
    note: 'A high or low share price says nothing about whether a stock is cheap: compare valuation ratios such as P/E instead.',
  },
  marketCap: {
    title: 'Market cap',
    what: 'The total value of all the company’s shares: share price times shares outstanding.',
    note: 'Large caps are roughly above $10 billion, mid caps $2–10 billion, small caps below $2 billion. Smaller companies tend to swing more in price.',
  },
  enterpriseValue: {
    title: 'Enterprise value',
    what: 'What it would cost to buy the whole business: market cap plus debt, minus cash.',
    note: 'Used in place of market cap when comparing companies with very different amounts of debt.',
  },
  periodReturn: {
    title: 'Return over the window',
    what: 'How much the price has changed over the chosen history, including dividends reinvested (prices are adjusted for them).',
    note: 'Compare it with a broad index such as the S&P 500 over the same window, which has averaged roughly 10% a year over the long run.',
  },
  totalReturn: {
    title: 'Total return',
    what: 'How much $1 invested at the start grew or shrank to by the end, after trading costs.',
    good: 'Above what simply holding (the benchmark) earned, with no larger drawdowns.',
    bad: 'Below the benchmark: the strategy did worse than doing nothing.',
  },
  annualizedReturn: {
    title: 'Annualized return',
    what: 'The total return expressed as a steady yearly rate (compound annual growth), so windows of different lengths compare.',
    good: 'Roughly 7–10% a year or more is in line with stocks’ long-run average.',
    note: 'Over less than a year this is not shown: annualizing a few months exaggerates it.',
  },
  week52Range: {
    title: '52-week range',
    what: 'The lowest and highest price over the past year.',
    note: 'Where today’s price sits in the range shows how far it has run up or fallen; it is not a forecast.',
  },
  week52Return: {
    title: '52-week return',
    what: 'The price change over the past year, and how it compares with the S&P 500 over the same year.',
    good: 'Ahead of the S&P 500.',
    bad: 'Far behind the market can signal trouble, or just an out-of-favour sector.',
  },
  averageVolume: {
    title: 'Average volume',
    what: 'How many shares trade on a typical day, averaged over three months.',
    good: 'Millions of shares a day: easy to buy and sell near the quoted price.',
    bad: 'Thinly traded stocks (under about 100,000 shares a day) can be costly to trade, with wide gaps between buy and sell prices.',
  },
  avgDollarVolume: {
    title: 'Average daily dollar volume',
    what: 'The value of shares traded on a typical day.',
    good: 'Above about $5 million a day for easy trading.',
    bad: 'Below about $1 million a day: buying or selling can move the price.',
  },
  return1m: {
    title: '1-month return',
    what: 'The price change over the last month.',
    note: 'Very short-term moves often partly reverse; they say little on their own.',
  },
  return6m: {
    title: '6-month return',
    what: 'The price change over the last six months.',
  },
  momentum12_1: {
    title: 'Momentum (12–1)',
    what: 'The return over the past twelve months, leaving out the most recent month.',
    note: 'Research finds that recent winners have tended to keep outperforming for a while (momentum). The last month is left out because very recent moves tend to reverse.',
  },

  // ── Risk ──
  volatility: {
    title: 'Volatility',
    what: 'How much the price swings, as the annualized standard deviation of daily returns. About two years in three, the yearly return lands within one volatility of its average.',
    good: 'Under about 20% a year is calm for a single stock; a broad index is typically 15–20%.',
    bad: 'Above about 40% is very volatile: large swings in both directions are common.',
  },
  volatilityNow: {
    title: 'Volatility now',
    what: 'Today’s expected volatility from a GARCH model, which weighs recent swings more heavily than old ones.',
    good: 'Below the stock’s long-run level: a calm period.',
    bad: 'Well above its long-run level: the stock is in a turbulent spell.',
  },
  garchModel: {
    title: 'GARCH volatility model',
    what: 'A statistical model of how volatility changes over time: big moves tend to be followed by big moves, and turbulence fades back toward a long-run level.',
    note: 'It forecasts how much the price may swing, not which direction. It is fit to five years of daily returns.',
  },
  garchForecast: {
    title: 'Volatility forecast',
    what: 'The GARCH model’s expected volatility over the coming trading days.',
    note: 'Forecasts drift toward the long-run level; the further ahead, the closer to it.',
  },
  realizedVolatility: {
    title: 'Realized volatility',
    what: 'The volatility actually measured from daily returns over the chosen window.',
    note: 'Compare it with the forecast: a forecast well above recent realized volatility means the model expects things to get bumpier.',
  },
  longRunVolatility: {
    title: 'Long-run volatility',
    what: 'The level the GARCH model expects volatility to settle at over time. The half-life is how many days a shock takes to fade halfway back.',
    note: 'A short half-life (a few weeks) means turbulence passes quickly; a long one means it lingers.',
  },
  maxDrawdown: {
    title: 'Max drawdown',
    what: 'The largest fall from a peak to a later low, before a new high: the worst loss someone buying at the top would have seen.',
    good: 'Smaller (closer to 0%) than the benchmark’s.',
    bad: 'Falls beyond about −30% are severe; −50% needs a +100% gain just to break even.',
  },
  drawdownTrough: {
    title: 'Drawdown trough',
    what: 'The date the largest drawdown hit bottom.',
  },
  recovered: {
    title: 'Recovered',
    what: 'When the value climbed back to its previous peak after the largest drawdown.',
    bad: '“Not yet” means it is still below that peak.',
  },
  beta: {
    title: 'Beta',
    what: 'How much the stock tends to move with the market. A beta of 1 moves in step with it; 1.5 tends to move 50% more; 0.5 half as much.',
    good: 'Below 1 for a steadier, more defensive holding.',
    bad: 'Well above 1 means larger losses when the market falls (and larger gains when it rises).',
  },
  sharpe: {
    title: 'Sharpe ratio',
    what: 'Return earned per unit of risk taken: the return above the risk-free rate, divided by volatility.',
    good: 'Above 1 is good, above 2 excellent. Stocks overall have averaged roughly 0.4–0.6 over the long run.',
    bad: 'Below 0 means it earned less than cash.',
  },
  sortino: {
    title: 'Sortino ratio',
    what: 'Like the Sharpe ratio, but it only counts downside swings as risk, since upside surprises are welcome.',
    good: 'Above 1 is good, above 2 excellent.',
    bad: 'Below 0 means it earned less than cash.',
  },
  annualizedVolatility: {
    title: 'Annualized volatility',
    what: 'How much returns swung, as a yearly standard deviation.',
    good: 'Lower than the benchmark’s for a similar return.',
  },

  // ── Factor models ──
  factorModel: {
    title: 'Factor exposures',
    what: 'A regression explaining the stock’s returns by a few well-known market forces (factors): the market as a whole (Mkt-RF), small vs. large companies (SMB), cheap vs. expensive “value” stocks (HML), and recent winners vs. losers (Mom).',
    note: 'A bar’s whisker shows the 95% range; one that crosses zero means that exposure could be nothing.',
  },
  marketBeta: {
    title: 'Market beta',
    what: 'How strongly the stock moves with the overall market, from the factor regression. 1 means in step with the market.',
    good: 'Around 1 or below for a steadier holding.',
    bad: 'Above about 1.5 means it tends to amplify market falls.',
  },
  sizeBeta: {
    title: 'Size beta (SMB)',
    what: 'Positive when the stock behaves like a small company, negative like a large one.',
  },
  valueBeta: {
    title: 'Value beta (HML)',
    what: 'Positive when the stock behaves like a cheap “value” stock, negative like a pricier “growth” stock.',
  },
  momentumBeta: {
    title: 'Momentum beta',
    what: 'Positive when the stock moves with recent winners, negative with recent losers.',
  },
  profitabilityBeta: {
    title: 'Profitability beta (RMW)',
    what: 'Positive when the stock behaves like highly profitable companies, negative like weakly profitable ones.',
  },
  investmentBeta: {
    title: 'Investment beta (CMA)',
    what: 'Positive when the stock behaves like companies that invest conservatively, negative like ones expanding aggressively.',
  },
  factorBeta: {
    title: 'Factor beta',
    what: 'How strongly returns move with this factor. Zero means no relationship.',
    note: 'With a t-statistic below about 2 in size, the estimate is not reliably different from zero.',
  },
  alpha: {
    title: 'Alpha',
    what: 'Return the factors do not explain, per year: in principle, the skill or luck beyond market exposures.',
    good: 'Positive, with a t-statistic above about 2, which is unlikely to be pure luck.',
    bad: 'Negative alpha means it lagged what its exposures alone would have earned.',
    note: 'Alpha is a record of the past, and noisy; it rarely persists.',
  },
  rSquared: {
    title: 'R²',
    what: 'The share of the return swings the factors explain.',
    note: 'High (above about 70%) means the stock mostly moves with broad market forces; low means company-specific news drives it more.',
  },
  factorRSquared: {
    title: 'Factor R²',
    what: 'The share of the stock’s return swings explained by the market, size, value and momentum factors.',
    note: 'Low values mean company-specific news dominates, which diversifies better in a portfolio.',
  },
  systematicRisk: {
    title: 'Systematic risk',
    what: 'The share of the stock’s risk that comes from broad market forces, which diversification cannot remove. The rest is idiosyncratic: specific to the company.',
    note: 'Idiosyncratic risk shrinks as you hold more, unrelated stocks; systematic risk does not.',
  },
  tStat: {
    title: 't-statistic',
    what: 'How many standard errors an estimate is from zero.',
    good: 'Above about 2 in size: the estimate is unlikely to be zero by chance.',
  },
  standardError: {
    title: 'Standard error',
    what: 'How uncertain an estimate is. Roughly, the true value lies within two standard errors of the estimate 95% of the time.',
  },
  pValue: {
    title: 'p-value',
    what: 'The chance of an estimate at least this far from zero if the true value were zero.',
    good: 'Below 0.05 is the usual bar for a real effect.',
  },

  // ── Valuation ──
  pe: {
    title: 'P/E ratio',
    what: 'Price per share divided by the last twelve months’ earnings per share: what investors pay for each dollar of profit.',
    good: 'Below the industry’s typical level can mean cheap. The S&P 500 has averaged roughly 15–20 over the long run.',
    bad: 'Very high (above about 40) prices in a lot of future growth. Negative means a loss.',
    note: 'Compare within an industry: fast-growing sectors normally trade at higher multiples.',
  },
  forwardPe: {
    title: 'Forward P/E',
    what: 'Price divided by analysts’ estimate of next year’s earnings per share.',
    note: 'Below the trailing P/E means earnings are expected to grow. Estimates can be wrong.',
  },
  peg: {
    title: 'PEG ratio',
    what: 'The P/E divided by the earnings growth rate, to judge whether a high P/E is backed by growth.',
    good: 'Around 1 or below.',
    bad: 'Well above 2: the price outruns the growth.',
  },
  priceSales: {
    title: 'Price / sales',
    what: 'Market cap divided by a year’s revenue. Useful for companies without profits yet.',
    good: 'Below about 2 is modest for most industries.',
    bad: 'Above about 10 needs very high growth or margins to justify.',
  },
  priceBook: {
    title: 'Price / book',
    what: 'Market cap divided by the accounting value of the company’s assets minus its debts.',
    good: 'Near or below 1 can mean cheap, especially for banks and insurers.',
    note: 'Less meaningful for software and brand-driven companies, whose main assets are not on the balance sheet.',
  },
  evEbitda: {
    title: 'EV / EBITDA',
    what: 'Enterprise value divided by earnings before interest, taxes, depreciation and amortization: a valuation that works across companies with different debts.',
    good: 'Below about 10 is often considered reasonable.',
    bad: 'Above about 20 is expensive unless growth is high.',
  },
  priceFcf: {
    title: 'Price / free cash flow',
    what: 'Market cap divided by a year’s free cash flow: the cash left after running and investing in the business.',
    good: 'Below about 20.',
    bad: 'Very high or negative: little cash is being generated for shareholders.',
  },
  fcfYield: {
    title: 'Free cash flow yield',
    what: 'A year’s free cash flow as a percentage of market cap: the cash return the business generates on its price.',
    good: 'Above about 5%.',
    bad: 'Negative: the company is burning cash.',
  },

  // ── Profitability and growth ──
  grossMargin: {
    title: 'Gross margin',
    what: 'The share of revenue left after the direct cost of making the product or service.',
    good: 'Above about 40%; software often exceeds 70%.',
    bad: 'Below about 20%, typical of retail and commodities, leaves little room for error.',
  },
  operatingMargin: {
    title: 'Operating margin',
    what: 'The share of revenue left after all operating costs, before interest and taxes.',
    good: 'Above about 15%.',
    bad: 'Negative: the core business loses money.',
  },
  netMargin: {
    title: 'Net margin',
    what: 'The share of revenue that ends up as profit after every cost, interest and tax.',
    good: 'Above about 10%.',
    bad: 'Negative: the company made a loss.',
  },
  roe: {
    title: 'Return on equity',
    what: 'Profit as a percentage of shareholders’ equity: how well the company turns shareholders’ money into profit.',
    good: 'Above about 15%.',
    bad: 'Below about 5%, or boosted by heavy debt (check debt / equity).',
  },
  roa: {
    title: 'Return on assets',
    what: 'Profit as a percentage of everything the company owns.',
    good: 'Above about 5%.',
  },
  revenueGrowth: {
    title: 'Revenue growth',
    what: 'How much sales grew over the last twelve months compared with the twelve before.',
    good: 'Above about 10% a year is strong.',
    bad: 'Negative: sales are shrinking.',
  },
  revenueGrowth5y: {
    title: 'Revenue growth, five years',
    what: 'Average yearly sales growth over five years, which smooths out one-off years.',
    good: 'Above about 10% a year.',
  },
  epsGrowth: {
    title: 'EPS growth',
    what: 'How much earnings per share grew over the last twelve months.',
    good: 'Steady, positive growth.',
    bad: 'Negative or erratic. One-off gains or charges can distort it.',
  },
  earningsGrowth: {
    title: 'Earnings growth',
    what: 'How much profit grew over the last twelve months compared with the twelve before.',
    note: 'One-off gains or charges can distort it; check several years.',
  },

  // ── Dividends and balance sheet ──
  dividendYield: {
    title: 'Dividend yield',
    what: 'A year’s dividends as a percentage of the share price.',
    good: 'About 2–5% is typical for dividend payers.',
    bad: 'Above about 8% can signal the market expects a cut.',
  },
  dividendTtm: {
    title: 'Dividend',
    what: 'Dividends paid per share over the last twelve months.',
  },
  payoutRatio: {
    title: 'Payout ratio',
    what: 'The share of profit paid out as dividends.',
    good: 'Below about 60% leaves room to keep paying in a bad year.',
    bad: 'Above 100%: paying out more than it earns, which cannot last.',
  },
  dividendGrowth5y: {
    title: 'Dividend growth, five years',
    what: 'Average yearly dividend growth over five years.',
    good: 'Steady growth above inflation.',
  },
  debtEquity: {
    title: 'Debt / equity',
    what: 'Debt divided by shareholders’ equity: how much the company relies on borrowing.',
    good: 'Below about 1.',
    bad: 'Above about 2 adds risk if business slows or rates rise. Banks and utilities normally run higher.',
  },
  currentRatio: {
    title: 'Current ratio',
    what: 'Assets that turn into cash within a year, divided by bills due within a year.',
    good: 'Between about 1.5 and 3.',
    bad: 'Below 1: it may struggle to pay near-term bills.',
  },
  quickRatio: {
    title: 'Quick ratio',
    what: 'Like the current ratio, but leaves out inventory, which can be slow to sell.',
    good: 'Above about 1.',
    bad: 'Well below 1: reliant on selling inventory to pay bills.',
  },
  interestCoverage: {
    title: 'Interest coverage',
    what: 'Operating profit divided by interest owed: how many times over it can pay its interest.',
    good: 'Above about 5.',
    bad: 'Below about 2: interest eats much of the profit.',
  },

  // ── Analysts, earnings and insiders ──
  analystsAtBuy: {
    title: 'Analysts at buy',
    what: 'The share of Wall Street analysts covering the stock who rate it a buy or strong buy.',
    note: 'Analysts lean toward buy ratings; a change in the share over time says more than its level.',
  },
  nextEarnings: {
    title: 'Next earnings',
    what: 'The date of the next quarterly earnings report, and analysts’ average estimate of earnings per share.',
    note: 'Prices often move sharply on report days.',
  },
  epsSurprise: {
    title: 'EPS surprise',
    what: 'How far the latest reported earnings per share came in above or below analysts’ average estimate.',
    good: 'Positive: a beat.',
    bad: 'Negative: a miss, which often weighs on the price.',
  },
  insiders: {
    title: 'Insider trades',
    what: 'Shares company executives and directors bought and sold in the open market, from their SEC filings.',
    note: 'Insiders sell for many reasons (taxes, diversification); buying with their own money is the stronger signal.',
  },
  insiderSentiment: {
    title: 'Insider sentiment',
    what: 'A score from −100 (insiders only sold) to +100 (insiders only bought), based on how their holdings changed this month.',
    good: 'Positive: insiders added to their holdings.',
    note: 'Routine selling makes mildly negative scores common.',
  },

  // ── Public records ──
  lobbying: {
    title: 'Lobbying',
    what: 'Money the company disclosed spending on federal lobbying, by year.',
    note: 'Shows how much it depends on government policy. The current year is incomplete: disclosures lag by weeks.',
  },
  federalAwards: {
    title: 'Federal awards',
    what: 'Federal contracts and grants awarded to the company over the past year.',
    note: 'Large awards relative to revenue mean it depends on government customers.',
  },
  patents: {
    title: 'Patents',
    what: 'US patents and applications published for the company over the past year.',
    note: 'A rough sign of research activity; coverage is incomplete, so a low count proves little.',
  },
  secFilings: {
    title: 'SEC filings',
    what: 'Filings with the US Securities and Exchange Commission over the last 90 days. 8-Ks report material events; Forms 4 and 144 report insider trades.',
    note: 'A burst of 8-Ks often accompanies news worth reading.',
  },

  // ── News ──
  newsChart: {
    title: 'News coverage',
    what: 'How many news articles mentioned the company each day over the last two weeks.',
    note: 'A spike usually marks an event; read the headlines that day.',
  },
  newsLastDay: {
    title: 'Articles in the last 24 hours',
    what: 'News articles about the company published in the last day.',
  },
  newsLastWeek: {
    title: 'Articles in the last 7 days',
    what: 'News articles about the company published in the last week.',
  },
  newsPerDay: {
    title: 'Articles per day',
    what: 'The average daily number of articles over the last two weeks, a baseline for spotting unusual coverage.',
  },
  newsBusiestDay: {
    title: 'Busiest day',
    what: 'The day with the most coverage in the last two weeks.',
  },

  // ── Charts ──
  priceChart: {
    title: 'Price chart',
    what: 'The closing price over time, adjusted for splits and dividends so the line shows the return an investor actually got.',
    note: 'Hold Ctrl (or ⌘) and scroll to zoom.',
  },
  volatilityChart: {
    title: 'Volatility chart',
    what: 'How much the stock has been swinging over time, as annualized volatility, with the model’s forecast ahead.',
    note: 'Spikes mark turbulent spells, which tend to fade over the following weeks.',
  },
  equityChart: {
    title: 'Equity curve',
    what: 'How $1 invested at the start would have grown, for the strategy and for the benchmark, after costs.',
    good: 'A curve that ends above the benchmark’s with shallower dips.',
    note: 'Backtests show the past; a strategy tuned to one history often does worse on the next.',
  },

  // ── Backtests ──
  backtestPerformance: {
    title: 'Performance',
    what: 'The strategy’s return, risk and drawdowns compared with the benchmark over the same window.',
    note: 'Judge return and risk together: a higher return with much deeper drawdowns may not be better.',
  },
  attribution: {
    title: 'Return attribution',
    what: 'A regression of the strategy’s daily returns on market factors, separating returns from simple market, size, value or momentum exposure (betas) from what remains (alpha).',
    note: 'A strategy whose return is all beta could be had more cheaply with an index fund.',
  },
  rankIc: {
    title: 'Rank IC',
    what: 'Information coefficient: how well the ranking predicted the next period’s winners, as the correlation between each stock’s rank and its later return, averaged over every rebalance.',
    good: 'Above about 0.05 with a t-statistic above about 2 is a useful signal.',
    bad: 'Around zero or negative: the ranking did not predict returns.',
  },
  rankingScore: {
    title: 'Ranking score',
    what: 'The latest value of the signal each stock is ranked by.',
  },
  weightNow: {
    title: 'Weight now',
    what: 'The share of the portfolio the strategy holds in this stock as of the last rebalance.',
  },
  heldLong: {
    title: 'Held long',
    what: 'The share of rebalances at which the stock was bought.',
  },
  heldShort: {
    title: 'Held short',
    what: 'The share of rebalances at which the stock was sold short.',
  },
  contribution: {
    title: 'Contribution',
    what: 'This holding’s share of the strategy’s total return, after its trading costs.',
    note: 'Contributions add up to the strategy’s arithmetic return.',
  },
  averageWeight: {
    title: 'Average weight',
    what: 'The average share of capital the strategy put in this symbol over the backtest. Negative means it was, on average, sold short.',
  },
  timeHeld: {
    title: 'Held',
    what: 'The share of days the strategy held any position in this symbol.',
  },
  trades: {
    title: 'Trades',
    what: 'Days on which the position changed. Each trade pays the trading cost, so frequent trading eats into returns.',
  },
  rankedFrom: {
    title: 'Ranked from',
    what: 'The first date the stock had enough history to be ranked.',
  },

  // ── Portfolio ──
  holdingWeight: {
    title: 'Weight',
    what: 'The holding’s share of the portfolio’s value at the latest prices.',
    bad: 'One holding above about 20–25% concentrates a lot of risk in a single company.',
  },
  moneyMarketCash: {
    title: 'Cash (money-market fund)',
    what: 'A money-market fund, such as SPAXX or VMFXX, holds short-term government debt and keeps its price at $1 a share; its return comes as interest paid out. PyQxel treats it as cash: valued at $1 a share, held at a steady value in simulations, and left out of how the holdings move together, since its price never moves.',
    note: 'The fund’s interest, recently about 3–5% a year, is not included in the simulation, so the cash share slightly understates what the portfolio would earn.',
  },
  leftOutHoldings: {
    title: 'Holdings left out',
    what: 'Holdings this analysis could not use, and why: usually a recent listing with less than about ten months (201 trading days) of prices in the window, or one whose prices could not be fetched. The figures describe the rest of the portfolio.',
    good: 'Nothing left out, or only a small share of the portfolio.',
    bad: 'A large share left out: the results then say little about the portfolio as a whole.',
    note: 'In the simulation, the holdings kept are scaled up in proportion to stand in for those left out. Choosing a longer history does not help a recent listing; it only becomes usable once it has traded long enough.',
  },
  historyCut: {
    title: 'History lost to late listings',
    what: 'The simulation and the co-movement models need every holding on every day, so they start when the most recently listed holding started trading. This sets how much of the other holdings’ history one late listing may cost before it is left out instead.',
    good: 'Around 25–50%: recent listings are left out rather than shrinking everyone’s history to a few months.',
    bad: '“Keep all” with a holding that listed a few months ago: every figure then rests on that short window, which may miss whole market regimes such as a sell-off.',
    note: 'Measured against the holding with the longest history in the chosen window. Holdings left out are listed with how much history they have.',
  },
  monteCarlo: {
    title: 'Simulated outcomes (Monte Carlo)',
    what: 'Thousands of possible future paths for the portfolio, drawn from how the holdings’ returns behaved and moved together in the past. The bands show the range of outcomes.',
    note: 'It assumes the future resembles the past; real outcomes can fall outside every band.',
  },
  simulationPercentiles: {
    title: 'Percentiles',
    what: 'The 5th percentile is the outcome only 5% of simulated paths did worse than; the median (50th) is the middle path; the 95th, one only 5% beat.',
  },
  expectedReturn: {
    title: 'Expected return',
    what: 'The average return across all simulated paths over the horizon.',
    note: 'Rare big gains can pull the average above what most paths earn; the median is more typical.',
  },
  medianReturn: {
    title: 'Median return',
    what: 'The middle outcome: half the simulated paths did better, half worse.',
  },
  chanceOfLoss: {
    title: 'Chance of a loss',
    what: 'The share of simulated paths that end below the starting value.',
    good: 'Lower is safer. Over one month, 30–45% is normal for a stock portfolio.',
  },
  valueAtRisk: {
    title: 'Value at Risk (95%)',
    what: 'A loss the portfolio exceeds in only 5% of simulated paths: in 19 cases out of 20, the loss is smaller.',
    bad: 'A larger figure means more money at risk in a bad (but not the worst) case.',
  },
  expectedShortfall: {
    title: 'Expected shortfall (95%)',
    what: 'The average loss in the worst 5% of simulated paths: how bad the bad cases are, on average.',
    note: 'Always at least as large as Value at Risk; a big gap between them means a long tail of severe losses.',
  },
  medianMaxDrawdown: {
    title: 'Median max drawdown',
    what: 'The typical worst peak-to-trough fall along a simulated path.',
  },
  copula: {
    title: 'How the holdings move together (copulas)',
    what: 'A model of how the holdings’ returns move together, in normal times and in extremes. It compares a Gaussian copula, which treats crashes as unrelated, with a Student-t copula, which lets holdings crash together.',
    note: 'If the Student-t fits better, diversification may fail you exactly when markets fall.',
  },
  kendallTau: {
    title: 'Kendall’s tau',
    what: 'How consistently two holdings move in the same direction, from −1 (always opposite) through 0 (unrelated) to +1 (always together).',
    good: 'Low or negative values between holdings diversify the portfolio.',
    bad: 'Above about 0.5: the two largely move as one.',
  },
  ownKendallTau: {
    title: 'Kendall’s tau over the pair’s own history',
    what: 'The same rank correlation, measured over every day these two holdings both traded, rather than only the days every holding in the portfolio traded. When one holding listed recently, the main figure has to start when it did; this one can use the pair’s longer shared history.',
    note: 'Pairs measured over different stretches of time are not strictly comparable, so the heatmap and the copula models use the shared window; read this column as a check on whether the shorter window changed the picture.',
  },
  tailDependence: {
    title: 'Tail dependence',
    what: 'The chance that one holding has an extreme day given the other does: how often they crash (lower tail) or soar (upper tail) together.',
    bad: 'High lower-tail dependence means losses arrive together, when diversification matters most.',
  },
  shareOfVariance: {
    title: 'Share of variance',
    what: 'How much of the stock’s return swings this factor accounts for. The shares of all factors plus the unexplained, company-specific part add up to 100%.',
  },

  // ── AI ──
  aiReport: {
    title: 'AI research report',
    what: 'Claude reads the stock’s prices, statistics, financial statements, filings and news, and writes an investment thesis or risk summary citing those figures.',
    note: 'An AI’s reading of data, not advice: check the figures it cites and what it says it could not see.',
  },
  portfolioReview: {
    title: 'AI portfolio review',
    what: 'Claude reviews your saved holdings, where the risk comes from and how well they diversify, and recommends specific changes, which the app turns into exact orders.',
    note: 'An AI’s recommendation from past prices, not a licensed adviser’s; check taxes, costs and prices before trading.',
  },
  reviewHistory: {
    title: 'Price history to measure',
    what: 'How many years of daily prices the review’s statistics are measured over: each holding’s return, volatility, drawdown and correlations. It is not how far ahead Claude looks. Pick a preset or choose Custom for any length from 12 to 120 months.',
    good: 'Two to five years usually spans both rising and falling markets.',
    bad: 'One year can miss a whole market regime, such as a sell-off; ten years may describe companies that have since changed.',
    note: 'Holdings listed more recently than the window starts shorten it for all of them, or are left out.',
  },
  fractionalShares: {
    title: 'Fractional order',
    what: 'An order for part of a share, used when whole shares would round the trade away, or to sell a holding of less than one share in full.',
    note: 'Not every broker trades fractions of every security. Some, including Fidelity, accept fractional limit orders only as day orders, and allow no stop or trailing-stop orders on fractions; if yours will not take one, trade the fraction at market or round to whole shares.',
  },
  stance: {
    title: 'Stance',
    what: 'Claude’s overall view of the stock from the data it was given: bearish (expects it to do poorly), neutral, or bullish (expects it to do well).',
    note: 'A reading of past data, not a forecast you can bank on; weigh it against the counterpoints.',
  },
  conviction: {
    title: 'Conviction',
    what: 'How strongly the evidence backs the stance, from low to high. Low conviction means the case is finely balanced or the data are thin.',
  },
  currentDrawdown: {
    title: 'Current drawdown',
    what: 'How far the price now sits below its highest close in the window. Zero means it is at a high.',
    good: 'Within about −10% of the high.',
    bad: 'Below about −30%: still deep in a fall.',
  },
  diversificationRatio: {
    title: 'Diversification ratio',
    what: 'The holdings’ average volatility, weighted, divided by the portfolio’s own volatility. Above 1 means the holdings partly cancel out each other’s swings.',
    good: 'About 1.3 or more: holdings offset each other well.',
    bad: 'Close to 1: the holdings move largely as one.',
  },
  effectiveHoldings: {
    title: 'Effective holdings',
    what: 'How many equally weighted holdings would be as concentrated as this portfolio. Ten holdings with one at 80% count as barely more than one.',
    good: '10 or more for a stock portfolio.',
    bad: 'Under about 5: a few positions drive most of the result.',
  },
  riskLevel: {
    title: 'Risk level',
    what: 'Claude’s overall grade of how risky the portfolio is, from low to high, based on its volatility, drawdowns and concentration.',
  },
  diversification: {
    title: 'Diversification',
    what: 'Claude’s grade of how well the holdings offset one another, from poor to strong, based on correlations, sectors and concentration.',
  },

  // ── Account ──
  aiRequestsHour: {
    title: 'AI requests in the last hour',
    what: 'New AI reports, portfolio reviews and assistant answers written for you in the last hour, which count toward your hourly limit. Reused reports are free and not counted.',
  },
  aiRequestsDay: {
    title: 'AI requests in the last day',
    what: 'New AI requests written for you in the last 24 hours, which count toward your daily limit.',
  },
  requestsPerMinute: {
    title: 'Requests per minute',
    what: 'How many requests your account may make to PyQxel each minute, across every page. It keeps the server responsive for everyone.',
  },
} satisfies Record<string, Info>

export type InfoKey = keyof typeof entries

export const INFO: Record<InfoKey, Info> = entries
