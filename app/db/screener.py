"""Storage of the screened universe, its fundamentals and refresh runs, and screening."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import ColumnElement, and_, case, delete, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core.credentials import current_provider_keys
from app.data.universe import Listing
from app.db.nightly import latest_success
from app.db.tables import (
    ScreenedStockColumns,
    ScreenerCompanyRecord,
    ScreenerFundamentalsRecord,
    ScreenerListingRecord,
    ScreenerRunRecord,
    ScreenerStockRecord,
    UserScreenerStockRecord,
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


def _column(
    field: ScreenField, table: type[ScreenedStockColumns] = ScreenerStockRecord
) -> InstrumentedAttribute[Any]:
    """The column of ``table`` holding ``field``."""
    column: InstrumentedAttribute[Any] = getattr(table, field)
    return column


@dataclass(frozen=True)
class Universe:
    """A screened universe a screen reads: the shared one, or a user's own."""

    table: type[ScreenedStockColumns]
    rows: ColumnElement[bool]
    """Selects the universe's rows in ``table``."""
    refreshed_at: datetime | None
    prices_as_of: date | None
    own: bool
    """Whether it is the user's own, built nightly from their keys."""


async def user_universe(session: AsyncSession, user_id: str) -> Universe | None:
    """``user_id``'s own universe, if a nightly refresh of theirs has succeeded."""
    run = await latest_success(session, user_id)
    if run is None:
        return None
    return Universe(
        table=UserScreenerStockRecord,
        rows=UserScreenerStockRecord.user_id == user_id,
        refreshed_at=None if run.finished_at is None else as_utc(run.finished_at),
        prices_as_of=run.prices_as_of,
        own=True,
    )


async def shared_universe(session: AsyncSession) -> Universe:
    """The universe every account may see, as the last full refresh built it."""
    succeeded = await _latest_run(session, SUCCEEDED, with_universe=True)
    return Universe(
        table=ScreenerStockRecord,
        rows=true(),
        refreshed_at=(
            None
            if succeeded is None or succeeded.finished_at is None
            else as_utc(succeeded.finished_at)
        ),
        prices_as_of=None if succeeded is None else succeeded.prices_as_of,
        own=False,
    )


async def replace_listings(
    session: AsyncSession, listings: Iterable[Listing], shares: dict[str, float]
) -> int:
    """Replace the stored SEC listings, with the shares of each listing in ``shares``."""
    rows = [
        ScreenerListingRecord(
            symbol=listing.symbol,
            cik=listing.cik,
            name=listing.name,
            exchange=listing.exchange,
            shares_outstanding=shares.get(listing.symbol),
        )
        for listing in listings
    ]
    await session.execute(delete(ScreenerListingRecord))
    session.add_all(rows)
    await session.commit()
    return len(rows)


async def load_listings(session: AsyncSession) -> list[tuple[Listing, float | None]]:
    """The stored SEC listings, each with its shares outstanding when known."""
    records = await session.scalars(
        select(ScreenerListingRecord).order_by(ScreenerListingRecord.symbol)
    )
    return [
        (
            Listing(symbol=r.symbol, cik=r.cik, name=r.name, exchange=r.exchange),
            r.shares_outstanding,
        )
        for r in records
    ]


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


async def foreign_companies(session: AsyncSession) -> set[int]:
    """The CIKs of foreign companies among those with stored financials: each that files
    as a foreign private issuer (20-F or 40-F), or reports in a currency other than the
    dollar. Only those two fields are read, not every company's whole financials."""
    rows = await session.execute(
        select(
            ScreenerFundamentalsRecord.cik,
            ScreenerFundamentalsRecord.financials["currency"].as_string(),
            ScreenerFundamentalsRecord.financials["foreign_issuer"].as_string(),
        )
    )
    return {
        cik
        for cik, currency, foreign in rows
        # Postgres reads a stored true as "true", SQLite as 1.
        if (currency and currency != "USD") or str(foreign).lower() in ("true", "1")
    }


