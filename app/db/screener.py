"""Storage of the screened universe, its fundamentals and refresh runs, and screening."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, case, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.db.tables import (
    ScreenerFundamentalsRecord,
    ScreenerRunRecord,
    ScreenerStockRecord,
    as_utc,
)
from app.models.fundamentals import Financials
from app.models.screener import (
    BROAD_MARKET_COUNT,
    LARGE_CAP_COUNT,
    LIQUID_DOLLAR_VOLUME,
    PeerGroupKind,
    RankedStock,
    ScreenedStock,
    ScreenField,
    ScreenRequest,
    ScreenResponse,
    SearchMatch,
    UniverseStatus,
)

RUNNING, SUCCEEDED, FAILED = "running", "succeeded", "failed"
"""States of a refresh run."""

MIN_PEERS: int = 10
"""Fewest other stocks an industry or sector needs to serve as a stock's peer group."""


def _column(field: ScreenField) -> InstrumentedAttribute[Any]:
    """The table column holding ``field``."""
    column: InstrumentedAttribute[Any] = getattr(ScreenerStockRecord, field)
    return column


async def replace_universe(session: AsyncSession, stocks: Iterable[ScreenedStock]) -> int:
    """Replace the screened universe with ``stocks`` in one transaction; return the count."""
    rows = [ScreenerStockRecord(**stock.model_dump()) for stock in stocks]
    await session.execute(delete(ScreenerStockRecord))
    session.add_all(rows)
    await session.commit()
    return len(rows)


async def save_fundamentals(
    session: AsyncSession, financials: Iterable[Financials], refreshed_at: datetime
) -> int:
    """Store each company's financials, replacing any stored earlier; return the count."""
    rows = [
        ScreenerFundamentalsRecord(
            cik=item.cik,
            refreshed_at=refreshed_at,
            latest_period_end=item.latest_period_end,
            financials=item.model_dump(mode="json"),
        )
        for item in financials
    ]
    if rows:
        ciks = [row.cik for row in rows]
        await session.execute(
            delete(ScreenerFundamentalsRecord).where(ScreenerFundamentalsRecord.cik.in_(ciks))
        )
        session.add_all(rows)
    await session.commit()
    return len(rows)


async def load_fundamentals(session: AsyncSession, ciks: Iterable[int]) -> dict[int, Financials]:
    """The stored financials of each of ``ciks`` that has them."""
    wanted = list(set(ciks))
    found: dict[int, Financials] = {}
    # Chunked, since databases limit how many values one IN clause may hold.
    for first in range(0, len(wanted), 500):
        records = await session.scalars(
            select(ScreenerFundamentalsRecord).where(
                ScreenerFundamentalsRecord.cik.in_(wanted[first : first + 500])
            )
        )
        for record in records:
            found[record.cik] = Financials.model_validate(record.financials)
    return found


async def fundamentals_refreshed_at(session: AsyncSession) -> datetime | None:
    """When the stored fundamentals were last refreshed in bulk, if ever."""
    latest = await session.scalar(
        select(func.max(ScreenerRunRecord.started_at)).where(
            ScreenerRunRecord.fundamentals_refreshed.is_(True),
            ScreenerRunRecord.status == SUCCEEDED,
        )
    )
    return None if latest is None else as_utc(latest)


async def previous_share_counts(session: AsyncSession) -> dict[str, float]:
    """Shares outstanding implied by the stored universe's market caps from the provider."""
    rows = await session.execute(
        select(
            ScreenerStockRecord.symbol, ScreenerStockRecord.market_cap, ScreenerStockRecord.price
        ).where(ScreenerStockRecord.market_cap_source == "provider")
    )
    return {symbol: cap / price for symbol, cap, price in rows if price > 0}


async def start_run(session: AsyncSession) -> str:
    """Record a refresh as started; return its ID."""
    run_id = str(uuid4())
    session.add(ScreenerRunRecord(id=run_id, started_at=datetime.now(timezone.utc), status=RUNNING))
    await session.commit()
    return run_id


