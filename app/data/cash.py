"""Recognizing money-market funds, which hold a steady $1 a share and so act as cash.

Their price history is flat or missing (Yahoo serves SPAXX as a single bar), so models
of how prices move cannot use them; portfolio models hold them as cash instead.
"""

import re

MONEY_MARKET_QUOTE_TYPE: str = "MONEYMARKET"
"""How Yahoo classes a money-market fund."""

MONEY_MARKET_PRICE: float = 1.0
"""The share price a money-market fund holds."""

_MONEY_MARKET_SYMBOL = re.compile(r"[A-Z]{3}XX")
"""US mutual fund tickers have five letters ending in X; money-market funds end in XX."""


def is_money_market(symbol: str, quote_type: str | None = None) -> bool:
    """Whether ``symbol`` is a money-market fund, by its provider's class or its ticker."""
    return quote_type == MONEY_MARKET_QUOTE_TYPE or bool(_MONEY_MARKET_SYMBOL.fullmatch(symbol))


def money_market_message(symbol: str) -> str:
    """Why a model of price movements has nothing to fit for money-market fund ``symbol``."""
    return (
        f"{symbol} is a money-market fund: its price holds at $1 a share and its return is "
        "paid as interest, so there is no price movement to model."
    )