async def save_companies(
    session: AsyncSession, codes: dict[int, tuple[int | None, str | None]], fetched_at: datetime
) -> None:
    """Store each company's SEC industry code and title, replacing any stored earlier."""
    for cik, (sic, description) in codes.items():
        record = await session.get(ScreenerCompanyRecord, cik)
        if record is None:
            record = ScreenerCompanyRecord(cik=cik)
            session.add(record)
        record.sic = sic
        record.sic_description = description[:160] if description else None
        record.fetched_at = fetched_at
    await session.commit()


async def load_companies(
    session: AsyncSession, ciks: Iterable[int]
) -> dict[int, tuple[int | None, str | None, datetime]]:
    """Each stored company's SEC industry code, its title, and when it was read."""
    wanted = sorted(set(ciks))
    found: dict[int, tuple[int | None, str | None, datetime]] = {}
    # Databases limit how many values one IN clause may hold.
    for start in range(0, len(wanted), 500):
        records = await session.scalars(
            select(ScreenerCompanyRecord).where(
                ScreenerCompanyRecord.cik.in_(wanted[start : start + 500])
            )
        )
        found.update({r.cik: (r.sic, r.sic_description, as_utc(r.fetched_at)) for r in records})
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
    return {symbol: cap / price for symbol, cap, price in rows if cap and price > 0}


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
    licensed: bool = False,
) -> None:
    """Record a refresh as finished: failed with ``error``, or succeeded with its results,
    and whether it used licensed market data alone."""
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
    run.licensed = licensed
    await session.commit()


async def _latest_run(
    session: AsyncSession, status: str | None = None, with_universe: bool = False
) -> ScreenerRunRecord | None:
    """The most recently started refresh, optionally only one in ``status``, and only one
    that built the shared universe rather than just the SEC first stage."""
    query = select(ScreenerRunRecord)
    if status is not None:
        query = query.where(ScreenerRunRecord.status == status)
    if with_universe:
        query = query.where(ScreenerRunRecord.stocks.is_not(None))
    run: ScreenerRunRecord | None = await session.scalar(
        query.order_by(ScreenerRunRecord.started_at.desc()).limit(1)
    )
    return run


async def universe_is_licensed(session: AsyncSession) -> bool:
    """Whether the stored universe was built from licensed market data alone, and so may be
    shown to every account; ``False`` when it has never been built."""
    succeeded = await _latest_run(session, SUCCEEDED, with_universe=True)
    return succeeded is not None and succeeded.licensed


async def shows_universe(session: AsyncSession) -> bool:
    """Whether the current request may be shown the stored universe: always when it was
    built from licensed data, and otherwise only to an account allowed to see unlicensed
    data (see :class:`app.core.credentials.ProviderKeys`)."""
    return current_provider_keys().unlicensed or await universe_is_licensed(session)


async def universe_status(
    session: AsyncSession, universe: Universe | None = None
) -> UniverseStatus:
    """The size of ``universe`` (the shared one by default) and what refreshed it."""
    universe = universe or await shared_universe(session)
    table = universe.table
    sector: InstrumentedAttribute[Any] = getattr(table, "sector")
    stocks = await session.scalar(select(func.count()).select_from(table).where(universe.rows))
    latest = await _latest_run(session)
    fundamentals = await fundamentals_refreshed_at(session)
    succeeded = None if universe.own else await _latest_run(session, SUCCEEDED, with_universe=True)
    sectors = await session.scalars(
        select(sector).where(universe.rows, sector.is_not(None)).distinct().order_by(sector)
    )
    return UniverseStatus(
        stocks=stocks or 0,
        refreshed_at=universe.refreshed_at,
        prices_as_of=universe.prices_as_of,
        fundamentals_refreshed_at=fundamentals,
        factor_data_end=None if succeeded is None else succeeded.factor_data_end,
        last_error=(
            None if universe.own or latest is None or latest.status != FAILED else latest.error
        ),
        sectors=[name for name in sectors if name is not None],
        own=universe.own,
    )


async def universe_listings(session: AsyncSession, universe: Universe) -> list[Listing]:
    """The companies' listings in ``universe``, to match an index's holdings against when
    the SEC's listings have not been stored."""
    table = universe.table
    rows = await session.execute(
        select(
            getattr(table, "symbol"),
            getattr(table, "cik"),
            getattr(table, "name"),
            getattr(table, "exchange"),
        ).where(universe.rows, getattr(table, "cik").is_not(None))
    )
    return [
        Listing(symbol=symbol, cik=cik, name=name, exchange=exchange)
        for symbol, cik, name, exchange in rows
    ]