async def finish_run(
    session: AsyncSession,
    run_id: str,
    *,
    error: str | None = None,
    stocks: int | None = None,
    prices_as_of: date | None = None,
    fundamentals_refreshed: bool = False,
    factor_data_end: date | None = None,
) -> None:
    """Record a refresh as finished: failed with ``error``, or succeeded with its results."""
    run = await session.get(ScreenerRunRecord, run_id)
    if run is None:
        return
    run.finished_at = datetime.now(timezone.utc)
    run.status = FAILED if error else SUCCEEDED
    run.error = error
    run.stocks = stocks
    run.prices_as_of = prices_as_of
    run.fundamentals_refreshed = fundamentals_refreshed
    run.factor_data_end = factor_data_end
    await session.commit()


async def _latest_run(session: AsyncSession, status: str | None = None) -> ScreenerRunRecord | None:
    """The most recently started refresh, optionally only one in ``status``."""
    query = select(ScreenerRunRecord)
    if status is not None:
        query = query.where(ScreenerRunRecord.status == status)
    run: ScreenerRunRecord | None = await session.scalar(
        query.order_by(ScreenerRunRecord.started_at.desc()).limit(1)
    )
    return run


async def universe_status(session: AsyncSession) -> UniverseStatus:
    """The size of the screened universe and what its last refreshes did."""
    stocks = await session.scalar(select(func.count()).select_from(ScreenerStockRecord))
    succeeded = await _latest_run(session, SUCCEEDED)
    latest = await _latest_run(session)
    fundamentals = await fundamentals_refreshed_at(session)
    sectors = await session.scalars(
        select(ScreenerStockRecord.sector)
        .where(ScreenerStockRecord.sector.is_not(None))
        .distinct()
        .order_by(ScreenerStockRecord.sector)
    )
    return UniverseStatus(
        stocks=stocks or 0,
        refreshed_at=(
            None
            if succeeded is None or succeeded.finished_at is None
            else as_utc(succeeded.finished_at)
        ),
        prices_as_of=None if succeeded is None else succeeded.prices_as_of,
        fundamentals_refreshed_at=fundamentals,
        factor_data_end=None if succeeded is None else succeeded.factor_data_end,
        last_error=latest.error if latest is not None and latest.status == FAILED else None,
        sectors=[sector for sector in sectors if sector is not None],
    )


async def screen(session: AsyncSession, request: ScreenRequest) -> ScreenResponse:
    """Return the page of stocks matching ``request``, in its sort order."""
    query = select(ScreenerStockRecord)
    if request.universe == "large_cap":
        query = query.where(ScreenerStockRecord.market_cap_rank <= LARGE_CAP_COUNT)
    elif request.universe == "broad_market":
        query = query.where(ScreenerStockRecord.market_cap_rank <= BROAD_MARKET_COUNT)
    elif request.universe == "liquid":
        query = query.where(ScreenerStockRecord.avg_dollar_volume >= LIQUID_DOLLAR_VOLUME)
    if request.sectors:
        query = query.where(ScreenerStockRecord.sector.in_(request.sectors))
    if request.industries:
        query = query.where(ScreenerStockRecord.industry.in_(request.industries))
    for condition in request.filters:
        column = _column(condition.field)
        query = query.where(column.is_not(None))
        minimum = condition.min
        if condition.field == "pe_ratio" and minimum is None:
            # A P/E bound means a profitable company; screening for losses takes a negative min.
            minimum = 0.0
        if minimum is not None:
            query = query.where(column >= minimum)
        if condition.max is not None:
            query = query.where(column <= condition.max)

    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    order = _column(request.sort.field)
    # A negative P/E is a loss, not a bargain: losses follow every profitable company.
    pe = ScreenerStockRecord.pe_ratio
    losses_last = case((pe.is_(None), 2), (pe < 0, 1), else_=0)
    records = await session.scalars(
        query.order_by(
            *([losses_last] if request.sort.field == "pe_ratio" else []),
            (order.desc() if request.sort.descending else order.asc()).nulls_last(),
            ScreenerStockRecord.symbol,
        )
        .limit(request.limit)
        .offset(request.offset)
    )
    items = [
        RankedStock(
            **ScreenedStock.model_validate(record, from_attributes=True).model_dump(),
            rank=request.offset + position + 1,
        )
        for position, record in enumerate(records)
    ]
    succeeded = await _latest_run(session, SUCCEEDED)
    return ScreenResponse(
        items=items,
        total=total or 0,
        limit=request.limit,
        offset=request.offset,
        refreshed_at=(
            None
            if succeeded is None or succeeded.finished_at is None
            else as_utc(succeeded.finished_at)
        ),
    )


