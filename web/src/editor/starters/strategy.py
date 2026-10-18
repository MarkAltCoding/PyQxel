# A custom strategy returns the position to hold after each bar's close:
#   1.0 fully long, 0.0 flat, -1.0 fully short, up to 2.0 leveraged.
# The backtest holds each position over the next bar, so a signal never trades on the
# bar that produced it.
#
# bars is a pandas DataFrame with Open, High, Low, Close and Volume columns, one row per
# bar, oldest first, on a UTC DatetimeIndex. Prices are adjusted for splits and
# dividends. numpy is np and pandas is pd; whatever you print() appears below.


def strategy(bars: pd.DataFrame) -> pd.Series:
    close = bars["Close"]

    # Long while the 50-bar average is above the 200-bar one, otherwise flat.
    trend = (close.rolling(50).mean() > close.rolling(200).mean()).astype(float)

    # Sized to aim for 15% annualized volatility, never more than fully invested.
    # (252 daily bars make a year; use 52 for weekly bars.)
    volatility = close.pct_change().rolling(20).std() * np.sqrt(252)
    position = trend * (0.15 / volatility).clip(upper=1.0)

    # Rolling windows are NaN until they fill: stay flat until then.
    return position.fillna(0.0)
