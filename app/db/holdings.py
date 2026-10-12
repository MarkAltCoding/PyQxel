"""Storage of each account's saved portfolio."""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.tables import HoldingRecord
from app.models.portfolio import HoldingShares


async def get_holdings(session: AsyncSession, user_id: str) -> list[HoldingShares]:
    """Return ``user_id``'s holdings in the order they were saved."""
    records = await session.scalars(
        select(HoldingRecord)
        .where(HoldingRecord.user_id == user_id)
        .order_by(HoldingRecord.position)
    )
    return [HoldingShares(symbol=record.symbol, shares=record.shares) for record in records]


async def replace_holdings(
    session: AsyncSession, user_id: str, holdings: list[HoldingShares]
) -> None:
    """Save ``holdings`` as ``user_id``'s whole portfolio, replacing what was there."""
    await session.execute(delete(HoldingRecord).where(HoldingRecord.user_id == user_id))
    session.add_all(
        HoldingRecord(user_id=user_id, symbol=holding.symbol, shares=holding.shares, position=i)
        for i, holding in enumerate(holdings)
    )
    await session.commit()