def _like_pattern(text: str) -> str:
    """Escape ``LIKE`` wildcards in ``text`` so it matches literally (escape ``\\``)."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def search_stocks(session: AsyncSession, query: str, limit: int) -> list[SearchMatch]:
    """Return up to ``limit`` stocks whose symbol starts with or name contains ``query``.

    An exact symbol comes first, then symbols starting with ``query``, then names
    containing it; within each, larger companies first. Matching ignores case.
    """
    text = query.strip().lower()
    if not text:
        return []
    escaped = _like_pattern(text)
    symbol = func.lower(ScreenerStockRecord.symbol)
    name = func.lower(ScreenerStockRecord.name)
    starts = symbol.like(f"{escaped}%", escape="\\")
    contains = name.like(f"%{escaped}%", escape="\\")
    relevance = case((symbol == text, 0), (starts, 1), else_=2)
    records = await session.scalars(
        select(ScreenerStockRecord)
        .where(or_(starts, contains))
        .order_by(relevance, ScreenerStockRecord.market_cap.desc())
        .limit(limit)
    )
    return [SearchMatch.model_validate(record, from_attributes=True) for record in records]


async def market_caps(
    session: AsyncSession, symbols: Iterable[str]
) -> dict[str, tuple[str, float]]:
    """The name and market cap of each of ``symbols`` in the screened universe."""
    wanted = sorted(set(symbols))
    found: dict[str, tuple[str, float]] = {}
    # SQLite allows a limited number of bound parameters per statement.
    for start in range(0, len(wanted), 500):
        rows = await session.execute(
            select(
                ScreenerStockRecord.symbol, ScreenerStockRecord.name, ScreenerStockRecord.market_cap
            ).where(ScreenerStockRecord.symbol.in_(wanted[start : start + 500]))
        )
        found.update({symbol: (name, cap) for symbol, name, cap in rows})
    return found


@dataclass(frozen=True)
class PeerGroup:
    """A stock in the screened universe and the stocks it is compared with."""

    stock: ScreenedStock
    kind: PeerGroupKind
    name: str
    peers: list[ScreenedStock]
    universe_size: int
    prices_as_of: date | None


async def peer_group(session: AsyncSession, symbol: str) -> PeerGroup | None:
    """``symbol`` and its peers: its industry, else its sector, else the whole universe.

    The narrowest group with at least :data:`MIN_PEERS` other stocks is used. Returns
    ``None`` when ``symbol`` is not in the screened universe.
    """
    record = await session.get(ScreenerStockRecord, symbol)
    if record is None:
        return None
    stock = ScreenedStock.model_validate(record, from_attributes=True)
    universe_size = await session.scalar(select(func.count()).select_from(ScreenerStockRecord))
    others = ScreenerStockRecord.symbol != symbol

    kind: PeerGroupKind = "universe"
    name = "screened universe"
    condition = others
    groups: tuple[tuple[PeerGroupKind, InstrumentedAttribute[Any], str | None], ...] = (
        ("industry", ScreenerStockRecord.industry, stock.industry),
        ("sector", ScreenerStockRecord.sector, stock.sector),
    )
    for group, column, value in groups:
        if value is None:
            continue
        in_group = and_(others, column == value)
        members = await session.scalar(
            select(func.count()).select_from(ScreenerStockRecord).where(in_group)
        )
        if (members or 0) >= MIN_PEERS:
            kind, name, condition = group, value, in_group
            break

    records = await session.scalars(select(ScreenerStockRecord).where(condition))
    succeeded = await _latest_run(session, SUCCEEDED)
    return PeerGroup(
        stock=stock,
        kind=kind,
        name=name,
        peers=[ScreenedStock.model_validate(peer, from_attributes=True) for peer in records],
        universe_size=universe_size or 1,
        prices_as_of=None if succeeded is None else succeeded.prices_as_of,
    )
