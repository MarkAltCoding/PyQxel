"""Summaries of what insiders and analysts say about a company."""

from datetime import date

from app.models.signals import InsiderSummary, InsiderTrade, MarketSignals, SignalsContext

CONTEXT_RECOMMENDATION_MONTHS: int = 4
"""Months of analyst ratings given to Claude, enough to show a trend."""
CONTEXT_SENTIMENT_MONTHS: int = 6
"""Months of insider sentiment given to Claude."""
INSIDER_TRADES: int = 20
"""Open-market insider trades listed."""


def summarize_insiders(trades: list[InsiderTrade], start: date, end: date) -> InsiderSummary:
    """Total insiders' open-market purchases and sales over ``start`` to ``end``."""
    bought = [trade for trade in trades if trade.shares > 0]
    sold = [trade for trade in trades if trade.shares < 0]
    value_bought = sum(trade.value for trade in bought if trade.value is not None)
    value_sold = -sum(trade.value for trade in sold if trade.value is not None)
    return InsiderSummary(
        start=start,
        end=end,
        purchases=len(bought),
        sales=len(sold),
        insiders_buying=len({trade.name for trade in bought}),
        insiders_selling=len({trade.name for trade in sold}),
        shares_bought=sum(trade.shares for trade in bought),
        shares_sold=-sum(trade.shares for trade in sold),
        value_bought=value_bought,
        value_sold=value_sold,
        net_value=value_bought - value_sold,
    )


def summarize_signals(signals: MarketSignals) -> MarketSignals:
    """``signals`` with its insider trades totaled, and only the newest listed."""
    if signals.insider_window is None:
        return signals
    start, end = signals.insider_window
    return signals.model_copy(
        update={
            "insiders": summarize_insiders(signals.insider_trades, start, end),
            "insider_trades": signals.insider_trades[:INSIDER_TRADES],
        }
    )


def signals_context(signals: MarketSignals) -> SignalsContext:
    """The parts of ``signals`` given to Claude: recent ratings, earnings, and insiders'
    totals rather than each trade."""
    return SignalsContext(
        recommendations=signals.recommendations[:CONTEXT_RECOMMENDATION_MONTHS],
        earnings=signals.earnings,
        next_earnings=signals.next_earnings,
        insiders=signals.insiders,
        insider_sentiment=signals.insider_sentiment[:CONTEXT_SENTIMENT_MONTHS],
    )
