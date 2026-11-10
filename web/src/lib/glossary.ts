// Plain-language explanations of every stat, model, and chart in the app, shown by the
// "i" beside each (components/InfoTip). Every new stat, model, or chart gets an entry here.
//
// Ranges are rules of thumb for a typical US stock or a long-only portfolio; they vary by
// industry, market, and era, and are a starting point for judgment, not a verdict.

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
  notForFunds: {
    title: 'Does not apply to funds',
    what: 'An ETF or mutual fund holds many companies instead of running a business, so a single company’s figures, such as its market cap, sector, earnings, margins, or valuation ratios, have no value for it. A fund is judged instead by what it holds and what it costs: its net assets, expense ratio, and holdings.',
  },
  netAssets: {
    title: 'Net assets',
    what: 'The total value of everything the fund holds, minus what it owes: the fund’s size, as market cap is a company’s.',
    good: 'Above about $1 billion: large funds trade cheaply and are unlikely to close.',
    bad: 'Below about $100 million: small funds are closed more often, which can hand you a taxable sale.',
  },
  expenseRatio: {
    title: 'Expense ratio',
    what: 'The share of your money the fund keeps each year to run itself, taken out of its returns. 0.03% is $3 a year on every $10,000.',
    good: 'Below 0.20% for an index fund; broad market funds charge as little as 0.03%.',
    bad: 'Above about 0.75%: fees compound, and over decades a 1% fee can take a fifth of your final balance.',
  },
  fundDividendYield: {
    title: 'Distribution yield',
    what: 'The dividends and interest the fund paid out over the last year, as a percentage of its price.',
    note: 'A stock fund typically yields 1–2%, a bond fund more. A high yield is not extra return on its own: the price falls by what is paid out.',
  },
  portfolioTurnover: {
    title: 'Turnover',
    what: 'The share of its holdings the fund replaced over a year.',
    good: 'Below about 20%, as for most index funds: less trading costs less and, outside a retirement account, triggers fewer taxable gains.',
    bad: 'Above 100% means it replaces everything within a year, a mark of active trading.',
  },
  inceptionDate: {
    title: 'Started',
    what: 'When the fund began trading. Its price history, and so its returns and risk, go back no further.',
  },
  leveragedFund: {
    title: 'Leveraged',
    what: 'Whether the fund borrows or uses derivatives to multiply its index’s daily moves, say two or three times.',
    bad: 'A leveraged fund resets every day, so over months it can lose money even when its index rises. It is built for short trades, not for holding.',
  },
  fundSectors: {
    title: 'Sector weights',
    what: 'How the fund’s money is split among the market’s sectors, such as technology or health care.',
    note: 'A fund heavy in one sector rises and falls with it: the S&P 500 has recently held about a third in technology.',
  },
  assetMix: {
    title: 'What it holds',
    what: 'How the fund’s money is split between stocks, bonds, and cash, from its latest holdings report to the SEC.',
    note: 'Stocks grow more over the long run and swing more; bonds pay steady interest and usually fall less in a crash; cash is steady but earns least.',
  },
  weightedFromHoldings: {
    title: 'Weighted from its holdings',
    what: 'A fund has no earnings or sales of its own, so these figures are those of the companies it owns, each counted by its share of the fund: what you own a slice of when you buy it. They use the holdings in the fund’s latest report and each company’s latest figures, as of the dates shown.',
    note: 'Only the companies whose figures could be found are counted; the coverage says how much of the fund’s stocks that is. Sectors come from each company’s SEC industry code where known, which can differ from the fund’s own classification.',
  },
  fundPe: {
    title: 'P/E, weighted from holdings',
    what: 'What the fund pays for each dollar of its companies’ yearly earnings: its share of their prices over its share of their profits. It is a harmonic mean, so one company with a huge P/E cannot swamp it.',
    good: 'Below about 20 is cheaper than the US market’s recent average.',
    bad: 'Above about 30 means its companies are priced for strong growth, which leaves less room for disappointment.',
  },
  fundPriceSales: {
    title: 'Price / sales, weighted from holdings',
    what: 'What the fund pays for each dollar of its companies’ yearly sales.',
    note: 'The S&P 500 has recently traded around 3 times sales; value funds lower, growth funds higher.',
  },
  fundMargins: {
    title: 'Margins, weighted from holdings',
    what: 'The share of each dollar of sales its companies keep: after the cost of goods (gross), after running the business (operating), and after everything including tax (net), weighted by their share of the fund.',
    note: 'Large technology companies push a fund’s margins up; banks have no gross margin and are left out of it.',
  },
  fundRoe: {
    title: 'Return on equity, weighted from holdings',
    what: 'Its companies’ yearly profit as a share of the money their shareholders have put in, weighted by their share of the fund.',
    note: 'Companies that buy back many shares can show very high figures, which lifts the average.',
  },
  fundGrowth: {
    title: 'Growth, weighted from holdings',
    what: 'How much its companies’ sales and earnings grew over the last year, weighted by their share of the fund.',
    note: 'A few fast growers among its largest holdings can lift these a lot.',
  },
  fundSize: {
    title: 'Company size',
    what: 'The average market cap of its companies, weighted by their share of the fund, and how much is in large (over $10 billion), mid-sized ($2–10 billion) and small companies.',
    note: 'Small companies have grown faster in some eras and swing more; most index funds are dominated by large ones.',
  },
  bondCoupon: {
    title: 'Average coupon',
    what: 'The interest rate the fund’s bonds pay on their face value, weighted by their share of the fund.',
    note: 'What you earn also depends on the price paid for the bonds: their yield, which this is close to only when they trade near face value.',
  },
  bondMaturity: {
    title: 'Average maturity',
    what: 'How many years until the fund’s bonds are due to be repaid, weighted by their share of the fund.',
    good: 'Shorter, below about 3 years, means prices barely move when interest rates change.',
    bad: 'Longer, above about 10 years, means a bigger price fall when rates rise.',
    note: 'This is the bonds’ stated final date; mortgage-backed bonds are usually repaid years earlier, so the fund is less sensitive to rates than this suggests.',
  },
  bondIssuers: {
    title: 'Who the bonds are from',
    what: 'How the fund’s bonds split between the US Treasury, government agencies and the mortgages they guarantee, companies, cities and states, and foreign governments.',
    note: 'Treasuries are the safest; corporate bonds pay more to make up for the chance a company cannot pay.',
  },
  fundHoldings: {
    title: 'Largest holdings',
    what: 'The fund’s biggest positions and the share of its money in each.',
    note: 'If the top ten add up to much more than about 30%, a few companies drive its returns. Compare with your other holdings to see overlap: two funds holding the same names are less diversified than they look.',
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
    bad: 'Far behind the market can signal trouble, or just an out-of-favor sector.',
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
    what: 'The share of the stock’s return swings explained by the market, size, value, and momentum factors.',
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
    what: 'Enterprise value divided by earnings before interest, taxes, depreciation, and amortization: a valuation that works across companies with different debts.',
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
    what: 'The share of revenue that ends up as profit after every cost, interest, and tax.',
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

  // ── Analysts, earnings, and insiders ──
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
  backtestMode: {
    title: 'Single ticker (or two) or across tickers',
    what: 'Two ways to backtest. Single ticker (or two) tests a strategy on symbols you pick: a built-in rule on one stock, ETF, or fund, or your own code on it, with Also trade adding up to 19 more for the code to move money between. Across tickers ranks a set of stocks against one another at each rebalance and holds the best, or runs code that trades every ticker it names.',
    note: 'Pick Single ticker (or two) to time one investment or split money among a few you already know. Pick Across tickers to test a rule for choosing among many, such as “hold the three sectors that rose most last quarter”.',
  },
  backtestPerformance: {
    title: 'Performance',
    what: 'The strategy’s return, risk, and drawdowns compared with the benchmark over the same window.',
    note: 'Judge return and risk together: a higher return with much deeper drawdowns may not be better.',
  },
  attribution: {
    title: 'Return attribution',
    what: 'A regression of the strategy’s daily returns on market factors, separating returns from simple market, size, value, or momentum exposure (betas) from what remains (alpha).',
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
  holdingGainLoss: {
    title: 'Gain/loss over a window',
    what: 'How much each holding’s value rose or fell over the window you pick, in dollars and as a percent, with dividends counted as if reinvested. Pick the window in the column’s menu, or choose “Since a date…” for any start you like.',
    note: 'PyQxel doesn’t know when you bought or what you paid, so the dollars assume you held today’s shares for the whole window. That makes this how the position performed, not your broker’s “total gain/loss” since you bought.',
  },
  gainUnavailableCash: {
    title: 'No gain or loss: cash',
    what: 'A money-market fund keeps its price at $1 a share and pays what it earns as interest, so its price never shows a gain or loss.',
    note: 'Its interest, recently about 3–5% a year, still adds to your account: your broker lists it as dividends.',
  },
  gainUnavailableNew: {
    title: 'No gain or loss: too new',
    what: 'This holding started trading after the window began, so there is no price to measure the whole window from.',
    note: 'Pick a shorter window, or a custom start after its first trading day, to see its change.',
  },
  gainUnavailableData: {
    title: 'No gain or loss: no prices',
    what: 'Its prices over the window could not be fetched, or your market data keys cover only part of the window.',
    note: 'Settings shows what your keys cover; adding a key there often fills this in.',
  },
  holding52WeekRange: {
    title: '52-week range',
    what: 'The lowest and highest prices of the last 52 weeks, at the ends of the line. The dot shows where the latest price sits between them.',
    good: 'Near the middle is ordinary. A dot near the high means the stock has been rising; near the low, falling.',
    bad: 'A dot pinned at the low can be a warning sign worth a look, but it is not by itself a reason to buy or sell.',
    note: 'Prices are adjusted for splits and dividends, so the ends can differ slightly from your broker’s.',
  },
  accountTotal: {
    title: 'Account total',
    what: 'The whole portfolio: the value of every priced holding, and the gains or losses of its holdings over each window, added up. The percent is that gain against what the portfolio was worth at the window’s start.',
    note: 'Cash counts as no gain. Holdings without a gain or loss (too new, or no prices) are left out of the totals’ gains.',
  },
  combinedReview: {
    title: 'Review both portfolios together',
    what: 'One review of all your saved portfolios as a single, overall portfolio, such as a brokerage account and a Roth IRA together. Claude sees which account holds what, and says which account each change belongs in, with taxes in mind.',
    good: 'Cheaper than reviewing each one, and better advice: overlap and gaps across accounts show up only when they are seen together.',
    note: 'Say what each account is (taxable, Roth IRA, 401(k)…) in its name or in “Anything else”, so the plan can account for taxes.',
  },
  dataCoverage: {
    title: 'Market data, by kind',
    what: 'Each kind of market data PyQxel uses, and which of your keys give it. PyQxel tries your keys in order, cheapest first, and uses the first that has the data.',
    good: 'Covered: one of your keys gives it in full on its free plan.',
    bad: 'Partly: your keys give some of it, such as a year of history instead of thirty. Off: none of your keys gives it.',
    note: 'A paid plan of yours may cover more than its free plan; the paid line says what it adds.',
  },
  planCash: {
    title: 'Cash in the plan',
    what: 'The money the plan leaves uninvested. When you hold a money-market fund such as SPAXX, your cash sits in it, so the plan shows cash under that fund; otherwise it is shown as Cash. Cash never needs an order: it is what is left after the sells and buys.',
    note: 'Cash has no ticker. CASH, in capitals, is a bank’s stock (Pathward Financial), not cash.',
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
    what: 'Claude reviews your saved holdings (where the risk comes from and how well they diversify) and recommends specific changes, which the app turns into exact orders.',
    note: 'An AI’s recommendation from past prices, not a licensed adviser’s; check taxes, costs, and prices before trading.',
  },
  reviewHistory: {
    title: 'Price history to measure',
    what: 'How many years of daily prices the review’s statistics are measured over: each holding’s return, volatility, drawdown, and correlations. It is not how far ahead Claude looks. Pick a preset or choose Custom for any length from 12 to 120 months.',
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
    what: 'Claude’s overall grade of how risky the portfolio is, from low to high, based on its volatility, drawdowns, and concentration.',
  },
  diversification: {
    title: 'Diversification',
    what: 'Claude’s grade of how well the holdings offset one another, from poor to strong, based on correlations, sectors, and concentration.',
  },

  // ── Account ──
  aiRequestsHour: {
    title: 'AI requests in the last hour',
    what: 'New AI reports, portfolio reviews, and assistant answers written for you in the last hour, which count toward your hourly limit. Reused reports are free and not counted.',
  },
  aiRequestsDay: {
    title: 'AI requests in the last day',
    what: 'New AI requests written for you in the last 24 hours, which count toward your daily limit.',
  },
  requestsPerMinute: {
    title: 'Requests per minute',
    what: 'How many requests your account may make to PyQxel each minute, across every page. It keeps the server responsive for everyone.',
  },

  // ── News order ──
  marketNewsOrder: {
    title: 'Most important market news',
    what: 'Most important puts first the headlines that look most likely to move markets as a whole. PyQxel scores each one from what it is about (the Fed, interest rates, inflation, jobs and the economy count most, then big market moves, trade and large company events such as mergers and earnings), whether a major publisher such as Reuters, Bloomberg, or The Wall Street Journal ran it, and how recent it is: a headline counts about half as much after a day. Newest simply lists them by time.',
    note: 'It is a rule of thumb that reads headlines for key words, not a judgment of each story, so an important story with a plain headline can rank low.',
  },
  companyNewsOrder: {
    title: 'Most important company news',
    what: 'Most important puts first the headlines that look most likely to matter for this company’s shares. PyQxel scores each one from what it is about (results and guidance count most, then analyst upgrades and downgrades, mergers and deals, buybacks and dividends, leadership changes, lawsuits, and regulators, and drug approvals), whether the headline names the ticker, whether a major publisher such as Reuters or Bloomberg ran it, and how recent it is: a headline counts about half as much after two days. Newest simply lists them by time.',
    note: 'It is a rule of thumb that reads headlines for key words, not a judgment of each story, so an important story with a plain headline can rank low, and a market-wide story that mentions the company can rank high.',
  },

  // ── Nightly screener refresh ──
  nightlyRefresh: {
    title: 'Nightly screener refresh',
    what: 'Rebuilds your own copy of the stock screener every night, on your own market data keys, so its numbers come from data you are licensed to see. Until it first runs, the screener shows the shared universe if there is one.',
    note: 'Finnhub’s free plan allows about 60 requests a minute and the refresh makes one per listed stock and ETF: about four hours for every stock and ETF, and minutes for a smaller choice such as the S&P 500 (see What to refresh). Once a week a refresh of everything checks every US ETF for new and newly traded ones, which join within the week. While it runs it uses most of your Finnhub allowance, so news, ratings and quotes may be slow or briefly unavailable: pick an hour when you are not using PyQxel. Free price-history plans cover only some stocks and ETFs a night: the largest stocks and most-traded ETFs come first, sharing them in proportion. ETFs have no market cap or company figures, so a screen on those leaves them out. Sectors come from each company’s SEC industry code, which can differ from a data provider’s.',
  },
  nightlyUniverse: {
    title: 'What to refresh',
    what: 'Which securities your own screener is rebuilt from each night. Tick as many sets as you like: every stock on a US exchange, every US ETF, the stocks listed on Nasdaq, the S&P 500 (500 large US companies), the Nasdaq-100 (the 100 largest non-financial companies on Nasdaq), and international stocks, meaning foreign companies whose shares or ADRs trade in the US, such as Toyota (TM) and ASML. A security in several sets is fetched once.',
    good: 'The fewest sets that hold the stocks you look for: a refresh of the S&P 500 takes about 10 minutes, every US stock and ETF about four hours, and the less it fetches, the less of your Finnhub allowance it uses while it runs.',
    note: 'International stocks are those whose companies report to the SEC as foreign companies (on Form 20-F or 40-F) or in a currency other than the dollar. Listings on exchanges abroad, such as Toyota’s 7203 in Tokyo, need a paid plan, since free plans have no figures for them. The indexes’ companies come from the holdings that index funds (IVV for the S&P 500, QQQM for the Nasdaq-100) report to the SEC each quarter, published about two months after the quarter ends, so a company that just joined or left an index shows up a few months late, and a few whose names changed since they filed with the SEC may be missing.',
  },
  nightlyCustom: {
    title: 'Also refresh these tickers',
    what: 'Stocks and ETFs refreshed every night on top of the sets you ticked, such as one ETF you follow with a refresh of the S&P 500. They are kept in your screener even below its usual floors on market cap, price, and trading volume, and they get price histories first.',
    note: 'A listing on an exchange abroad, such as 7203.T, has no figures on free plans, so its company’s US listing (TM for Toyota) is refreshed in its place when there is one; the refresh says so after it runs. Up to 100 tickers.',
  },
  nightlyLastRefresh: {
    title: 'Last refresh',
    what: 'When your own screener was last rebuilt successfully, and the date of the closing prices it used.',
    good: 'Within the last day or so, on trading days.',
    bad: 'Several days old: check for a failed refresh above, such as a rejected key.',
  },
  nightlyStocks: {
    title: 'Stocks in your screener',
    what: 'How many US-listed stocks passed the screener’s floors (market cap, share price, and trading volume) in your last refresh.',
    note: 'Usually a few thousand for every US stock, about 500 for the S&P 500, and about 100 for the Nasdaq-100. Fewer can mean Finnhub left some stocks out, for example when its limit was reached.',
  },
  nightlyPriced: {
    title: 'With price history',
    what: 'How many of those stocks also have the columns that need a year of daily prices: one-month return, 12-1 momentum, volatility, maximum drawdown, and factor betas. They come from your Tiingo, FMP, EODHD, or Alpha Vantage key.',
    good: 'Close to the number of stocks, which takes a paid price-history plan.',
    note: 'Free plans allow only some stocks a day, so this is often a few hundred or fewer. For the rest, Finnhub’s figures stand in: its 52-week return for 12-1 momentum (it also counts the latest month), its three-month volatility for the year’s, and its beta for the market beta. Maximum drawdown and the size, value, and momentum betas stay empty for them.',
  },
  nightlyNextRefresh: {
    title: 'Next refresh',
    what: 'When your next nightly refresh is due to start, in your own time. It starts within the hour after this time.',
  },
  // ── Billing ──
  billing: {
    title: 'Billing',
    what: 'What you pay for the nightly screener refresh. Everything else in PyQxel is free to use, since your own keys pay the data providers. The refresh is different: it runs on PyQxel’s cloud server, which charges by the second of computing time, and you pay your refreshes’ share of that at cost, plus Stripe’s processing fees on your own payments, with nothing added.',
    good: 'A refresh of the S&P 500 costs about a cent a night, and one of the Nasdaq-100 less. Every US stock and ETF, which takes about four hours, costs roughly 25 to 30 cents a night.',
    note: 'If you have neither credit nor a subscription, your refresh pauses until you add one; nothing else in PyQxel changes.',
  },
  billingCredit: {
    title: 'Credit',
    what: 'Money you paid in advance, which each night’s refresh uses up by what it cost. Shown to a tenth of a cent, since one refresh can cost less than a cent.',
    good: 'Enough for a few months at your pace, so you top up rarely and lose little to Stripe’s fee on each payment.',
    bad: 'Below zero: refreshes ran past it with no subscription to bill the rest. Your next top-up repays it first.',
  },
  billingLast30Days: {
    title: 'Last 30 days',
    what: 'What your nightly refreshes cost in the last 30 days, and how many ran. Each refresh costs its share of the time the server spent on it, at Google Cloud’s price for that computing time.',
    note: 'A refresh that failed is billed too, since the server ran either way; a failed one usually stops within a minute or two and costs a fraction of a cent.',
  },
  billingTopUp: {
    title: 'Add credit',
    what: 'Buy any amount of credit from $5 to $500 on Stripe’s secure checkout page; it appears within seconds. You pay Stripe’s processing fee on top, so $10 of credit costs $10.61. Refreshes use the credit before any subscription is billed.',
    good: '$10 to $20 suits most refresh choices. Stripe’s fee is 30 cents plus 2.9% of each payment, about 6% on $10 and 4% on $20, so larger top-ups pay less in fees.',
    note: 'Credit you have not used is refunded to your card if you delete your account.',
  },
  billingSubscription: {
    title: 'Subscription',
    what: 'Stripe bills your card at the end of each week, month, or year for what your refreshes cost in that period, after any credit is used. You choose how often; the amount is whatever your refreshes cost, not a fixed price. Stripe’s fees on each bill, 30 cents plus 3.6%, are added to the next bill.',
    good: 'Yearly or monthly: the 30-cent part of the fee is a large share of a small weekly bill.',
    note: 'Stripe charges nothing under 50 cents; a smaller bill is added to the next one. If a payment fails, Stripe retries it and your refresh pauses until it is paid. Cancel any time on Stripe’s billing page: the subscription ends at the end of the period, billing the refreshes up to then.',
  },
  billingHistory: {
    title: 'History',
    what: 'Your latest payments and charges: credit you added, each refresh’s cost (taken from credit, or billed through your subscription when credit ran out), Stripe’s fees on a subscription bill, and any refund. Receipts and invoices are on Stripe’s billing page.',
  },
  nightlyKeyUse: {
    title: 'What one refresh uses',
    what: 'About how many requests one nightly refresh sends on each key, and what share of that plan’s daily or monthly allowance that is. Finnhub gets one request per listed stock for its metrics; each price-history key gets one request per stock given price-history columns.',
    good: 'Well under each plan’s daily limit, so pages and backtests the next day still have requests left.',
    bad: 'Near or over 100% of a daily limit: the refresh would use up that key for the day, and pages would fall back to shorter or older prices.',
    note: 'After a refresh runs, these are its own counts rather than estimates.',
  },

  // ── Plan usage ──
  planUsage: {
    title: 'Plan usage',
    what: 'How much of this key’s plan has been used: each limit the plan sets (per minute, hour, day, or month, and different symbols a month where one applies), how much is used and left, and when each resets.',
    note: 'Where the provider reports its own figures, they count every use of the key, from PyQxel or anywhere else. Tiingo, FMP, and Alpha Vantage report nothing, so for them PyQxel shows its own count of its requests, which leaves out use of the same key elsewhere.',
  },
  usageLimit: {
    title: 'Limit',
    what: 'The most the plan allows in each span of time: requests (EODHD counts “calls”, which weigh some requests more), different symbols, or data downloaded.',
  },
  usageUsed: {
    title: 'Used',
    what: 'How much of the limit has been used in the current span, and who counted it: the provider itself, or PyQxel’s own count of its requests.',
  },
  usageLeft: {
    title: 'Left',
    what: 'How much of the limit remains until it resets.',
    good: 'Plenty left for the pages you open: about one request per holding or stock shown.',
    bad: 'Close to zero: pages then fall back to other keys, or to shorter or older prices, until the limit resets.',
  },
  usageResets: {
    title: 'Resets',
    what: 'When the current span ends and the limit starts again, on this device’s clock, with the provider’s rule for it.',
    note: 'FMP and Alpha Vantage do not say when their daily limits reset, so PyQxel counts their days from midnight UTC.',
  },
  loadEstimate: {
    title: 'Before this page loads',
    what: 'Opening this page asks your keys’ providers for prices, profiles, or news right away. This shows about how many requests that would send on each key, and how much of the tightest limit is left, so you can choose whether to spend them now.',
    note: 'Prices PyQxel already keeps for you, and recent answers it remembers, cost nothing, so the real count is often lower.',
  },
  storedPrices: {
    title: 'Stored prices',
    what: 'Daily prices PyQxel keeps for you alone, fetched on your own keys. Later pages and backtests reuse them, fetching only the days since, instead of spending a request each time.',
    note: 'They are never shown to anyone else, as the providers’ terms require, and are deleted when unused for 30 days, when you delete them here, or when you delete your account.',
  },
  shortHistory: {
    title: 'Shorter history than asked',
    what: 'A provider served fewer years of prices than the backtest window asks for, because a key ran out of requests or its plan covers less. A backtest on the shorter history can trade very differently, or not at all.',
    note: 'Waiting until the key’s limit resets brings the full history back, and PyQxel keeps it for next time.',
  },
} satisfies Record<string, Info>

export type InfoKey = keyof typeof entries

export const INFO: Record<InfoKey, Info> = entries