async def index_symbols(
    session: AsyncSession, universe: Universe, classes: Mapping[int, int]
) -> set[str]:
    """The symbols in ``universe`` of the companies an index holds, ``classes`` giving
    how many of each company's share classes it holds (CIK -> count): those most traded,
    so Alphabet's two classes are kept and a company's notes or preferreds are not."""
    table = universe.table
    cik: InstrumentedAttribute[Any] = getattr(table, "cik")
    rows = await session.execute(
        select(getattr(table, "symbol"), cik, getattr(table, "avg_dollar_volume")).where(
            universe.rows, cik.in_(list(classes))
        )
    )
    by_company: dict[int, list[tuple[float, str]]] = {}
    for symbol, company, volume in rows:
        by_company.setdefault(company, []).append((-(volume or 0.0), symbol))
    return {
        symbol
        for company, listed in by_company.items()
        for _, symbol in sorted(listed)[: classes.get(company, 1)]
    }


async def screen(
    session: AsyncSession,
    request: ScreenRequest,
    universe: Universe | None = None,
    index_classes: Mapping[int, int] | None = None,
) -> ScreenResponse:
    """Return the page of stocks in ``universe`` (the shared one by default) matching
    ``request``, in its sort order. A screen of the S&P 500 needs ``index_classes``, the
    share classes of each company in it (CIK -> count).

    Results are counted, ranked, and paged by company, so a page of 50 holds 50
    companies, with the matching share classes of each together under one rank."""
    universe = universe or await shared_universe(session)
    table = universe.table

    def column(field: str) -> InstrumentedAttribute[Any]:
        found: InstrumentedAttribute[Any] = getattr(table, field)
        return found

    query = select(table).where(universe.rows)
    if request.universe == "sp500":
        symbols = await index_symbols(session, universe, index_classes or {})
        query = query.where(column("symbol").in_(sorted(symbols)))
    elif request.universe == "nasdaq":
        query = query.where(column("exchange") == "Nasdaq")
    elif request.universe == "large_cap":
        query = query.where(column("market_cap_rank") <= LARGE_CAP_COUNT)
    elif request.universe == "broad_market":
        query = query.where(column("market_cap_rank") <= BROAD_MARKET_COUNT)
    elif request.universe == "liquid":
        query = query.where(column("avg_dollar_volume") >= LIQUID_DOLLAR_VOLUME)
    if request.asset_types:
        query = query.where(column("asset_type").in_(request.asset_types))
    if request.sectors:
        query = query.where(column("sector").in_(request.sectors))
    if request.industries:
        query = query.where(column("industry").in_(request.industries))
    for condition in request.filters:
        field = _column(condition.field, table)
        query = query.where(field.is_not(None))
        minimum = condition.min
        if condition.field == "pe_ratio" and minimum is None:
            # A P/E bound means a profitable company; screening for losses takes a negative min.
            minimum = 0.0
        if minimum is not None:
            query = query.where(field >= minimum)
        if condition.max is not None:
            query = query.where(field <= condition.max)

    order = _column(request.sort.field, table)
    # A negative P/E is a loss, not a bargain: losses follow every profitable company.
    pe = column("pe_ratio")
    losses_last = case((pe.is_(None), 2), (pe < 0, 1), else_=0)
    matches = await session.execute(
        query.with_only_columns(column("symbol"), column("cik")).order_by(
            *([losses_last] if request.sort.field == "pe_ratio" else []),
            (order.desc() if request.sort.descending else order.asc()).nulls_last(),
            column("symbol"),
        )
    )
    # A company's share classes, such as Alphabet's GOOGL and GOOG, are one result: the
    # company takes the place of its first class in the order, and its other classes
    # follow it there, sharing its rank.
    companies: dict[int | str, list[str]] = {}
    for symbol, cik in matches:
        companies.setdefault(symbol if cik is None else cik, []).append(symbol)
    page = list(companies.values())[request.offset : request.offset + request.limit]
    wanted = [symbol for classes in page for symbol in classes]
    found = {
        getattr(record, "symbol"): record
        for record in await session.scalars(
            select(table).where(universe.rows, column("symbol").in_(wanted))
        )
    }
    items = [
        RankedStock(
            **ScreenedStock.model_validate(found[symbol], from_attributes=True).model_dump(),
            rank=request.offset + position + 1,
        )
        for position, classes in enumerate(page)
        for symbol in classes
        if symbol in found
    ]
    return ScreenResponse(
        items=items,
        total=len(companies),
        limit=request.limit,
        offset=request.offset,
        refreshed_at=universe.refreshed_at,
        own=universe.own,
    )


