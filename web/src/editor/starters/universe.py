# A strategy across several symbols returns the weights to hold after each bar's close:
# one column per symbol, each a share of capital (0.5 = half in that symbol, -0.5 = half
# of capital short). Whatever the weights leave over is cash, and the absolute weights may
# add up to at most 2.0 on a bar. The backtest holds each row over the next bar, so a
# signal never trades on the bar that produced it.
#
# bars is a pandas DataFrame on the dates every symbol traded, oldest first, whose columns
# are (field, symbol): bars["Close"] has one column per symbol, and
# bars.xs("GOOG", axis=1, level="symbol") is one symbol's Open, High, Low, Close and
# Volume. Prices are adjusted for splits and dividends. numpy is np and pandas is pd;
# whatever you print() appears below.


def strategy(bars: pd.DataFrame) -> pd.DataFrame:
    close = bars["Close"]

    # Each symbol's return over the last six months (126 daily bars).
    momentum = close.pct_change(126)

    # Hold the best half of the symbols in equal parts, but only those that rose: the
    # rest of the capital waits in cash. With two symbols, that is the stronger one.
    held = max(1, len(close.columns) // 2)
    chosen = (momentum.rank(axis=1, ascending=False) <= held) & (momentum > 0)
    weights = chosen.astype(float) / held

    # Reallocate only at each month's last bar, holding the weights in between.
    months = close.index.to_period("M")
    month_end = pd.Series(months != months.shift(-1), index=close.index)
    weights = weights.where(month_end, np.nan).ffill()

    # Before the first reallocation there are no weights: stay in cash.
    return weights.fillna(0.0)
