"""Storage of each account's saved portfolios, numbered from 1, and their names."""

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.tables import HoldingRecord, PortfolioRecord
from app.models.portfolio import (
    MAX_PORTFOLIOS,
    HoldingShares,
    PortfolioSummary,
    default_portfolio_name,
)


async def get_holdings(
    session: AsyncSession, user_id: str, portfolio: int = 1
) -> list[HoldingShares]:
    """Return the holdings of ``user_id``'s ``portfolio`` in the order they were saved."""
    records = await session.scalars(
        select(HoldingRecord)
        .where(HoldingRecord.user_id == user_id, HoldingRecord.portfolio == portfolio)
        .order_by(HoldingRecord.position)
    )
    return [HoldingShares(symbol=record.symbol, shares=record.shares) for record in records]


async def get_portfolio_name(session: AsyncSession, user_id: str, portfolio: int = 1) -> str:
    """Return the name of ``user_id``'s ``portfolio``, or its default name."""
    name = await session.scalar(
        select(PortfolioRecord.name).where(
            PortfolioRecord.user_id == user_id, PortfolioRecord.portfolio == portfolio
        )
    )
    return name or default_portfolio_name(portfolio)


async def list_portfolios(session: AsyncSession, user_id: str) -> list[PortfolioSummary]:
    """Every portfolio slot of ``user_id``, with its name and how many holdings it has."""
    names = dict(
        (
            await session.execute(
                select(PortfolioRecord.portfolio, PortfolioRecord.name).where(
                    PortfolioRecord.user_id == user_id
                )
            )
        )
        .tuples()
        .all()
    )
    counts = dict(
        (
            await session.execute(
                select(HoldingRecord.portfolio, func.count())
                .where(HoldingRecord.user_id == user_id)
                .group_by(HoldingRecord.portfolio)
            )
        )
        .tuples()
        .all()
    )
    return [
        PortfolioSummary(
            portfolio=number,
            name=names.get(number) or default_portfolio_name(number),
            holdings=counts.get(number, 0),
        )
        for number in range(1, MAX_PORTFOLIOS + 1)
    ]


async def replace_holdings(
    session: AsyncSession,
    user_id: str,
    holdings: list[HoldingShares],
    portfolio: int = 1,
    name: str | None = None,
) -> None:
    """Save ``holdings`` as the whole of ``user_id``'s ``portfolio``, replacing what was
    there, and rename it when ``name`` is given."""
    await session.execute(
        delete(HoldingRecord).where(
            HoldingRecord.user_id == user_id, HoldingRecord.portfolio == portfolio
        )
    )
    session.add_all(
        HoldingRecord(
            user_id=user_id,
            portfolio=portfolio,
            symbol=holding.symbol,
            shares=holding.shares,
            position=i,
        )
        for i, holding in enumerate(holdings)
    )
    if name is not None:
        await session.merge(PortfolioRecord(user_id=user_id, portfolio=portfolio, name=name))
    await session.commit()
