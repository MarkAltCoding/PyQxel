// Plain-language explanations of every stat, model, and chart in the app, shown by the
// "i" beside each (components/InfoTip). Every new stat, model, or chart gets an entry here.
//
// Keep each part short enough to read at a glance: `what` in one or two sentences, `good`
// and `bad` in a line each, and anything longer in `more`, which the box shows only when
// asked. A stat with a formula gives it in `formula`.
//
// Ranges are rules of thumb for a typical US stock or a long-only portfolio; they vary by
// industry, market, and era, and are a starting point for judgment, not a verdict.

export interface Info {
  /** What it is called. */
  title: string
  /** What it measures, for someone new to investing: one or two short sentences. */
  what: string
  /**
   * How it is calculated. `_x` or `_{text}` writes a subscript, `^x` or `^{text}` a
   * superscript; a new line starts a second line, such as what the symbols stand for.
   */
  formula?: string
  /** What a reassuring value looks like, where there is one. */
  good?: string
  /** What a worrying value looks like, where there is one. */
  bad?: string
  /** A short caveat or context, always shown. */
  note?: string
  /** Detail most readers can skip, shown behind "More". */
  more?: string
}

const entries = {
  // ── Prices and returns ──
  price: {
    title: 'Price',
    what: 'The latest price of one share, adjusted for splits.',
    note: 'A high or low price says nothing about whether a stock is cheap; compare valuation ratios such as P/E.',
  },
  marketCap: {
    title: 'Market cap',
    what: 'The market value of the whole company.',
    formula: 'Market cap = share price × shares outstanding',
    note: 'Large caps are above about $10 billion, mid caps $2–10 billion, and small caps below $2 billion. Smaller companies tend to swing more.',
  },
  shareClasses: {
    title: 'Share classes',
    what: 'Some companies sell more than one kind of share, such as Alphabet’s GOOGL (votes) and GOOG (no vote). Both own the same company, so the screener lists them together and counts it once.',
    note: 'Their prices move almost together; the most traded class is usually the cheapest to buy and sell.',
  },
  notForFunds: {
    title: 'Does not apply to funds',
    what: 'A fund holds many companies rather than running a business, so one company’s figures (market cap, earnings, margins) have no value for it.',
    note: 'Judge a fund by what it holds and what it costs: its net assets, expense ratio, and holdings.',
  },
  netAssets: {
    title: 'Net assets',
    what: 'The fund’s size: the value of everything it holds, minus what it owes.',
    formula: 'Net assets = holdings’ value − liabilities',
    good: 'Above about $1 billion: large funds trade cheaply and rarely close.',
    bad: 'Below about $100 million: small funds close more often, which can force a taxable sale.',
  },
  expenseRatio: {
    title: 'Expense ratio',
    what: 'The share of your money the fund keeps each year to run itself, taken out of its returns.',
    formula: 'Yearly cost = amount invested × expense ratio\n$10,000 × 0.03% = $3 a year',
    good: 'Below 0.20% for an index fund; broad market funds charge as little as 0.03%.',
    bad: 'Above about 0.75%: over decades, a 1% fee can take a fifth of your final balance.',
  },
  fundDividendYield: {
    title: 'Distribution yield',
    what: 'The dividends and interest the fund paid over the last year, as a share of its price.',
    formula: 'Yield = payouts over 12 months ÷ price',
    note: 'Stock funds typically yield 1–2%, bond funds more. A payout is not extra return: the price drops by what is paid.',
  },
  portfolioTurnover: {
    title: 'Turnover',
    what: 'The share of its holdings the fund replaced over a year.',
    formula: 'Turnover = lesser of purchases and sales ÷ average net assets',
    good: 'Below about 20%, like most index funds: less trading costs less and triggers fewer taxable gains.',
    bad: 'Above 100%: it replaces everything within a year, a sign of active trading.',
  },
  inceptionDate: {
    title: 'Started',
    what: 'When the fund began trading. Its price history, returns, and risk go back no further.',
  },
  leveragedFund: {
    title: 'Leveraged',
    what: 'Whether the fund borrows or uses derivatives to multiply its index’s daily move, say 2× or 3×.',
    bad: 'It resets daily, so over months it can lose money even when its index rises. It is built for short trades, not holding.',
  },
  fundSectors: {
    title: 'Sector weights',
    what: 'How the fund’s money is split among sectors, such as technology or health care.',
    note: 'A fund heavy in one sector rises and falls with it; the S&P 500 has recently held about a third in technology.',
  },
  assetMix: {
    title: 'What it holds',
    what: 'How the fund’s money is split between stocks, bonds, and cash, from its latest SEC holdings report.',
    note: 'Stocks grow most over the long run and swing most; bonds pay steady interest and usually fall less in a crash; cash is steady but earns least.',
  },
  weightedFromHoldings: {
    title: 'Weighted from its holdings',
    what: 'A fund has no earnings of its own, so these are its companies’ figures, each counted by its share of the fund.',
    formula: 'Fund figure = Σ w_i × figure_i ÷ Σ w_i\nw_i = holding i’s share of the fund',
    note: 'Only companies whose figures could be found count; the coverage says how much of the fund that is.',
    more: 'They use the holdings in the fund’s latest report and each company’s latest figures, as of the dates shown. Sectors come from each company’s SEC industry code, which can differ from the fund’s own labels.',
  },
  fundPe: {
    title: 'P/E, weighted from holdings',
    what: 'What the fund pays for each dollar of its companies’ yearly profit.',
    formula: 'P/E = 1 ÷ Σ (w_i ÷ P/E_i)\na weighted harmonic mean, so one huge P/E cannot swamp it',
    good: 'Below about 20: cheaper than the US market’s recent average.',
    bad: 'Above about 30: priced for strong growth, leaving less room for disappointment.',
  },
  fundPriceSales: {
    title: 'Price / sales, weighted from holdings',
    what: 'What the fund pays for each dollar of its companies’ yearly sales.',
    formula: 'P/S = 1 ÷ Σ (w_i ÷ P/S_i)',
    note: 'The S&P 500 has recently traded near 3× sales; value funds lower, growth funds higher.',
  },
  fundMargins: {
    title: 'Margins, weighted from holdings',
    what: 'The share of each sales dollar its companies keep: after the cost of goods (gross), after running the business (operating), and after everything (net).',
    formula: 'Margin = Σ w_i × margin_i ÷ Σ w_i',
    note: 'Large tech companies push margins up; banks have no gross margin and are left out of it.',
  },
  fundRoe: {
    title: 'Return on equity, weighted from holdings',
    what: 'Its companies’ yearly profit as a share of their shareholders’ money, by weight in the fund.',
    formula: 'ROE = Σ w_i × ROE_i ÷ Σ w_i',
    note: 'Companies that buy back many shares can show very high figures, which lifts the average.',
  },
  fundGrowth: {
    title: 'Growth, weighted from holdings',
    what: 'How much its companies’ sales and earnings grew over the last year, by weight in the fund.',
    formula: 'Growth = Σ w_i × growth_i ÷ Σ w_i',
    note: 'A few fast growers among the largest holdings can lift these a lot.',
  },
  fundSize: {
    title: 'Company size',
    what: 'The average market cap of its companies, by weight, and how much is in large (over $10B), mid ($2–10B), and small companies.',
    formula: 'Average size = Σ w_i × market cap_i ÷ Σ w_i',
    note: 'Most index funds are dominated by large companies; small ones swing more.',
  },
  bondCoupon: {
    title: 'Average coupon',
    what: 'The interest rate the fund’s bonds pay on their face value, by weight in the fund.',
    formula: 'Coupon = yearly interest ÷ face value',
    note: 'What you earn depends on the price paid (the yield); the two match only near face value.',
  },
  bondMaturity: {
    title: 'Average maturity',
    what: 'How many years until the fund’s bonds are repaid, by weight in the fund.',
    good: 'Below about 3 years: prices barely move when interest rates change.',
    bad: 'Above about 10 years: a bigger price fall when rates rise.',
    note: 'Mortgage bonds are usually repaid years before their final date, so the fund is less rate-sensitive than this suggests.',
  },
  bondIssuers: {
    title: 'Who the bonds are from',
    what: 'How the fund’s bonds split between the US Treasury, agencies and the mortgages they back, companies, cities and states, and foreign governments.',
    note: 'Treasuries are the safest; corporate bonds pay more for the chance a company cannot pay.',
  },
  fundHoldings: {
    title: 'Largest holdings',
    what: 'The fund’s biggest positions and the share of its money in each.',
    note: 'If the top ten add up to much over 30%, a few companies drive its returns. Two funds with the same names are less diversified than they look.',
  },
  enterpriseValue: {
    title: 'Enterprise value',
    what: 'What it would cost to buy the whole business, debts included.',
    formula: 'EV = market cap + debt − cash',
    note: 'Better than market cap for comparing companies with very different debts.',
  },
  periodReturn: {
    title: 'Return over the window',
    what: 'How much the price changed over the chosen history, with dividends reinvested.',
    formula: 'Return = P_{end} ÷ P_{start} − 1\nP = price adjusted for splits and dividends',
    note: 'Compare it with the S&P 500 over the same window; it has averaged about 10% a year over the long run.',
  },
  totalReturn: {
    title: 'Total return',
    what: 'How much $1 invested at the start grew or shrank by the end, after trading costs.',
    formula: 'Total return = Π (1 + r_t) − 1\nr_t = each day’s return',
    good: 'Above the benchmark’s, without deeper drawdowns.',
    bad: 'Below the benchmark: doing nothing would have done better.',
  },
  annualizedReturn: {
    title: 'Annualized return',
    what: 'The total return as a steady yearly rate (compound annual growth), so different windows compare.',
    formula: 'CAGR = (1 + total return)^{252 ÷ n} − 1\nn = trading days; a year has about 252',
    good: 'About 7–10% a year or more, in line with stocks’ long-run average.',
    note: 'Not shown under a year: annualizing a few months exaggerates it.',
  },
  week52Range: {
    title: '52-week range',
    what: 'The lowest and highest price over the past year.',
    formula: 'Low = min P, High = max P, over the last 252 trading days',
    note: 'Where today’s price sits shows how far it has risen or fallen; it is not a forecast.',
  },
  week52Return: {
    title: '52-week return',
    what: 'The price change over the past year, beside the S&P 500’s over the same year.',
    formula: 'Return = P_{today} ÷ P_{a year ago} − 1',
    good: 'Ahead of the S&P 500.',
    bad: 'Far behind the market: trouble, or just an out-of-favor sector.',
  },
  averageVolume: {
    title: 'Average volume',
    what: 'How many shares trade on a typical day, averaged over three months.',
    good: 'Millions of shares a day: easy to trade near the quoted price.',
    bad: 'Under about 100,000 a day: costly to trade, with wide gaps between buy and sell prices.',
  },
  avgDollarVolume: {
    title: 'Average daily dollar volume',
    what: 'The value of shares traded on a typical day.',
    formula: 'Dollar volume = average of (price × shares traded) per day',
    good: 'Above about $5 million a day: easy to trade.',
    bad: 'Below about $1 million a day: your own order can move the price.',
  },
  return1m: {
    title: '1-month return',
    what: 'The price change over the last month (about 21 trading days).',
    formula: 'Return = P_{today} ÷ P_{21 days ago} − 1',
    note: 'Very short-term moves often partly reverse, so they say little on their own.',
  },
  return6m: {
    title: '6-month return',
    what: 'The price change over the last six months (about 126 trading days).',
    formula: 'Return = P_{today} ÷ P_{126 days ago} − 1',
  },
  momentum12_1: {
    title: 'Momentum (12–1)',
    what: 'The return over the past twelve months, leaving out the latest month.',
    formula: 'Momentum = P_{1 month ago} ÷ P_{12 months ago} − 1',
    note: 'Recent winners have tended to keep winning for a while. The last month is skipped because very recent moves tend to reverse.',
  },

  // ── Risk ──
  volatility: {
    title: 'Volatility',
    what: 'How much the price swings: the yearly standard deviation of daily returns. About two years in three, the return lands within one volatility of its average.',
    formula: 'σ = √252 × stdev(r_t)\nr_t = daily returns; 252 trading days a year',
    good: 'Under about 20% a year is calm for one stock; a broad index runs 15–20%.',
    bad: 'Above about 40%: large swings both ways are common.',
  },
  volatilityNow: {
    title: 'Volatility now',
    what: 'Today’s expected volatility from the GARCH model, which weighs recent swings more than old ones.',
    formula: 'σ_{now} = √252 × σ_t\nσ_t = the GARCH model’s latest daily volatility',
    good: 'Below its long-run level: a calm spell.',
    bad: 'Well above its long-run level: a turbulent spell.',
  },
  garchModel: {
    title: 'GARCH volatility model',
    what: 'A model of how volatility changes: big moves tend to follow big moves, then calm returns toward a long-run level.',
    formula:
      'σ_t^2 = ω + α ε_{t−1}^2 + β σ_{t−1}^2\nε = yesterday’s surprise; α + β = how long shocks last',
    note: 'It forecasts how big the swings may be, not their direction. Fit to five years of daily returns.',
  },
  garchForecast: {
    title: 'Volatility forecast',
    what: 'The GARCH model’s expected volatility over the coming trading days.',
    formula: 'σ_{h days} = √(252 × average of the forecast daily variances)',
    note: 'Forecasts drift toward the long-run level; the further ahead, the closer.',
  },
  realizedVolatility: {
    title: 'Realized volatility',
    what: 'The volatility actually measured from daily returns over the chosen window.',
    formula: 'σ = √252 × stdev(r_t) over the window',
    note: 'A forecast well above it means the model expects things to get bumpier.',
  },
  longRunVolatility: {
    title: 'Long-run volatility',
    what: 'The level the GARCH model expects volatility to settle at. The half-life is how many days a shock takes to fade halfway.',
    formula: 'σ_{long run} = √(252 × ω ÷ (1 − α − β))\nhalf-life = ln 0.5 ÷ ln(α + β) days',
    note: 'A half-life of a few weeks means turbulence passes quickly; a long one means it lingers.',
  },
  maxDrawdown: {
    title: 'Max drawdown',
    what: 'The largest fall from a peak to a later low: the worst loss for someone who bought at the top.',
    formula: 'Drawdown_t = V_t ÷ peak V up to t − 1\nMax drawdown = the lowest drawdown',
    good: 'Smaller (closer to 0%) than the benchmark’s.',
    bad: 'Beyond about −30% is severe; −50% needs +100% just to break even.',
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
    what: 'How much the stock tends to move with the market: 1 moves in step, 1.5 moves 50% more, 0.5 half as much.',
    formula: 'β = cov(r_{stock}, r_{market}) ÷ var(r_{market})',
    good: 'Below 1 for a steadier, more defensive holding.',
    bad: 'Well above 1: larger losses when the market falls (and larger gains when it rises).',
  },
  sharpe: {
    title: 'Sharpe ratio',
    what: 'Return earned per unit of risk taken.',
    formula: 'Sharpe = mean(r − r_f) ÷ stdev(r) × √252\nr = daily return; r_f = the T-bill rate',
    good: 'Above 1 is good, above 2 excellent. Stocks have averaged about 0.4–0.6.',
    bad: 'Below 0: it earned less than cash.',
  },
  sortino: {
    title: 'Sortino ratio',
    what: 'Like the Sharpe ratio, but only losses count as risk, since gains are welcome.',
    formula:
      'Sortino = mean(r − r_f) ÷ DD × √252\nDD = √mean(min(r − r_f, 0)^2), the downside deviation',
    good: 'Above 1 is good, above 2 excellent.',
    bad: 'Below 0: it earned less than cash.',
  },
  annualizedVolatility: {
    title: 'Annualized volatility',
    what: 'How much returns swung, as a yearly standard deviation.',
    formula: 'σ = √252 × stdev(r_t)',
    good: 'Lower than the benchmark’s for a similar return.',
  },

  // ── Factor models ──
  factorModel: {
    title: 'Factor exposures',
    what: 'A regression explaining the stock’s returns by a few broad forces: the market, small vs. large companies, cheap vs. expensive (value), and recent winners vs. losers (momentum).',
    formula: 'r − r_f = α + β_{mkt}·MKT + β_{smb}·SMB + β_{hml}·HML + β_{mom}·MOM + ε',
    note: 'Each whisker is a 95% range; one that crosses zero means that exposure could be nothing.',
  },
  marketBeta: {
    title: 'Market beta',
    what: 'How strongly the stock moves with the market, from the factor regression. 1 means in step.',
    formula: 'β_{mkt} in r − r_f = α + β_{mkt}·(r_m − r_f) + … + ε',
    good: 'Around 1 or below for a steadier holding.',
    bad: 'Above about 1.5: it tends to amplify market falls.',
  },
  sizeBeta: {
    title: 'Size beta (SMB)',
    what: 'Positive when the stock behaves like a small company, negative like a large one.',
    formula: 'SMB = small companies’ return − big companies’ return',
  },
  valueBeta: {
    title: 'Value beta (HML)',
    what: 'Positive when the stock behaves like a cheap “value” stock, negative like a pricier “growth” stock.',
    formula: 'HML = high book-to-market return − low book-to-market return',
  },
  momentumBeta: {
    title: 'Momentum beta',
    what: 'Positive when the stock moves with recent winners, negative with recent losers.',
    formula: 'MOM = past winners’ return − past losers’ return',
  },
  profitabilityBeta: {
    title: 'Profitability beta (RMW)',
    what: 'Positive when the stock behaves like highly profitable companies, negative like weakly profitable ones.',
    formula: 'RMW = robust-profit return − weak-profit return',
  },
  investmentBeta: {
    title: 'Investment beta (CMA)',
    what: 'Positive when the stock behaves like companies that invest conservatively, negative like fast expanders.',
    formula: 'CMA = conservative investors’ return − aggressive investors’ return',
  },
  factorBeta: {
    title: 'Factor beta',
    what: 'How strongly returns move with this factor. Zero means no relationship.',
    formula: 'r − r_f = α + Σ β_k · factor_k + ε',
    note: 'With |t| below about 2, the estimate is not reliably different from zero.',
  },
  alpha: {
    title: 'Alpha',
    what: 'The yearly return the factors do not explain: skill or luck beyond market exposures.',
    formula: 'α_{yearly} = 252 × α_{daily}\nα_{daily} = the factor regression’s intercept',
    good: 'Positive, with t above about 2: unlikely to be pure luck.',
    bad: 'Negative: it lagged what its exposures alone would have earned.',
    note: 'Alpha is a noisy record of the past and rarely persists.',
  },
  rSquared: {
    title: 'R²',
    what: 'The share of the return swings the factors explain.',
    formula: 'R^2 = 1 − var(ε) ÷ var(r)',
    note: 'Above about 70%: it moves mostly with broad forces. Low: company news drives it more.',
  },
  factorRSquared: {
    title: 'Factor R²',
    what: 'The share of the stock’s return swings explained by the market, size, value, and momentum factors.',
    formula: 'R^2 = 1 − var(ε) ÷ var(r)',
    note: 'Low values mean company news dominates, which diversifies better in a portfolio.',
  },
  systematicRisk: {
    title: 'Systematic risk',
    what: 'The share of the stock’s risk from broad market forces, which diversifying cannot remove. The rest is idiosyncratic: the company’s own.',
    formula: 'Systematic = R^2   Idiosyncratic = 1 − R^2',
    note: 'Holding more unrelated stocks shrinks idiosyncratic risk, not systematic risk.',
  },
  tStat: {
    title: 't-statistic',
    what: 'How many standard errors an estimate is from zero.',
    formula: 't = estimate ÷ standard error',
    good: 'Above about 2 in size: unlikely to be zero by chance.',
  },
  standardError: {
    title: 'Standard error',
    what: 'How uncertain an estimate is. The true value lies within about two standard errors 95% of the time.',
    formula: '95% range ≈ estimate ± 1.96 × SE',
  },
  pValue: {
    title: 'p-value',
    what: 'The chance of an estimate at least this far from zero if the true value were zero.',
    formula: 'p = P(|T| ≥ |t|) when the true value is 0',
    good: 'Below 0.05, the usual bar for a real effect.',
  },

  // ── Valuation ──
  pe: {
    title: 'P/E ratio',
    what: 'What investors pay for each dollar of yearly profit.',
    formula: 'P/E = price ÷ earnings per share (last 12 months)',
    good: 'Below its industry’s usual level can mean cheap. The S&P 500 has averaged about 15–20.',
    bad: 'Above about 40 prices in a lot of growth. Negative means a loss.',
    note: 'Compare within an industry: fast growers normally trade higher.',
  },
  forwardPe: {
    title: 'Forward P/E',
    what: 'Price over analysts’ estimate of next year’s earnings.',
    formula: 'Forward P/E = price ÷ estimated EPS (next 12 months)',
    note: 'Below the trailing P/E means earnings are expected to grow. Estimates can be wrong.',
  },
  peg: {
    title: 'PEG ratio',
    what: 'The P/E adjusted for growth, to judge whether a high P/E is earned.',
    formula: 'PEG = P/E ÷ yearly EPS growth (in %)',
    good: 'Around 1 or below.',
    bad: 'Well above 2: the price outruns the growth.',
  },
  priceSales: {
    title: 'Price / sales',
    what: 'What investors pay for each dollar of yearly revenue; useful before a company has profits.',
    formula: 'P/S = market cap ÷ revenue (last 12 months)',
    good: 'Below about 2 is modest for most industries.',
    bad: 'Above about 10 needs very high growth or margins.',
  },
  priceBook: {
    title: 'Price / book',
    what: 'Market value over the accounting value of what the company owns, minus what it owes.',
    formula: 'P/B = market cap ÷ (assets − liabilities)',
    good: 'Near or below 1 can mean cheap, especially for banks and insurers.',
    note: 'Less telling for software and brands, whose main assets are not on the balance sheet.',
  },
  evEbitda: {
    title: 'EV / EBITDA',
    what: 'A valuation that compares fairly across companies with different debts.',
    formula:
      'EV / EBITDA = enterprise value ÷ earnings before interest, taxes, depreciation, and amortization',
    good: 'Below about 10 is often reasonable.',
    bad: 'Above about 20 is expensive unless growth is high.',
  },
  priceFcf: {
    title: 'Price / free cash flow',
    what: 'What investors pay for each dollar of cash the business has left after running and investing in itself.',
    formula: 'P/FCF = market cap ÷ (operating cash flow − capital spending)',
    good: 'Below about 20.',
    bad: 'Very high or negative: little cash reaches shareholders.',
  },
  fcfYield: {
    title: 'Free cash flow yield',
    what: 'The cash the business generates as a return on its price; the inverse of P/FCF.',
    formula: 'FCF yield = free cash flow ÷ market cap',
    good: 'Above about 5%.',
    bad: 'Negative: the company is burning cash.',
  },

  // ── Profitability and growth ──
  grossMargin: {
    title: 'Gross margin',
    what: 'The share of revenue left after the direct cost of the product or service.',
    formula: 'Gross margin = (revenue − cost of goods sold) ÷ revenue',
    good: 'Above about 40%; software often exceeds 70%.',
    bad: 'Below about 20%, as in retail and commodities: little room for error.',
  },
  operatingMargin: {
    title: 'Operating margin',
    what: 'The share of revenue left after all operating costs, before interest and taxes.',
    formula: 'Operating margin = operating income ÷ revenue',
    good: 'Above about 15%.',
    bad: 'Negative: the core business loses money.',
  },
  netMargin: {
    title: 'Net margin',
    what: 'The share of revenue that ends up as profit after every cost, interest, and tax.',
    formula: 'Net margin = net income ÷ revenue',
    good: 'Above about 10%.',
    bad: 'Negative: the company made a loss.',
  },
  roe: {
    title: 'Return on equity',
    what: 'How well the company turns shareholders’ money into profit.',
    formula: 'ROE = net income ÷ shareholders’ equity',
    good: 'Above about 15%.',
    bad: 'Below about 5%, or propped up by heavy debt (check debt / equity).',
  },
  roa: {
    title: 'Return on assets',
    what: 'Profit as a share of everything the company owns.',
    formula: 'ROA = net income ÷ total assets',
    good: 'Above about 5%.',
  },
  revenueGrowth: {
    title: 'Revenue growth',
    what: 'How much sales grew over the last twelve months against the twelve before.',
    formula: 'Growth = revenue_{last 12 months} ÷ revenue_{12 months before} − 1',
    good: 'Above about 10% a year is strong.',
    bad: 'Negative: sales are shrinking.',
  },
  revenueGrowth5y: {
    title: 'Revenue growth, five years',
    what: 'Average yearly sales growth over five years, which smooths out one-off years.',
    formula: 'Growth = (revenue_{now} ÷ revenue_{5 years ago})^{1/5} − 1',
    good: 'Above about 10% a year.',
  },
  epsGrowth: {
    title: 'EPS growth',
    what: 'How much earnings per share grew over the last twelve months.',
    formula: 'Growth = EPS_{last 12 months} ÷ EPS_{12 months before} − 1',
    good: 'Steady, positive growth.',
    bad: 'Negative or erratic. One-off gains or charges can distort it.',
  },
  earningsGrowth: {
    title: 'Earnings growth',
    what: 'How much profit grew over the last twelve months against the twelve before.',
    formula: 'Growth = net income_{last 12 months} ÷ net income_{12 months before} − 1',
    note: 'One-off gains or charges can distort it; check several years.',
  },

  // ── Dividends and balance sheet ──
  dividendYield: {
    title: 'Dividend yield',
    what: 'A year’s dividends as a share of the price.',
    formula: 'Yield = dividends per share (12 months) ÷ price',
    good: 'About 2–5% is typical for dividend payers.',
    bad: 'Above about 8%: the market may expect a cut.',
  },
  dividendTtm: {
    title: 'Dividend',
    what: 'Dividends paid per share over the last twelve months.',
    formula: 'Dividend = Σ dividends per share paid in the last 12 months',
  },
  payoutRatio: {
    title: 'Payout ratio',
    what: 'The share of profit paid out as dividends.',
    formula: 'Payout = dividends ÷ net income',
    good: 'Below about 60%: room to keep paying in a bad year.',
    bad: 'Above 100%: paying out more than it earns, which cannot last.',
  },
  dividendGrowth5y: {
    title: 'Dividend growth, five years',
    what: 'Average yearly dividend growth over five years.',
    formula: 'Growth = (dividend_{now} ÷ dividend_{5 years ago})^{1/5} − 1',
    good: 'Steady growth above inflation.',
  },
  debtEquity: {
    title: 'Debt / equity',
    what: 'How much the company relies on borrowing.',
    formula: 'D/E = total debt ÷ shareholders’ equity',
    good: 'Below about 1.',
    bad: 'Above about 2 adds risk if business slows or rates rise. Banks and utilities normally run higher.',
  },
  currentRatio: {
    title: 'Current ratio',
    what: 'Whether the company can pay the bills due within a year.',
    formula: 'Current ratio = current assets ÷ current liabilities',
    good: 'Between about 1.5 and 3.',
    bad: 'Below 1: it may struggle to pay near-term bills.',
  },
  quickRatio: {
    title: 'Quick ratio',
    what: 'Like the current ratio, but leaves out inventory, which can be slow to sell.',
    formula: 'Quick ratio = (current assets − inventory) ÷ current liabilities',
    good: 'Above about 1.',
    bad: 'Well below 1: it needs to sell inventory to pay its bills.',
  },
  interestCoverage: {
    title: 'Interest coverage',
    what: 'How many times over the company’s profit covers the interest it owes.',
    formula: 'Coverage = operating income ÷ interest expense',
    good: 'Above about 5.',
    bad: 'Below about 2: interest eats much of the profit.',
  },

  // ── Analysts, earnings, and insiders ──
  analystsAtBuy: {
    title: 'Analysts at buy',
    what: 'The share of analysts covering the stock who rate it a buy or strong buy.',
    formula: 'Share = (buy + strong buy) ÷ all ratings',
    note: 'Analysts lean toward buy ratings; a change over time says more than the level.',
  },
  nextEarnings: {
    title: 'Next earnings',
    what: 'The date of the next quarterly report, and analysts’ average estimate of earnings per share.',
    note: 'Prices often move sharply on report days.',
  },
  epsSurprise: {
    title: 'EPS surprise',
    what: 'How far the latest earnings per share came in above or below analysts’ average estimate.',
    formula: 'Surprise = (actual EPS − estimated EPS) ÷ |estimated EPS|',
    good: 'Positive: a beat.',
    bad: 'Negative: a miss, which often weighs on the price.',
  },
  insiders: {
    title: 'Insider trades',
    what: 'Shares executives and directors bought and sold on the open market, from their SEC filings.',
    note: 'Insiders sell for many reasons, such as taxes; buying with their own money is the stronger signal.',
  },
  insiderSentiment: {
    title: 'Insider sentiment',
    what: 'Finnhub’s monthly share purchase ratio: from −100 (insiders only sold) to +100 (insiders only bought), from how their holdings changed this month.',
    good: 'Positive: insiders added to their holdings.',
    note: 'Routine selling makes mildly negative scores common.',
  },

  // ── Public records ──
  lobbying: {
    title: 'Lobbying',
    what: 'Money the company disclosed spending on federal lobbying, by year.',
    note: 'Shows how much it depends on policy. The current year is incomplete: disclosures lag by weeks.',
  },
  federalAwards: {
    title: 'Federal awards',
    what: 'Federal contracts and grants awarded to the company over the past year.',
    note: 'Large awards next to revenue mean it depends on government customers.',
  },
  patents: {
    title: 'Patents',
    what: 'US patents and applications published for the company over the past year.',
    note: 'A rough sign of research activity; coverage is incomplete, so a low count proves little.',
  },
  secFilings: {
    title: 'SEC filings',
    what: 'Filings with the SEC over the last 90 days. 8-Ks report material events; Forms 4 and 144 report insider trades.',
    note: 'A burst of 8-Ks often comes with news worth reading.',
  },

  // ── News ──
  newsChart: {
    title: 'News coverage',
    what: 'How many articles mentioned the company each day over the last two weeks.',
    note: 'A spike usually marks an event; read that day’s headlines.',
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
    what: 'The average daily count over the last two weeks: a baseline for spotting unusual coverage.',
    formula: 'Per day = articles in 14 days ÷ 14',
  },
  newsBusiestDay: {
    title: 'Busiest day',
    what: 'The day with the most coverage in the last two weeks.',
  },

  // ── Charts ──
  priceChart: {
    title: 'Price chart',
    what: 'The closing price over time, adjusted for splits and dividends, so the line shows what an investor actually earned.',
    note: 'Hold Ctrl (or ⌘) and scroll to zoom.',
  },
  volatilityChart: {
    title: 'Volatility chart',
    what: 'How much the stock has swung over time, as annualized volatility, with the model’s forecast ahead.',
    note: 'Spikes mark turbulent spells, which tend to fade over the following weeks.',
  },
  equityChart: {
    title: 'Equity curve',
    what: 'How $1 invested at the start would have grown, for the strategy and the benchmark, after costs.',
    formula: 'V_t = Π_{s ≤ t} (1 + r_s)',
    good: 'Ends above the benchmark with shallower dips.',
    note: 'A strategy tuned to one history often does worse on the next.',
  },

  // ── Backtests ──
  backtestMode: {
    title: 'Single Ticker (or Two) or Across Tickers',
    what: 'Single Ticker (or Two) tests a strategy on symbols you pick. Across Tickers ranks many stocks at each rebalance and holds the best, or runs code that trades every ticker it names.',
    note: 'Use Single Ticker to time one investment or split money among a few. Use Across Tickers to test a rule for choosing among many.',
    more: 'Single Ticker (or Two) runs a built-in rule on one stock, ETF, or fund, or your own code on it, with Also trade adding up to 19 more for the code to move money between. Across Tickers suits rules such as “hold the three sectors that rose most last quarter”.',
  },
  backtestPerformance: {
    title: 'Performance',
    what: 'The strategy’s return, risk, and drawdowns beside the benchmark’s over the same window.',
    note: 'Judge return and risk together: a higher return with much deeper drawdowns may not be better.',
  },
  attribution: {
    title: 'Return attribution',
    what: 'A regression of the strategy’s daily returns on market factors, splitting returns from simple exposures (betas) from what remains (alpha).',
    formula: 'r − r_f = α + Σ β_k · factor_k + ε',
    note: 'A return that is all beta could be had more cheaply with an index fund.',
  },
  rankIc: {
    title: 'Rank IC',
    what: 'How well the ranking predicted the next period’s winners, averaged over every rebalance.',
    formula: 'IC = Spearman correlation(score rank, next-period return rank)',
    good: 'Above about 0.05, with t above about 2: a useful signal.',
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
    formula: 'Held long = rebalances holding it ÷ all rebalances',
  },
  heldShort: {
    title: 'Held short',
    what: 'The share of rebalances at which the stock was sold short.',
    formula: 'Held short = rebalances shorting it ÷ all rebalances',
  },
  contribution: {
    title: 'Contribution',
    what: 'This holding’s part of the strategy’s total return, after its trading costs.',
    formula: 'Contribution_i = Σ_t w_{i,t} × r_{i,t} − costs_i',
    note: 'Contributions add up to the strategy’s arithmetic return.',
  },
  averageWeight: {
    title: 'Average weight',
    what: 'The average share of capital in this symbol over the backtest. Negative means it was mostly sold short.',
    formula: 'Average weight = mean of w_t over every day',
  },
  timeHeld: {
    title: 'Held',
    what: 'The share of days the strategy held any position in this symbol.',
    formula: 'Held = days with w_t ≠ 0 ÷ all days',
  },
  trades: {
    title: 'Trades',
    what: 'Days on which the position changed. Each trade pays the trading cost, so frequent trading eats into returns.',
    formula: 'Cost of a trade = |change in weight| × cost in bps ÷ 10,000',
  },
  rankedFrom: {
    title: 'Ranked from',
    what: 'The first date the stock had enough history to be ranked.',
  },

  // ── Portfolio ──
  holdingWeight: {
    title: 'Weight',
    what: 'The holding’s share of the portfolio’s value at the latest prices.',
    formula: 'w_i = value_i ÷ Σ values',
    bad: 'One holding above about 20–25% concentrates a lot of risk in one company.',
  },
  holdingGainLoss: {
    title: 'Gain/loss over a window',
    what: 'How much each holding’s value rose or fell over the window you pick, with dividends reinvested.',
    formula: 'Gain = shares × (P_{end} − P_{start})   Gain % = P_{end} ÷ P_{start} − 1',
    note: 'It assumes you held today’s shares for the whole window: how the position performed, not your broker’s gain since you bought.',
    more: 'Pick the window in the column’s menu, or “Since a date…” for any start. PyQxel does not know when you bought or what you paid.',
  },
  gainUnavailableCash: {
    title: 'No gain or loss: cash',
    what: 'A money-market fund keeps its price at $1 and pays what it earns as interest, so its price never shows a gain.',
    note: 'Its interest, recently 3–5% a year, still adds to your account; brokers list it as dividends.',
  },
  gainUnavailableNew: {
    title: 'No gain or loss: too new',
    what: 'This holding started trading after the window began, so there is no starting price.',
    note: 'Pick a shorter window, or a start after its first trading day.',
  },
  gainUnavailableData: {
    title: 'No gain or loss: no prices',
    what: 'Its prices over the window could not be fetched, or your keys cover only part of it.',
    note: 'Settings shows what your keys cover; adding a key there often fills this in.',
  },
  holding52WeekRange: {
    title: '52-week range',
    what: 'The year’s lowest and highest prices at the line’s ends; the dot is the latest price.',
    formula: 'Position = (P − low) ÷ (high − low)',
    good: 'Near the middle is ordinary. Near the high: it has been rising; near the low, falling.',
    bad: 'Pinned at the low can be worth a look, but is not by itself a reason to buy or sell.',
    note: 'Prices are adjusted for splits and dividends, so the ends can differ slightly from your broker’s.',
  },
  accountTotal: {
    title: 'Account total',
    what: 'The whole portfolio: every priced holding’s value and gains added up.',
    formula: 'Gain % = Σ gains ÷ Σ values at the window’s start',
    note: 'Cash counts as no gain. Holdings with no gain (too new, or no prices) are left out of the gains.',
  },
  combinedReview: {
    title: 'Review several portfolios together',
    what: 'Review this portfolio alone, or two to five of them at once, such as a brokerage account and a Roth IRA, as one whole or each with its own plan.',
    good: 'Cheaper than one review each, and overlap and gaps across accounts only show up together.',
    more: 'Claude sees which account holds what. A review of several as one is kept apart from each portfolio’s own reviews; one giving each its own plan also shows as each one’s latest review. Set each account’s type above its holdings so the plan weighs taxes and contribution limits.',
  },
  dataCoverage: {
    title: 'Market data, by kind',
    what: 'Each kind of data PyQxel uses and which of your keys give it. Keys are tried cheapest first, and the first with the data is used.',
    good: 'Covered: a key of yours gives it in full on its free plan.',
    bad: 'Partly: some of it, such as one year of history instead of thirty. Off: none of your keys gives it.',
    note: 'A paid plan may cover more; the paid line says what it adds.',
  },
  planCash: {
    title: 'Cash in the plan',
    what: 'The money the plan leaves uninvested. It never needs an order: it is what is left after the sells and buys.',
    note: 'If you hold a money-market fund such as SPAXX, the cash is shown under it.',
    more: 'Cash has no ticker. CASH, in capitals, is a bank’s stock (Pathward Financial), not cash.',
  },
  moneyMarketCash: {
    title: 'Cash (money-market fund)',
    what: 'A money-market fund, such as SPAXX or VMFXX, holds short-term government debt at a steady $1 a share. PyQxel treats it as cash.',
    note: 'Its interest, recently 3–5% a year, is left out of the simulation, so the cash share slightly understates returns.',
    more: 'It is valued at $1 a share, held at a steady value in simulations, and left out of how the holdings move together, since its price never moves.',
  },
  leftOutHoldings: {
    title: 'Holdings left out',
    what: 'Holdings this analysis could not use, and why: usually a recent listing with under about ten months (201 trading days) of prices, or one with no prices.',
    good: 'Nothing left out, or only a small share of the portfolio.',
    bad: 'A large share left out: the results then say little about the whole portfolio.',
    more: 'In the simulation, the holdings kept are scaled up to stand in for those left out. A longer history does not help a recent listing; it becomes usable once it has traded long enough.',
  },
  historyCut: {
    title: 'History lost to late listings',
    what: 'The simulation and co-movement models need every holding on every day, so they start when the newest holding did. This caps how much history one late listing may cost before it is left out.',
    good: 'About 25–50%: recent listings are left out rather than shrinking everyone’s history to months.',
    bad: '“Keep all” with a holding listed months ago: every figure then rests on a short window that may miss a sell-off.',
    note: 'Measured against the holding with the longest history in the window.',
  },
  monteCarlo: {
    title: 'Simulated outcomes (Monte Carlo)',
    what: 'Thousands of possible future paths for the portfolio, drawn from how the holdings behaved and moved together in the past. The bands show the range.',
    formula:
      'V_T = V_0 × Π_{t=1}^{T} (1 + Σ_i w_i r_{i,t})\nrepeated for thousands of paths of drawn returns r',
    note: 'It assumes the future resembles the past; real outcomes can fall outside every band.',
  },
  simulationPercentiles: {
    title: 'Percentiles',
    what: 'The 5th percentile is the outcome only 5% of paths did worse than; the 50th (median) is the middle; the 95th, one only 5% beat.',
    formula: 'p-th percentile = the outcome p% of paths fall below',
  },
  expectedReturn: {
    title: 'Expected return',
    what: 'The average return across all simulated paths over the horizon.',
    formula: 'E[R] = (1 ÷ N) Σ_{paths} R_{path}',
    note: 'Rare big gains can pull the average above what most paths earn; the median is more typical.',
  },
  medianReturn: {
    title: 'Median return',
    what: 'The middle outcome: half the simulated paths did better, half worse.',
    formula: 'Median = 50th percentile of R over all paths',
  },
  chanceOfLoss: {
    title: 'Chance of a loss',
    what: 'The share of simulated paths that end below the starting value.',
    formula: 'P(loss) = paths with V_T < V_0 ÷ all paths',
    good: 'Lower is safer. Over a month, 30–45% is normal for stocks.',
  },
  valueAtRisk: {
    title: 'Value at Risk (95%)',
    what: 'A loss exceeded in only 5% of simulated paths: in 19 cases out of 20, the loss is smaller.',
    formula: 'VaR_{95} = −(5th percentile of R)',
    bad: 'A larger figure means more money at risk in a bad (but not the worst) case.',
  },
  expectedShortfall: {
    title: 'Expected shortfall (95%)',
    what: 'The average loss in the worst 5% of simulated paths.',
    formula: 'ES_{95} = −mean(R | R ≤ 5th percentile)',
    note: 'Always at least VaR; a big gap means a long tail of severe losses.',
  },
  medianMaxDrawdown: {
    title: 'Median max drawdown',
    what: 'The typical worst peak-to-trough fall along a simulated path.',
    formula: 'median over all paths of max drawdown_{path}',
  },
  copula: {
    title: 'How the holdings move together (copulas)',
    what: 'A model of how the holdings move together, in normal times and in extremes. It compares a Gaussian copula, where crashes are unrelated, with a Student-t copula, where holdings can crash together.',
    formula: 'ρ = sin(π τ ÷ 2)\nτ = each pair’s Kendall’s tau',
    note: 'If the Student-t fits better, diversification may fail exactly when markets fall.',
  },
  kendallTau: {
    title: 'Kendall’s tau',
    what: 'How consistently two holdings move the same way, from −1 (always opposite) through 0 (unrelated) to +1 (always together).',
    formula: 'τ = (concordant pairs − discordant pairs) ÷ all pairs of days',
    good: 'Low or negative between holdings: they diversify each other.',
    bad: 'Above about 0.5: the two largely move as one.',
  },
  ownKendallTau: {
    title: 'Kendall’s tau over the pair’s own history',
    what: 'The same correlation, over every day these two both traded rather than only days every holding traded.',
    note: 'Read it as a check on whether a recent listing’s shorter window changed the picture; the heatmap and copulas use the shared window.',
  },
  tailDependence: {
    title: 'Tail dependence',
    what: 'The chance one holding has an extreme day given the other does: how often they crash (lower tail) or soar (upper tail) together.',
    formula: 'λ_L = lim_{q→0} P(U_1 ≤ q | U_2 ≤ q)',
    bad: 'High lower-tail dependence: losses arrive together, when diversification matters most.',
  },
  shareOfVariance: {
    title: 'Share of variance',
    what: 'How much of the stock’s return swings this factor accounts for. All factors plus the company’s own part add to 100%.',
    formula: 'Share_k = β_k cov(f_k, r) ÷ var(r)',
  },

  // ── AI ──
  aiReport: {
    title: 'AI research report',
    what: 'Claude reads the stock’s prices, statistics, financials, filings, and news, and writes a thesis or risk summary citing those figures.',
    note: 'An AI’s reading of data, not advice: check the figures it cites and what it says it could not see.',
  },
  webSearch: {
    title: 'Let Claude search the web',
    what: 'Before writing, Claude searches the web for what PyQxel’s data cannot show, such as the latest results, analyst actions, deals, and legal news, and cites each source.',
    good: 'Worth it when recent news matters: around earnings, after a market move, or for short holding periods.',
    note: 'On by default. It adds a few cents and about a minute.',
    more: 'Anthropic bills about 1 cent per search, up to six searches, plus the tokens to read the results. A report that searches is always written anew. Outside the app, this choice goes into the prompt: Claude is told to search and name sources, or to keep to PyQxel’s data.',
  },
  refreshSchedule: {
    title: 'How often it refreshes',
    what: 'Which days the refresh runs, at most once a day. The default runs after each New York Stock Exchange session, so every refresh has a new day of prices.',
    good: 'After each trading day, in the evening: closing prices are ready about two hours after the 4 p.m. Eastern close.',
    note: 'A refresh starts on the hourly check after its time.',
    more: 'With an early-morning time, each run takes the day before’s session, so Saturday’s takes Friday’s. Other choices run every night, on weeknights, one day a week or a month, or on calendar days you pick; any weekday can have its own time. Nothing changes on a closed day except the rare Good Friday filing; news is never part of it. Closures for one-off events cannot be known ahead, so they count as open.',
  },
  picksSchedule: {
    title: 'Scheduled picks',
    what: 'Claude makes your picks by itself: weekly picks to hold for a week, monthly picks for a month, or both, with the settings above. Each list joins your saved picks.',
    good: 'Weekly on Sunday, before the market opens, and monthly on the last day of the month.',
    note: 'Turning this on stores your Anthropic and market data keys on PyQxel’s server, encrypted; turning it off deletes them.',
    more: 'Each list is billed to your Anthropic key like one you ask for, within your AI limits. A run never starts during your screener refresh: it waits for it to finish, and when both fall due together, they run together.',
  },
  reviewWithPicks: {
    title: 'Let the review see my latest picks',
    what: 'Gives the review your latest Claude’s Picks, with the screener’s figures, so it can weigh adding them. Unticked, it works from your holdings alone.',
    note: 'On by default. Nothing is fetched for it.',
  },
  picksHoldings: {
    title: 'Show Claude my holdings',
    what: 'Gives Claude what you hold in the portfolios you choose, so it avoids piling into what you already own and notes overlaps.',
    note: 'Nothing extra is fetched. To have your holdings reviewed, use the portfolio review.',
    more: 'Claude sees each security, its shares in each account, its value and weight, and the screener’s figures. It adds to something you own only when that is the best use of the money. A holding the screener does not hold is listed without a value.',
  },
  keyFeatures: {
    title: 'Where this key is used',
    what: 'Each part of PyQxel that can use this key, with a switch and a daily limit, so one part cannot use up what another needs.',
    good: 'Leave everything on with no limit unless a key runs out. Free plans run out fastest on FMP (250 a day), Alpha Vantage (25), and EODHD (20).',
    note: 'These choices follow your account to every device.',
    more: 'Untick a part to keep it off this key; it then uses your other keys, or says which it needs. Used today counts since the provider’s day began (midnight UTC, or Eastern Standard Time for Tiingo). A limit is checked as each request starts, so a page may go a few past it. The nightly refresh and scheduled picks have their own limits in their cards.',
  },
  picksSizing: {
    title: 'Size the picks by',
    what: 'An amount: Claude sizes picks for that much money, in dollars too, with fewer picks for a small sum. Percentages only: shares as percentages, for any sum.',
    note: 'With percentages only, a small sum may not buy every share exactly; check prices before buying.',
  },
  picksWeights: {
    title: 'Percentages',
    what: 'Ideal gives each pick the share Claude thinks best, such as 23%. Clean rounds every share to a multiple of 5%, such as 25%, easier to follow by hand.',
    formula: 'Clean: w_i ∈ {5%, 10%, 15%, …}, Σ w_i = 100%',
  },
  picksAdded: {
    title: 'Also weigh these tickers',
    what: 'Tickers you want Claude to consider beside the screener’s leaders. Each is weighed like every other candidate, and picked only if it is among the best.',
    note: 'Up to 25. A ticker must be in your screener for Claude to see its figures.',
    more: 'The list below shows every ticker the next picks would weigh; search it to check one. A ticker not in your screener can be added to your nightly refresh. Added stocks get their news looked up first, and the screener’s size and trading cutoffs do not apply, so check how thinly a small one trades.',
  },
  savedReviews: {
    title: 'Saved reviews',
    what: 'Every portfolio review Claude has written for you, newest first. Open one to read it again with its data; it costs nothing.',
    note: 'Prices and holdings have likely moved since, so an older review’s orders are a record, not today’s plan.',
  },
  askedFor: {
    title: 'Made for',
    what: 'What this report was asked for: your settings and notes, whether Claude searched the web, the model that wrote it, and its effort.',
    note: 'Effort is how long Claude may think: more weighs the data more carefully but takes longer and costs more.',
    more: 'A report written outside the app names the model Claude said it is, marked “outside the app”, or “not given” if it did not say; no effort is shown, since PyQxel did not set it.',
  },
  outsideSettings: {
    title: 'Effort used',
    what: 'The effort you used outside the app, so the saved analysis says exactly what wrote it. Your choice is remembered.',
    note: 'Claude cannot tell its own effort, so only you can note it, or end the pasted text with a line such as “Effort: high.”',
  },
  additionalContext: {
    title: 'Additional context',
    what: 'Reading for Claude to weigh beside PyQxel’s data: an article, research notes, a transcript, or the last picks’ handoff. Claude treats it as unverified and checks it where it can.',
    good: 'What the data cannot hold: paywalled news, a broker’s research, your own analysis. Up to 60,000 characters.',
    more: 'It is separate from the notes, which say what to tailor the report to. It is remembered on this page until you clear it and goes into the prompt taken outside the app. A report written with it is never reused for a request without it.',
  },
  waitForData: {
    title: 'Wait for more data',
    what: 'How long you will wait for data a provider’s per-minute limit holds back, such as Finnhub’s 60 requests a minute on its free plan.',
    good: 'Five minutes covers a review of about 40 holdings, or picks with news and ratings for their 60 likeliest stocks. For picks, 20–30 minutes adds ratings and insiders for every candidate.',
    note: 'It waits only when it must; with a paid plan or recent data, the report comes as fast as ever.',
    more: 'Each stock’s news is one request; its ratings, earnings surprises, and insider trades three more; each week of the earnings calendar one. Waiting lets PyQxel ask a minute’s worth at a time. With No waiting, what does not fit in the first minute is left out, and the notes say how long it would have taken. The same choice goes into the prompt taken outside the app.',
  },
  accountType: {
    title: 'Account type',
    what: 'What kind of account holds this portfolio: taxable brokerage, an IRA, an employer plan such as a 401(k), a health savings account, or a 529. Reviews use it to weigh taxes.',
    note: 'Saved with the portfolio. Selling at a gain costs tax only in a taxable account.',
  },
  contributionRoom: {
    title: 'Room left to contribute this year',
    what: 'The most new money you may still put into this account this tax year. A review never plans more than this.',
    formula: 'Room = min(yearly limit, earned income) − already contributed',
    note: 'Leave it empty if unsure; the review then gives the yearly limits instead.',
    more: 'The limit is shared across accounts of the same kind, such as all your IRAs. Example: someone who earned $3,500 and put $3,400 into a Roth IRA has $100 of room left.',
  },
  groupReviewMode: {
    title: 'How to review them',
    what: '“As one portfolio”: one plan for the whole, saying which account to change. “Each with its own plan”: a plan per account, planned together, such as holding each kind where it is taxed least.',
    good: 'Each with its own plan costs one review, not one per account.',
  },
  accountCash: {
    title: 'Cash in the account to trade',
    what: 'Cash already in this account, not counting a money-market fund, that its plan may spend.',
    note: 'New cash to invest is separate: entered once and divided among the accounts.',
  },
  accountGoals: {
    title: 'Goals for each account',
    what: 'Give each account its own risk tolerance, horizon, notes, and cash, or use the same goals for all.',
    note: 'New cash is entered once; the review splits it within each account’s contribution room.',
  },
  savedPicks: {
    title: 'Saved Claude’s Picks',
    what: 'Every list Claude made for you, newest first, with its return, the benchmark’s, and the difference. Open one to see it pick by pick.',
    note: 'A list is tracked until its holding period ends, then stays as it was that day.',
  },
  picksPerformance: {
    title: 'How the picks have done',
    what: '$10,000 put into the picks in Claude’s weights and never rebalanced, beside the same money in the benchmark fund. Dividends count.',
    formula: 'V_t = 10,000 × Σ_i w_i × P_{i,t} ÷ P_{i,fill}   (unfilled picks stay cash)',
    good: 'Above the benchmark by more than trading costs and taxes.',
    bad: 'Below the benchmark: owning the index would have done better.',
    note: 'A few weeks say little; judge picks over their whole period and several lists.',
    more: 'Each pick is bought only when its order would have filled, at that price; until then, or if it never fills, its money waits in cash. The benchmark starts at the last close before the picks were made. Measuring stops when the holding period ends.',
  },
  picksReturn: {
    title: 'Picks’ return',
    what: 'The list’s gain or loss since it was made, bought in Claude’s weights when each order would have filled.',
    formula: 'Return = Σ_i w_i × (P_{i,now} ÷ P_{i,fill} − 1)',
    note: 'An unfilled pick counts as cash. A pick that cannot be priced is left out, and the others are scaled up.',
  },
  picksBenchmarkReturn: {
    title: 'Benchmark’s return',
    what: 'The benchmark fund’s return over the same days, dividends included: SPY (S&P 500), QQQ (Nasdaq-100), DIA (Dow), IWM (small caps), or VTI (whole market).',
    formula: 'Return = P_{now} ÷ P_{start} − 1',
    note: 'Choose the one that matches what the picks could have been instead.',
  },
  picksExcess: {
    title: 'Versus the benchmark',
    what: 'How much better or worse the picks did than simply owning the index, in percentage points.',
    formula: 'Excess = picks’ return − benchmark’s return',
    good: 'Above zero: the picks beat the index.',
    bad: 'Below zero: the index did better.',
    note: 'Trading costs and taxes are not counted.',
  },
  picksTracking: {
    title: 'Tracked until',
    what: 'The day the list’s holding period ends. Its return is measured up to then and then stays fixed.',
  },
  pickGain: {
    title: 'Gain since picked',
    what: 'How much the pick gained or lost since its buy order would have filled, dividends included, and what that added to the list.',
    formula: 'Gain = P_{now} ÷ P_{fill} − 1   Added = w × gain',
  },
  webSources: {
    title: 'From the web',
    what: 'Facts Claude found by searching the web, each with the pages it came from. Nothing from the web appears without a source you can open.',
    good: 'Reported by several established outlets, the company itself, or a regulator.',
    bad: 'A single aggregator, forum, or promotional page: treat it as unconfirmed.',
    note: 'Pages can be wrong or dated; open the source before acting on a fact that matters.',
  },
  claudesPicks: {
    title: 'Claude’s Picks',
    what: 'The securities Claude would buy now and hold for your period: five to ten from the screener’s leaders, ranked, with how much of each, why, and when to sell.',
    good: 'Use them as a shortlist: open each one’s page and read its report before buying.',
    bad: 'Buying a list unread, or expecting a short-term pick to work: over weeks, any stock is near a coin toss.',
    note: 'An AI’s reading of past figures, not advice or a forecast. Picks can be concentrated and volatile.',
    more: 'Candidates are the screener’s leaders on size, momentum, recent returns, free cash flow yield, P/E, growth, return on equity, margins, value and small-company tilts, and low volatility, plus the most traded ETFs, each trading at least $5 million a day. Claude is asked to take a view rather than default to index funds.',
  },
  picksHorizon: {
    title: 'Holding period',
    what: 'How long you would hold the picks: one week to sixty years. Short periods favor momentum and upcoming events; long ones favor earnings, growth, and price.',
    good: 'A period that matches when you need the money. Longer is more predictable.',
    bad: 'A few weeks: prices are mostly noise, so expect some picks to lose.',
  },
  picksRisk: {
    title: 'Risk tolerance',
    what: 'How large a swing you could sit through without selling. Conservative leans steady and diversified; aggressive allows concentrated, volatile picks. Unset means moderate.',
    note: 'Higher risk means larger possible losses as well as gains, not a higher return.',
  },
  reviewRisk: {
    title: 'Risk tolerance',
    what: 'How large a drop you could sit through without selling. Conservative leans to steady holdings, funds, bonds, and cash; aggressive accepts more concentration. Unset means moderate.',
    good: 'Conservative if a 15% fall would make you sell, moderate for about 25%, aggressive for 35% or more.',
    note: 'Money you need within a few years usually belongs in the conservative part.',
  },
  pickOrders: {
    title: 'Orders to place',
    what: 'The orders to enter at your broker, in order. Market buys at the next price; limit buys only at its price or lower; a stop-loss sells if the price falls to it.',
    good: 'A buy limit within a day’s usual move usually fills within a few sessions; a stop-loss beyond normal swings avoids being triggered by noise.',
    note: 'Performance follows these orders: a pick counts as bought only when its order would have filled.',
    more: 'A stop-limit waits for the price to rise to its stop, then buys at its limit or lower. Good ’til canceled (GTC) stays open across sessions, up to 180 days at Fidelity; a day order cancels at the close. Fractional shares take only market and day limit orders in market hours, so the fraction is re-entered each morning, guarded by a price alert. A stop-loss can sell below its price when a stock opens lower.',
  },
  pickOrderStatus: {
    title: 'Orders',
    what: 'What the pick’s orders would have done. Bought: filled at “Bought at”. Order open: still waiting. Not filled: canceled, so its money stayed cash. Sold early: a stop or target sold it.',
    note: 'A limit counts as filled when the day’s low reached it; a stop-loss is watched from the day after buying.',
  },
  picksAddedFirst: {
    title: 'Gather data first for',
    what: 'Which stocks the waiting time is spent on first: your added tickers, or the 25 candidates likeliest to be picked.',
    good: 'Your tickers first when you most want them weighed; likeliest first when you added many.',
    note: 'It matters only when the wait is too short for everything.',
  },
  picksSellAtEnd: {
    title: 'Keep picks past the holding period',
    what: 'Normally every pick is sold at the period’s end. Checked, the end is when you next look: Claude says to sell only where that is best, and otherwise how much longer to hold.',
    note: 'Performance is still measured to the period’s end, so lists compare fairly.',
  },
  picksRecurring: {
    title: 'Recurring picks',
    what: 'Check this if you will make a new list each period. Claude then writes a handoff for the next list: what it would keep, the figures it saw, and the conditions to check.',
    good: 'Let the next list see the portfolio holding these picks, so it knows what you still own.',
  },
  picksHandoff: {
    title: 'For your next picks',
    what: 'A note this list wrote for the next: each pick it would keep, its price and figures, why, and what would end the case.',
    good: '“Use in my next picks” puts it in the additional context box.',
  },
  pickKeptPast: {
    title: 'Kept past the period',
    what: 'Claude would not sell this pick at the period’s end; “When to sell” says how much longer to hold it.',
    note: 'Its performance here is still measured to the period’s end.',
  },
  pickConviction: {
    title: 'Conviction',
    what: 'How firmly the figures support this pick: high when the measures that matter agree, medium with a real counterweight, low when the case is thin.',
    note: 'A judgment of the evidence, not a probability of profit.',
  },
  pickWeight: {
    title: 'Share of the money',
    what: 'How much of the money for these picks Claude would put in this one; the shares add up to 100%.',
    formula: 'Dollars_i = w_i × amount to invest',
    good: 'Larger shares for higher conviction and steadier securities.',
    bad: 'Most of the money in one or two volatile stocks: one bad result can undo the rest.',
    note: 'Shares of the money for the picks, not your whole portfolio.',
  },
  pickCandidates: {
    title: 'Candidates',
    what: 'The securities Claude chose from: the screener’s leaders on about a dozen measures. Each trades at least $5 million a day and, for a company, is worth at least $300 million.',
    note: 'Picks are only as current as the screener; company figures can be months old.',
  },
  pickOutsideScreener: {
    title: 'Not in your screener',
    what: 'Claude may pick something the screener lacks, such as a bond or broad market fund, when it clearly suits you better than any candidate.',
    note: 'Look it up by ticker to see its price, costs, and holdings before buying.',
  },
  picksUniverse: {
    title: 'Universe',
    what: 'Which part of the screener to search: an index or size group, everything, or only your latest Claude’s Picks, to compare them side by side.',
    note: 'A pick the screener does not hold is not listed.',
  },
  portfolioReview: {
    title: 'AI portfolio review',
    what: 'Claude reviews your saved holdings (where the risk comes from and how well they diversify) and recommends changes, which the app turns into exact orders.',
    note: 'An AI’s recommendation from past prices, not a licensed adviser’s; check taxes, costs, and prices before trading.',
  },
  reviewHistory: {
    title: 'Price history to measure',
    what: 'How many years of prices the review’s statistics use. It is not how far ahead Claude looks.',
    good: 'Two to five years usually spans both rising and falling markets.',
    bad: 'One year can miss a sell-off; ten may describe companies that have since changed.',
    note: 'Recent listings shorten the window for all holdings, or are left out.',
  },
  fractionalShares: {
    title: 'Fractional order',
    what: 'An order for part of a share, used when your broker trades fractions, or where whole shares would round a trade away.',
    note: 'Some brokers, including Fidelity, take fractional limits only as day orders and no stops; if yours refuses, trade at market or round to whole shares.',
  },
  stance: {
    title: 'Stance',
    what: 'Claude’s overall view from the data it was given: bearish (expects it to do poorly), neutral, or bullish.',
    note: 'A reading of past data, not a forecast; weigh it against the counterpoints.',
  },
  conviction: {
    title: 'Conviction',
    what: 'How strongly the evidence backs the stance. Low means the case is finely balanced or the data are thin.',
  },
  currentDrawdown: {
    title: 'Current drawdown',
    what: 'How far the price sits below its highest close in the window. Zero means it is at a high.',
    formula: 'Drawdown = P_{now} ÷ highest P − 1',
    good: 'Within about −10% of the high.',
    bad: 'Below about −30%: still deep in a fall.',
  },
  diversificationRatio: {
    title: 'Diversification ratio',
    what: 'How much the holdings cancel out each other’s swings. Above 1 means they partly offset.',
    formula: 'DR = Σ_i w_i σ_i ÷ σ_{portfolio}',
    good: 'About 1.3 or more: holdings offset each other well.',
    bad: 'Close to 1: they move largely as one.',
  },
  effectiveHoldings: {
    title: 'Effective holdings',
    what: 'How many equal-sized holdings would be as concentrated as this portfolio.',
    formula: 'N_{eff} = 1 ÷ Σ_i w_i^2\n10 holdings with one at 80% ≈ 1.5',
    good: '10 or more for a stock portfolio.',
    bad: 'Under about 5: a few positions drive most of the result.',
  },
  riskLevel: {
    title: 'Risk level',
    what: 'Claude’s grade of the portfolio’s risk, low to high, from its volatility, drawdowns, and concentration.',
  },
  diversification: {
    title: 'Diversification',
    what: 'Claude’s grade of how well the holdings offset one another, poor to strong, from correlations, sectors, and concentration.',
  },

  // ── Account ──
  aiRequestsHour: {
    title: 'AI requests in the last hour',
    what: 'New AI reports, reviews, and assistant answers in the last hour, counted toward your hourly limit. Reused reports are free.',
  },
  aiRequestsDay: {
    title: 'AI requests in the last day',
    what: 'New AI requests in the last 24 hours, counted toward your daily limit.',
  },
  requestsPerMinute: {
    title: 'Requests per minute',
    what: 'How many requests your account may make to PyQxel each minute, which keeps the server responsive for everyone.',
  },

  // ── News order ──
  marketNewsOrder: {
    title: 'Most important market news',
    what: 'Puts first the headlines most likely to move the whole market, scored on topic, publisher, and age. Newest lists them by time.',
    formula:
      'Score = (0.6 × topic + 0.4 × publisher) × (0.4 + 0.6 × 0.5^{hours ÷ 24})\ntopic and publisher run from 0 to 1',
    note: 'A rule of thumb that reads headlines for key words, so an important story with a plain headline can rank low.',
    more: 'The Fed, interest rates, inflation, jobs, and the economy count most, then big market moves, trade, and large company events. Major publishers such as Reuters, Bloomberg, and The Wall Street Journal count more.',
  },
  companyNewsOrder: {
    title: 'Most important company news',
    what: 'Puts first the headlines most likely to matter for this stock, scored on topic, whether they name the ticker, publisher, and age. Newest lists them by time.',
    formula:
      'Score = (0.6 × topic + 0.4 × publisher) × (0.4 + 0.6 × 0.5^{hours ÷ 48})\ntopic and publisher run from 0 to 1',
    note: 'A rule of thumb that reads headlines for key words, not a judgment of each story.',
    more: 'Results and guidance count most, then analyst changes, deals, buybacks and dividends, leadership, lawsuits and regulators, and drug approvals. A market-wide story that mentions the company can rank high.',
  },

  // ── Nightly screener refresh ──
  nightlyRefresh: {
    title: 'Nightly screener refresh',
    what: 'Rebuilds your own copy of the screener on your own keys, so its numbers come from data you are licensed to see.',
    note: 'While it runs it uses most of your Finnhub allowance, so pick an hour you are not using PyQxel.',
    more: 'Finnhub’s free plan allows about 60 requests a minute and the refresh makes one per stock and ETF: about four hours for everything, minutes for the S&P 500. Paid plans (150 to 900 a minute) finish in a fraction of the time, which also costs less. Free price-history plans cover only some stocks a night: your added tickers first, then your holdings and likely picks, then the largest. Once a week it checks for new ETFs. ETFs have no company figures, so a screen on those leaves them out. Sectors come from SEC industry codes.',
  },
  nightlySharing: {
    title: 'Sharing the server',
    what: 'Refreshes running at the same time split the server’s cost, so each pays less: with 3 running, each pays a third.',
    formula: 'Your cost per second = server price ÷ refreshes running',
    good: 'The busiest hour is usually cheapest; the default, 7:00 PM Eastern, is the likeliest to be shared.',
    note: 'Not guaranteed: others can change their times or turn theirs off.',
    more: 'The server checks hourly and starts every due refresh, up to 8 at once. The count is of other users’ paid refreshes at their next time. A long refresh beside short ones still pays most of its own time.',
  },
  roundShares: {
    title: 'Whole shares or round fractions',
    what: 'Ticked, each holding lands on whole shares or a round fraction (such as 3.5) when that keeps it within 0.25% of the portfolio of its target.',
    formula: '|rounded value − target value| ≤ 0.25% × portfolio value',
    note: 'Rounding never spends more cash than the plan has. Unticked, orders use exact fractions.',
  },
  fractionalBroker: {
    title: 'Fractional shares',
    what: 'Many brokers, such as Fidelity, Schwab, and Robinhood, trade part of a share. Ticked, every order is sized to the exact fraction its target needs.',
    note: 'Brokers limit fractional orders, often to market or day limit orders; check yours trades each security in fractions.',
  },
  nightlyUniverse: {
    title: 'What to refresh',
    what: 'Which securities your screener is rebuilt from: every US stock, every US ETF, Nasdaq, the S&P 500, the Nasdaq-100, or international stocks. A security in several sets is fetched once.',
    good: 'The fewest sets that hold what you look for: the S&P 500 takes about 10 minutes, everything about four hours.',
    more: 'International stocks are foreign companies whose shares or ADRs trade in the US, such as Toyota (TM). Listings abroad need a paid plan. Index members come from index funds’ quarterly SEC reports (IVV, QQQM), published about two months after the quarter, so changes show up a few months late.',
  },
  nightlyCustom: {
    title: 'Also refresh these tickers',
    what: 'Stocks and ETFs refreshed every night on top of the sets you ticked. They stay in your screener even below its size and trading floors, and get price histories first.',
    note: 'Up to 100. A listing abroad, such as 7203.T, is refreshed as its US listing (TM) on free plans.',
  },
  nightlyLastRefresh: {
    title: 'Last refresh',
    what: 'When your screener was last rebuilt, and the date of the closing prices it used.',
    good: 'Within a day or so, on trading days.',
    bad: 'Several days old: check for a failed refresh, such as a rejected key.',
  },
  nightlyStocks: {
    title: 'Stocks in your screener',
    what: 'How many US stocks passed the screener’s floors on market cap, price, and trading volume in your last refresh.',
    note: 'Usually a few thousand for every stock, 500 for the S&P 500. Fewer can mean Finnhub’s limit was reached.',
  },
  nightlyPriced: {
    title: 'With price history',
    what: 'How many of those stocks also have the columns that need a year of prices: 1-month return, momentum, volatility, drawdown, and factor betas.',
    good: 'Close to the number of stocks, which takes a paid price plan.',
    note: 'Free plans allow some a day; for the rest, Finnhub’s figures stand in where they can.',
    more: 'Finnhub’s 52-week return stands in for 12–1 momentum, its three-month volatility for the year’s, and its beta for the market beta. Drawdown and the size, value, and momentum betas stay empty.',
  },
  nightlyNextRefresh: {
    title: 'Next refresh',
    what: 'When your next refresh is due, in your own time. It starts within the hour after.',
  },

  // ── Billing ──
  billing: {
    title: 'Billing',
    what: 'What you pay for the nightly screener refresh: its server time at cost, plus Stripe’s fees on your payments. Everything else is free.',
    good: 'The S&P 500 costs about a cent a night; every US stock and ETF about 25–30 cents.',
    note: 'With neither credit nor a subscription, the refresh pauses; nothing else changes.',
  },
  billingCredit: {
    title: 'Credit',
    what: 'Money paid in advance, which each refresh uses up by what it cost. Shown to a tenth of a cent.',
    good: 'A few months’ worth, so you top up rarely and pay less in fees.',
    bad: 'Below zero: refreshes ran past it. Your next top-up repays it first.',
  },
  billingLast30Days: {
    title: 'Last 30 days',
    what: 'What your refreshes cost in the last 30 days, and how many ran.',
    formula: 'Cost = Σ refresh seconds × Google Cloud’s price ÷ refreshes sharing',
    note: 'Failed refreshes are billed too, usually a fraction of a cent.',
  },
  billingTopUp: {
    title: 'Add credit',
    what: 'Buy $5 to $500 of credit on Stripe’s secure checkout; it appears within seconds. Stripe’s fee is added on top.',
    formula: 'You pay = (credit + $0.30) ÷ (1 − 2.9%)\n$10 of credit costs $10.61',
    good: '$10 to $20 suits most refresh choices; larger top-ups pay less in fees.',
    more: 'A card from outside the US costs Stripe 1.5% more, taken from the credit. Sales tax or VAT is added where PyQxel collects it; Stripe’s fees on it come out of the credit. Unused credit is refunded if you delete your account. Payments from the EU or UK are released uncharged.',
  },
  billingSubscription: {
    title: 'Subscription',
    what: 'Stripe bills your card each week, month, or year for what your refreshes cost, after any credit. The amount varies; there is no fixed price.',
    formula: 'Bill = usage + $0.30 + 3.6% (+ 1.5% for a card from abroad)',
    good: 'Yearly or monthly: the 30-cent part is a large share of a small weekly bill.',
    note: 'Cancel any time on Stripe’s billing page; it ends at the period’s end.',
    more: 'Stripe charges nothing under 50 cents; a smaller bill joins the next. If a payment fails, Stripe retries and your refresh pauses until paid. Where PyQxel collects sales tax or VAT, it is added, with 0.5% more in fees.',
  },
  billingHistory: {
    title: 'History',
    what: 'Your latest payments and charges: credit added, each refresh’s cost, extra Stripe fees, and any refund or dispute.',
    note: 'Receipts and invoices are on Stripe’s billing page.',
  },
  nightlyPriceLimits: {
    title: 'Price histories each key fetches a night',
    what: 'How many stocks each price key fetches a night, one request each, largest first. Each next key takes the stocks the ones before skipped.',
    formula:
      'Stocks with price columns = Σ each key’s nightly limit\n50 on Tiingo + 20 on EODHD = 70',
    good: 'Each free plan’s allowance, unless you pay for more.',
    note: '“All the plan allows” uses up a free key for the day, so pages may show less until it resets.',
  },
  nightlyKeyUse: {
    title: 'What one refresh uses',
    what: 'About how many requests one refresh sends on each key, and what share of its plan’s allowance that is.',
    formula: 'Share = requests per refresh ÷ plan’s daily (or monthly) limit',
    good: 'Well under each daily limit, so pages still have requests left.',
    bad: 'Near 100%: the refresh would use up that key for the day.',
    note: 'After a refresh runs, these are its real counts.',
  },

  // ── Plan usage ──
  planUsage: {
    title: 'Plan usage',
    what: 'How much of this key’s plan is used: each limit, what is used and left, and when it resets.',
    note: 'Tiingo, FMP, and Alpha Vantage report nothing, so PyQxel counts its own requests for them, which misses use elsewhere.',
  },
  usageLimit: {
    title: 'Limit',
    what: 'The most the plan allows in each span of time: requests (EODHD weighs some more), different symbols, or data downloaded.',
  },
  usageUsed: {
    title: 'Used',
    what: 'How much of the limit is used so far, as counted by the provider or by PyQxel.',
  },
  usageLeft: {
    title: 'Left',
    what: 'How much of the limit remains until it resets.',
    formula: 'Left = limit − used',
    good: 'Plenty for the pages you open: about one request per stock shown.',
    bad: 'Near zero: pages fall back to other keys or shorter prices until it resets.',
  },
  usageResets: {
    title: 'Resets',
    what: 'When the current span ends and the limit starts again, on this device’s clock.',
    note: 'FMP and Alpha Vantage do not say when; PyQxel counts their days from midnight UTC.',
  },
  loadEstimate: {
    title: 'Before this page loads',
    what: 'This page asks your keys’ providers for data as soon as it opens. This shows about how many requests that would spend on each key.',
    note: 'Prices PyQxel already keeps for you cost nothing, so the real count is often lower.',
  },
  storedPrices: {
    title: 'Stored prices',
    what: 'Daily prices PyQxel keeps for you alone, fetched on your keys, so later pages fetch only the days since.',
    note: 'Never shown to anyone else, and deleted after 30 days unused, when you delete them here, or with your account.',
  },
  shortHistory: {
    title: 'Shorter history than asked',
    what: 'A provider served fewer years of prices than the backtest asked for, because a key ran out or its plan covers less.',
    note: 'A shorter history can trade very differently. Waiting for the limit to reset brings it back.',
  },
} satisfies Record<string, Info>

export type InfoKey = keyof typeof entries

export const INFO: Record<InfoKey, Info> = entries
