"""Storage of backtest results."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.tables import BacktestRecord, as_utc
from app.models.backtest import BacktestList, BacktestResponse, BacktestSummary


def _summary(record: BacktestRecord) -> BacktestSummary:
    """Build a listing entry from a stored row."""
    return BacktestSummary(
        id=UUID(record.id),
        saved_at=as_utc(record.created_at),
        symbol=record.symbol,
        strategy=record.strategy,
        period=record.period,  # type: ignore[arg-type]
        interval=record.interval,  # type: ignore[arg-type]
        total_return=record.total_return,
        sharpe_ratio=record.sharpe_ratio,
        max_drawdown=record.max_drawdown,
        benchmark_total_return=record.benchmark_total_return,
    )


async def save_backtest(session: AsyncSession, result: BacktestResponse) -> BacktestResponse:
    """Store ``result`` and return it with its new ``id`` and ``saved_at``.

    Raises:
        sqlalchemy.exc.SQLAlchemyError: If the database cannot be written.
    """
    saved = result.model_copy(update={"id": uuid4(), "saved_at": datetime.now(timezone.utc)})
    assert saved.id is not None and saved.saved_at is not None
    session.add(
        BacktestRecord(
            id=str(saved.id),
            created_at=saved.saved_at,
            symbol=saved.symbol,
            strategy=saved.strategy.type,
            period=saved.period,
            interval=saved.interval,
            total_return=saved.metrics.total_return,
            sharpe_ratio=saved.metrics.sharpe_ratio,
            max_drawdown=saved.metrics.max_drawdown,
            benchmark_total_return=saved.benchmark.total_return,
            result=saved.model_dump(mode="json", exclude={"id", "saved_at"}),
        )
    )
    await session.commit()
    return saved


async def get_backtest(session: AsyncSession, backtest_id: UUID) -> BacktestResponse | None:
    """Return the stored backtest with ``backtest_id``, or ``None`` if there is none."""
    record = await session.get(BacktestRecord, str(backtest_id))
    if record is None:
        return None
    return BacktestResponse.model_validate(
        {**record.result, "id": record.id, "saved_at": as_utc(record.created_at)}
    )


async def list_backtests(
    session: AsyncSession,
    symbol: str | None = None,
    strategy: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> BacktestList:
    """Return stored backtests newest first, optionally only one symbol's or strategy's."""
    query = select(BacktestRecord)
    if symbol is not None:
        query = query.where(BacktestRecord.symbol == symbol.upper())
    if strategy is not None:
        query = query.where(BacktestRecord.strategy == strategy)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    records = await session.scalars(
        query.order_by(BacktestRecord.created_at.desc(), BacktestRecord.id)
        .limit(limit)
        .offset(offset)
    )
    return BacktestList(
        items=[_summary(record) for record in records],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


async def delete_backtest(session: AsyncSession, backtest_id: UUID) -> bool:
    """Delete the stored backtest with ``backtest_id``; return whether one existed."""
    deleted = await session.execute(
        delete(BacktestRecord).where(BacktestRecord.id == str(backtest_id))
    )
    await session.commit()
    return bool(deleted.rowcount)  # type: ignore[attr-defined]
