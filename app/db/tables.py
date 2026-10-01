"""ORM table definitions for the results database."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, String, Text
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
    ``model`` is the one that wrote it, which differs after a fallback. The full response
    is kept as JSON in ``result``.
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
    expected_return: Mapped[float] = mapped_column(Float)
    probability_of_loss: Mapped[float] = mapped_column(Float)
    value_at_risk_95: Mapped[float] = mapped_column(Float)
    conditional_value_at_risk_95: Mapped[float] = mapped_column(Float)
    result: Mapped[dict[str, Any]] = mapped_column(JSON)
