"""Storage of Monte Carlo portfolio simulations."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.tables import SimulationRecord, as_utc
from app.models.simulation import SimulationList, SimulationOverview, SimulationResponse


def _symbol_key(symbols: list[str]) -> str:
    """Encode symbols as ``,A,B,`` so each can be matched with ``LIKE '%,A,%'``."""
    return "," + ",".join(symbols) + ","


def _listing(record: SimulationRecord) -> SimulationOverview:
    """Build a listing entry from a stored row."""
    return SimulationOverview(
        id=UUID(record.id),
        saved_at=as_utc(record.created_at),
        symbols=record.symbols.strip(",").split(","),
        horizon=record.horizon,
        paths=record.paths,
        dependence=record.dependence,  # type: ignore[arg-type]
        marginals=record.marginals,  # type: ignore[arg-type]
        rebalancing=record.rebalancing,  # type: ignore[arg-type]
        expected_return=record.expected_return,
        probability_of_loss=record.probability_of_loss,
        value_at_risk_95=record.value_at_risk_95,
        conditional_value_at_risk_95=record.conditional_value_at_risk_95,
    )


async def save_simulation(
    session: AsyncSession, user_id: str, result: SimulationResponse
) -> SimulationResponse:
    """Store ``result`` for ``user_id`` and return it with its new ``id`` and ``saved_at``.

    Raises:
        sqlalchemy.exc.SQLAlchemyError: If the database cannot be written.
    """
    saved = result.model_copy(update={"id": uuid4(), "saved_at": datetime.now(timezone.utc)})
    assert saved.id is not None and saved.saved_at is not None
    summary = saved.simulation
    at_95 = next(measure for measure in summary.risk if measure.confidence == 0.95)
    session.add(
        SimulationRecord(
            id=str(saved.id),
            user_id=user_id,
            created_at=saved.saved_at,
            symbols=_symbol_key([holding.symbol for holding in saved.holdings]),
            horizon=summary.horizon,
            paths=summary.paths,
            dependence=summary.dependence,
            marginals=summary.marginals,
            rebalancing=summary.rebalancing,
            expected_return=summary.expected_return,
            probability_of_loss=summary.probability_of_loss,
            value_at_risk_95=at_95.value_at_risk,
            conditional_value_at_risk_95=at_95.conditional_value_at_risk,
            result=saved.model_dump(mode="json", exclude={"id", "saved_at"}),
        )
    )
    await session.commit()
    return saved


async def get_simulation(
    session: AsyncSession, user_id: str, simulation_id: UUID
) -> SimulationResponse | None:
    """Return ``user_id``'s stored simulation with ``simulation_id``, or ``None``."""
    record = await session.get(SimulationRecord, str(simulation_id))
    if record is None or record.user_id != user_id:
        return None
    return SimulationResponse.model_validate(
        {**record.result, "id": record.id, "saved_at": as_utc(record.created_at)}
    )


async def list_simulations(
    session: AsyncSession,
    user_id: str,
    symbol: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> SimulationList:
    """Return ``user_id``'s stored simulations newest first, optionally those holding
    ``symbol``."""
    query = select(SimulationRecord).where(SimulationRecord.user_id == user_id)
    if symbol is not None:
        query = query.where(SimulationRecord.symbols.like(f"%,{symbol.upper()},%"))
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    records = await session.scalars(
        query.order_by(SimulationRecord.created_at.desc(), SimulationRecord.id)
        .limit(limit)
        .offset(offset)
    )
    return SimulationList(
        items=[_listing(record) for record in records],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


async def delete_simulation(session: AsyncSession, user_id: str, simulation_id: UUID) -> bool:
    """Delete ``user_id``'s stored simulation with ``simulation_id``; return whether they had
    it."""
    deleted = await session.execute(
        delete(SimulationRecord).where(
            SimulationRecord.id == str(simulation_id), SimulationRecord.user_id == user_id
        )
    )
    await session.commit()
    return bool(deleted.rowcount)  # type: ignore[attr-defined]
