"""ORM table definitions for the results database."""

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, Index, Integer, String, Text, false
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def as_utc(moment: datetime) -> datetime:
    """Return a stored ``moment`` as aware UTC; SQLite hands back naive datetimes."""
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment


class Base(DeclarativeBase):
    """Declarative base shared by every table."""


class BacktestRecord(Base):
    """One stored backtest.

    The full response is kept as JSON in ``result``; the columns beside it copy the
    fields used to filter and list results, so listing does not parse every curve.
    """

    __tablename__ = "backtests"
    __table_args__ = (Index("ix_backtests_symbol_created_at", "symbol", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    symbol: Mapped[str] = mapped_column(String(16))
    strategy: Mapped[str] = mapped_column(String(32))
    period: Mapped[str] = mapped_column(String(8))
    interval: Mapped[str] = mapped_column(String(8))
    total_return: Mapped[float] = mapped_column(Float)
    sharpe_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown: Mapped[float] = mapped_column(Float)
    benchmark_total_return: Mapped[float] = mapped_column(Float)
    result: Mapped[dict[str, Any]] = mapped_column(JSON)


class AnalysisRecord(Base):
    """One AI-written analysis, kept so the same request can reuse it instead of paying again.

    ``requested_model`` and ``effort`` are the settings the report was asked for with;
    ``model`` is the one that wrote it, which differs after a fallback.
    ``context_version`` is the version of the data it was written from, so reports
    written from older, thinner contexts are not reused. The full response is kept as
    JSON in ``result``.
    """

    __tablename__ = "analyses"
    __table_args__ = (
        Index(
            "ix_analyses_reuse",
            "symbol",
            "kind",
            "period",
            "include_filings",
            "requested_model",
            "effort",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    symbol: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(16))
    period: Mapped[str] = mapped_column(String(8))
    include_filings: Mapped[bool] = mapped_column(Boolean)
    requested_model: Mapped[str] = mapped_column(String(64))
    effort: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(64))
    context_version: Mapped[int] = mapped_column(Integer, server_default="1")
    headline: Mapped[str] = mapped_column(Text)
    result: Mapped[dict[str, Any]] = mapped_column(JSON)


class SimulationRecord(Base):
    """One stored Monte Carlo simulation.

    ``symbols`` holds the portfolio's symbols as ``,A,B,`` so one can be matched with a
    portable ``LIKE``. The full response is kept as JSON in ``result``.
    """

    __tablename__ = "simulations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    symbols: Mapped[str] = mapped_column(Text)
    horizon: Mapped[int] = mapped_column(Integer)
    paths: Mapped[int] = mapped_column(Integer)
    dependence: Mapped[str] = mapped_column(String(16))
    marginals: Mapped[str] = mapped_column(String(16))
    rebalancing: Mapped[str] = mapped_column(String(16), server_default="daily")
    expected_return: Mapped[float] = mapped_column(Float)
    probability_of_loss: Mapped[float] = mapped_column(Float)
    value_at_risk_95: Mapped[float] = mapped_column(Float)
    conditional_value_at_risk_95: Mapped[float] = mapped_column(Float)
    result: Mapped[dict[str, Any]] = mapped_column(JSON)


class ScreenerStockRecord(Base):
    """One stock in the screened universe and its precomputed metrics.

    The table holds one snapshot: each refresh replaces every row. Metric columns are
    named as :data:`~app.models.screener.ScreenField` names them.
    """

    __tablename__ = "screener_stocks"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    exchange: Mapped[str] = mapped_column(String(16))
    sector: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    industry: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cik: Mapped[int] = mapped_column(Integer, index=True)
    market_cap_source: Mapped[str] = mapped_column(String(16))
    market_cap_rank: Mapped[int] = mapped_column(Integer, index=True)
    fundamentals_period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    price: Mapped[float] = mapped_column(Float)
    market_cap: Mapped[float] = mapped_column(Float)
    avg_dollar_volume: Mapped[float] = mapped_column(Float)
    pe_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_to_sales: Mapped[float | None] = mapped_column(Float, nullable=True)
    ev_to_ebitda: Mapped[float | None] = mapped_column(Float, nullable=True)
    fcf_yield: Mapped[float | None] = mapped_column(Float, nullable=True)
    gross_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    operating_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_on_equity: Mapped[float | None] = mapped_column(Float, nullable=True)
    revenue_growth: Mapped[float | None] = mapped_column(Float, nullable=True)
    earnings_growth: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_1m: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_6m: Mapped[float | None] = mapped_column(Float, nullable=True)
    momentum_12_1: Mapped[float | None] = mapped_column(Float, nullable=True)
    volatility: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown: Mapped[float | None] = mapped_column(Float, nullable=True)
    beta_market: Mapped[float | None] = mapped_column(Float, nullable=True)
    beta_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    beta_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    beta_momentum: Mapped[float | None] = mapped_column(Float, nullable=True)
    factor_r_squared: Mapped[float | None] = mapped_column(Float, nullable=True)


class ScreenerFundamentalsRecord(Base):
    """A company's normalized financials, refreshed weekly for the screened universe.

    ``financials`` holds a :class:`~app.models.fundamentals.Financials` as JSON.
    """

    __tablename__ = "screener_fundamentals"

    cik: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    latest_period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    financials: Mapped[dict[str, Any]] = mapped_column(JSON)


class ScreenerRunRecord(Base):
    """One refresh of the screened universe, kept so its status and failures can be read."""

    __tablename__ = "screener_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    stocks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prices_as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    fundamentals_refreshed: Mapped[bool] = mapped_column(Boolean, server_default=false())
    factor_data_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
