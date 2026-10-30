# A strategy across tickers: name any tickers in strings and their prices are fetched for
# you, so there is no symbol to pick first. Those listed in SYMBOLS come first; the page
# shows every ticker found, and you can drop or add one there.
SYMBOLS = ["SPY", "QQQ", "TLT", "GLD"]
# "^VIX" is read only as a signal: a ticker the weights leave out is never held.
FEAR = "^VIX"

# bars is a pandas DataFrame on the dates every ticker traded, oldest first, whose
# columns are (field, symbol): bars["Close"]["SPY"] is SPY's close, and
# bars.xs("SPY", axis=1, level="symbol") its Open, High, Low, Close and Volume. Prices
# are adjusted for splits and dividends. numpy is np and pandas is pd; whatever you
# print() appears below.
#
# Return the weights to hold after each bar's close, one column per ticker you trade:
# each a share of capital (0.5 = half in it, -0.5 = half of capital short). Whatever the
# weights leave over is cash, and the absolute weights may add up to at most 2.0 on a
# bar. The backtest holds each row over the next bar, so a signal never trades on the
# bar that produced it.


def strategy(bars: pd.DataFrame) -> pd.DataFrame:
    close = bars["Close"][SYMBOLS]

    # Each ticker's return over the last six months (126 daily bars).
    momentum = close.pct_change(126)

    # Hold the best two that rose, in equal parts; the rest of the capital waits in cash.
    chosen = (momentum.rank(axis=1, ascending=False) <= 2) & (momentum > 0)
    weights = chosen.astype(float) / 2

    # When fear is high (the VIX above 30), halve every position.
    calm = bars["Close"][FEAR] <= 30
    weights = weights.mul(calm.map({True: 1.0, False: 0.5}), axis=0)

    # Reallocate only at each month's last bar, holding the weights in between.
    months = close.index.to_period("M")
    month_end = pd.Series(months != months.shift(-1), index=close.index)
    weights = weights.where(month_end, np.nan).ffill()

    # Before the first reallocation there are no weights: stay in cash.
    return weights.fillna(0.0)