def _like_pattern(text: str) -> str:
    """Escape ``LIKE`` wildcards in ``text`` so it matches literally (escape ``\\``)."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def search_stocks(
    session: AsyncSession, query: str, limit: int, details: bool = True
) -> list[SearchMatch]:
    """Return up to ``limit`` stocks whose symbol starts with or name contains ``query``.

    An exact symbol comes first, then symbols starting with ``query``, then names
    containing it; within each, larger companies first. Matching ignores case. Without
    ``details``, matches carry only their symbol, name, and exchange, which come from the
    SEC's list of listings, in alphabetical order within each group.
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
    size = ScreenerStockRecord.market_cap.desc() if details else ScreenerStockRecord.symbol
    records = await session.scalars(
        select(ScreenerStockRecord)
        .where(or_(starts, contains))
        .order_by(relevance, size)
        .limit(limit)
    )
    matches = [SearchMatch.model_validate(record, from_attributes=True) for record in records]
    if details:
        return matches
    return [match.model_copy(update={"sector": None, "market_cap": None}) for match in matches]


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
        found.update({symbol: (name, cap) for symbol, name, cap in rows if cap is not None})
    return found


@dataclass(frozen=True)
class PeerGroup:
    """A stock in the screened universe and the stocks it is compared with."""

    stock: ScreenedStock
    market_cap_rank: int
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
    if stock.market_cap_rank is None:
        # An ETF is no company, so it has no peers among companies.
        return None
    universe_size = await session.scalar(select(func.count()).select_from(ScreenerStockRecord))
    # The stock's other share classes are the same company, not a peer.
    others = (
        ScreenerStockRecord.symbol != symbol
        if stock.cik is None
        else and_(
            ScreenerStockRecord.symbol != symbol,
            or_(ScreenerStockRecord.cik.is_(None), ScreenerStockRecord.cik != stock.cik),
        )
    )

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
    succeeded = await _latest_run(session, SUCCEEDED, with_universe=True)
    return PeerGroup(
        stock=stock,
        market_cap_rank=stock.market_cap_rank,
        kind=kind,
        name=name,
        peers=[ScreenedStock.model_validate(peer, from_attributes=True) for peer in records],
        universe_size=universe_size or 1,
        prices_as_of=None if succeeded is None else succeeded.prices_as_of,
    )


@dataclass(frozen=True)
class CompanyFigures:
    """The stocks a request may see figures of, for weighting a fund's holdings: those of
    the user's own universe, else of the shared one when it may be shown, and the SEC's
    names of every listed company, which are public."""

    stocks: list[ScreenedStock]
    refreshed_at: datetime | None
    own: bool
    listings: list[tuple[str, str]]
    """(symbol, name) of every listed company on the SEC's list, for matching names."""


async def company_figures(session: AsyncSession) -> CompanyFigures:
    """The figures the current request may be shown, and the SEC's listed company names."""
    user_id = current_provider_keys().user_id
    universe = None if user_id is None else await user_universe(session, user_id)
    if universe is None and await shows_universe(session):
        universe = await shared_universe(session)
    stocks: list[ScreenedStock] = []
    if universe is not None:
        records = await session.scalars(
            select(universe.table).where(universe.rows, universe.table.asset_type == "stock")
        )
        stocks = [ScreenedStock.model_validate(r, from_attributes=True) for r in records]
    listings = [
        (symbol, name)
        for symbol, name in await session.execute(
            select(ScreenerListingRecord.symbol, ScreenerListingRecord.name)
        )
    ]
    return CompanyFigures(
        stocks=stocks,
        refreshed_at=None if universe is None else universe.refreshed_at,
        own=universe is not None and universe.own,
        listings=listings,
    )
