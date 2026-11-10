"""Refresh the own screeners of the users who opted in to a nightly refresh.

Run it with::

    python -m app.jobs.nightly

In production the service starts it only when some user's refresh is due, on Cloud
Scheduler's hourly call (:mod:`app.api.internal`); running it when no one is due does
nothing.

Each run refreshes every user whose chosen time has come since their last refresh (see
:func:`app.db.nightly.due_refreshes`), several at once, each on their own keys alone:

1. Starts from the shared SEC first stage that :mod:`app.jobs.screener` stores: every
   listed common stock, its shares outstanding, and its company's financials. A user who
   chose a smaller universe keeps only the stocks listed on Nasdaq, or the companies in
   the S&P 500 or the Nasdaq-100 (:mod:`app.data.index_members`), and gets no ETFs, so
   the refresh sends far fewer requests and finishes sooner.
2. Reads each stock's market cap, valuation, margins, growth, 26- and 52-week returns,
   three-month volatility, beta, and volume from Finnhub's free basic financials on the
   user's Finnhub key, one request per stock, paced under the free plan's limit: about an
   hour and a half for every listed stock.
3. Keeps stocks above the market cap floor, and fetches daily prices for them, largest
   first, on the price-history keys they let it use (Tiingo, FMP, EODHD, or Alpha
   Vantage, cheapest first), each key up to the number the user chose for it, or until
   its plan stops answering for the night. The requests sent on each key are recorded
   with the run. Those stocks get the price-history columns: one-month return, 12-1
   momentum, volatility, drawdown, and Carhart factor betas. The rest keep Finnhub's stand-ins (see
   :func:`app.stats.screening.user_screen_metrics`), and those whose price is unknown
   take a Finnhub quote.
4. Adds the ETFs listed on US exchanges. Once every :data:`ETF_SCAN_DAYS` days it checks
   them all, from Finnhub's free symbol list; on the other nights only those already in
   the user's universe, so a refresh takes about two hours rather than four, and a new or
   newly traded ETF joins within the week. Each costs one request for its basic
   financials (its returns, volatility, beta, and volume; an ETF has no market cap or
   company figures). Those that could clear the dollar
   volume floor even at their 52-week high share the price histories with the stocks, in
   proportion to their number, most traded first, and the rest take a Finnhub quote.
5. Takes sectors and industries from the companies' SEC industry codes, keeps stocks and
   ETFs above the price and dollar volume floors, and replaces the user's own universe
   with them, shown only to that user.

Each refresh runs in its own task with only its user's keys and the same data access as
that user's own requests (see :mod:`app.core.credentials`), so no user's data reaches
another.

On a server that bills (``STRIPE_SECRET_KEY`` set), a refresh runs only for a user with
credit left or a usage subscription, and each costs its user its share of the job's time
at Cloud Run's rates (:func:`run_cost_micros`), paid as :mod:`app.db.billing` explains.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import sees_unlicensed_data, uses_restricted_sources, uses_yfinance
from app.core.config import get_settings
from app.core.credentials import use_provider_keys
from app.core.redact import install_log_redaction
from app.core.text import listed as listed_text
from app.data import usage
from app.data.exchange_rates import fetch_exchange_rates
from app.data.factors import fetch_factors
from app.data.fetcher import DataFetchError, symbol_kind, us_listings
from app.data.fetcher import ProviderNotConfiguredError
from app.data.index_members import INDEX_NAMES, index_members
from app.data.screener_metrics import (
    EtfListing,
    fetch_etf_listings,
    fetch_etf_metrics,
    fetch_histories,
    fetch_quote_prices,
    fetch_screen_metrics,
)
from app.data.universe import Listing
from app.db.billing import MICROS_PER_DOLLAR, charge_usage
from app.db.nightly import (
    DueRefresh,
    PriceLimits,
    UniverseChoice,
    due_refreshes,
    finish_run,
    mark_etfs_scanned,
    replace_user_universe,
    run_choice,
    start_run,
    user_etfs,
)
from app.db.screener import (
    foreign_companies,
    load_companies,
    load_fundamentals,
    load_listings,
)
from app.db.session import close_database, get_sessionmaker, init_db
from app.db.users import User, get_user
from app.jobs.screener import PRICE_HISTORY_DAYS, completed_sessions
from app.models.fundamentals import Financials
from app.models.screener import ProviderScreenMetrics, ScreenedStock, ScreenMetrics
from app.stats.screening import etf_screen_metrics, market_cap_ranks, user_screen_metrics
from app.stats.sectors import industry_name, sector_for_sic

logger = logging.getLogger(__name__)

ETF_SCAN_DAYS: int = 7
"""Days between the refreshes that check every US ETF; the nights between check only the
ETFs already in the user's universe."""


@dataclass(frozen=True)
class UserRefreshResult:
    """What one user's refresh built."""

    stocks: int
    """Stocks and ETFs in the universe."""
    priced: int
    """Those with price-history columns."""
    prices_as_of: date | None
    scanned_etfs: bool = False
    """Whether it checked every US ETF, rather than those already in the universe."""
    notes: list[str] = field(default_factory=list)
    """What it could not do as asked, and what it did instead."""


@dataclass(frozen=True)
class _Stock:
    """A listed stock with its provider metrics, ready to score."""

    listing: Listing
    shares: float | None
    metrics: ProviderScreenMetrics


@dataclass(frozen=True)
class _Etf:
    """A listed ETF with its provider metrics, ready to score."""

    listing: EtfListing
    metrics: ProviderScreenMetrics

    @property
    def dollar_volume_bound(self) -> float:
        """The most its average dollar volume can be: its volume at its 52-week high."""
        return (self.metrics.average_volume or 0.0) * (self.metrics.week_52_high or 0.0)


def interleave(first: list[str], second: list[str]) -> list[str]:
    """``first`` and ``second`` merged, each in its own order, so that any leading part of
    the result holds them in proportion to their lengths: the stocks and ETFs that share a
    limited number of price histories."""
    total = len(first) + len(second)
    merged: list[str] = []
    taken_first = taken_second = 0
    for position in range(total):
        # Take from whichever list is furthest behind its share of the first ``position``.
        due_first = (position + 1) * len(first) / total if total else 0
        if taken_first < len(first) and (taken_second >= len(second) or taken_first < due_first):
            merged.append(first[taken_first])
            taken_first += 1
        else:
            merged.append(second[taken_second])
            taken_second += 1
    return merged


def _score(
    stocks: list[_Stock],
    histories: dict[str, pd.DataFrame],
    financials: dict[int, Financials],
    companies: dict[int, tuple[int | None, str | None, datetime]],
    factors: pd.DataFrame | None,
    exchange_rates: dict[str, float],
    today: date,
    etfs: list[_Etf] | None = None,
    added: set[str] | None = None,
) -> list[ScreenedStock]:
    """Every stock's and ETF's metrics, keeping those above the price and dollar volume
    floors, and those the user ``added`` by ticker whatever the floors (blocking)."""
    added = added or set()
    settings = get_settings()
    scored: list[tuple[_Stock, ScreenMetrics]] = []
    for stock in stocks:
        metrics = user_screen_metrics(
            stock.metrics,
            histories.get(stock.listing.symbol),
            stock.shares,
            financials.get(stock.listing.cik),
            factors,
            today,
            exchange_rates,
        )
        if metrics is None:
            continue
        if stock.listing.symbol not in added and (
            metrics.price < settings.screener_min_price
            or metrics.avg_dollar_volume < settings.screener_min_dollar_volume
        ):
            continue
        scored.append((stock, metrics))
    ranks = market_cap_ranks(
        {stock.listing.symbol: metrics.market_cap or 0.0 for stock, metrics in scored}
    )
    universe: list[ScreenedStock] = []
    for stock, metrics in scored:
        listing = stock.listing
        company = financials.get(listing.cik)
        sic, sic_title = companies.get(listing.cik, (None, None, None))[:2]
        universe.append(
            ScreenedStock(
                symbol=listing.symbol,
                name=listing.name,
                exchange=listing.exchange,
                sector=sector_for_sic(sic),
                industry=industry_name(sic_title),
                cik=listing.cik,
                market_cap_source="provider",
                market_cap_rank=ranks[listing.symbol],
                fundamentals_period_end=None if company is None else company.latest_period_end,
                **metrics.model_dump(),
            )
        )
    for etf in etfs or []:
        found = etf_screen_metrics(etf.metrics, histories.get(etf.listing.symbol), factors)
        if found is None or (
            etf.listing.symbol not in added
            and (
                found.price < settings.screener_min_price
                or found.avg_dollar_volume < settings.screener_min_dollar_volume
            )
        ):
            continue
        universe.append(
            ScreenedStock(
                symbol=etf.listing.symbol,
                name=etf.listing.name,
                exchange=etf.listing.exchange,
                asset_type="etf",
                market_cap_source="provider",
                **found.model_dump(),
            )
        )
    return universe


def scans_every_etf(scanned_at: datetime | None, now: datetime) -> bool:
    """Whether tonight's refresh checks every US ETF: when it never has, or last did
    :data:`ETF_SCAN_DAYS` or more days ago (less an hour, so a refresh that ran a little
    later last week does not wait another week)."""
    return scanned_at is None or now - scanned_at >= timedelta(days=ETF_SCAN_DAYS, hours=-1)


async def _etfs(skip: set[str], kept: list[EtfListing] | None = None) -> list[_Etf]:
    """The ETFs on US exchanges that could clear the dollar volume floor, most traded
    first, with Finnhub's metrics, on the user's key: those in ``kept``, or, when it is
    ``None``, every one on Finnhub's symbol list. Empty, with a warning, when Finnhub's
    symbol list fails, so the stocks are still refreshed.

    Raises:
        ProviderNotConfiguredError: If the user's Finnhub key is missing or rejected.
    """
    settings = get_settings()
    try:
        found = kept if kept is not None else await fetch_etf_listings()
        listings = [etf for etf in found if etf.symbol not in skip]
    except ProviderNotConfiguredError:
        raise
    except DataFetchError as exc:
        logger.warning("ETFs are left out: Finnhub's symbol list failed: %s", exc)
        return []
    by_symbol = {etf.symbol: etf for etf in listings}
    provided = await fetch_etf_metrics(by_symbol)
    etfs = [_Etf(by_symbol[symbol], metrics) for symbol, metrics in provided.items()]
    etfs = [
        etf
        for etf in etfs
        if etf.metrics.week_52_high is None
        or etf.dollar_volume_bound >= settings.screener_min_dollar_volume
    ]
    etfs.sort(key=lambda etf: -etf.dollar_volume_bound)
    logger.info(
        "Finnhub had metrics for %d of %d ETFs; %d could clear the dollar volume floor.",
        len(provided),
        len(listings),
        len(etfs),
    )
    return etfs


def main_classes(stocks: list[_Stock], classes: dict[int, int], only: set[str]) -> list[_Stock]:
    """``stocks``, keeping of each company whose listings are in ``only`` (those chosen
    through an index alone) the ``classes[cik]`` most traded: its share classes in the
    index (both of Alphabet's), not its notes and preferreds."""
    by_company: dict[int, list[_Stock]] = {}
    for stock in stocks:
        if stock.listing.symbol in only:
            by_company.setdefault(stock.listing.cik, []).append(stock)
    kept = {stock.listing.symbol for stock in stocks if stock.listing.symbol not in only}
    for cik, listed in by_company.items():
        listed.sort(key=lambda stock: -(stock.metrics.average_volume or 0.0))
        kept |= {stock.listing.symbol for stock in listed[: classes.get(cik, 1)]}
    return [stock for stock in stocks if stock.listing.symbol in kept]


@dataclass(frozen=True)
class _Chosen:
    """The listings a refresh's choice picks, each once, and what it found doing so."""

    listings: list[tuple[Listing, float | None]]
    """The stocks to ask Finnhub about, with their SEC shares outstanding."""
    index_only: set[str] = field(default_factory=set)
    """Those picked through an index alone, so trimmed to its share classes."""
    classes: dict[int, int] = field(default_factory=dict)
    """CIK -> share classes of the company an index holds."""
    added: set[str] = field(default_factory=set)
    """Stocks the user added by ticker, kept whatever the floors."""
    added_etfs: list[str] = field(default_factory=list)
    """Tickers the user added that are not stocks on the SEC's list: ETFs, if any."""
    notes: list[str] = field(default_factory=list)


async def choose_listings(
    choice: UniverseChoice, listings: list[tuple[Listing, float | None]]
) -> _Chosen:
    """The listings (with their SEC shares outstanding) the sets and tickers in ``choice``
    pick, combined, so a stock in several sets is asked about once.

    Raises:
        RuntimeError: If an index's companies could not be read from its fund's filing.
    """
    by_symbol = {listing.symbol: (listing, count) for listing, count in listings}
    chosen = set(choice.universes)
    broad: set[str] = set()
    if "us_stocks" in chosen:
        broad = set(by_symbol)
    else:
        if "nasdaq" in chosen:
            broad |= {
                symbol for symbol, (listing, _) in by_symbol.items() if listing.exchange == "Nasdaq"
            }
        if "international" in chosen:
            async with get_sessionmaker()() as session:
                foreign = await foreign_companies(session)
            broad |= {
                symbol for symbol, (listing, _) in by_symbol.items() if listing.cik in foreign
            }
    in_indexes: set[str] = set()
    classes: dict[int, int] = {}
    # Every index company is already among the US stocks.
    for index in ("sp500", "nasdaq100") if "us_stocks" not in chosen else ():
        if index not in chosen:
            continue
        try:
            members = await index_members(index, (listing for listing, _ in listings))
        except DataFetchError as exc:
            raise RuntimeError(
                f"The {INDEX_NAMES[index]}'s companies could not be read from the SEC ({exc}); "
                "your universe was kept."
            ) from exc
        in_indexes |= {listing.symbol for listing in members.listings}
        for cik, count in members.classes.items():
            classes[cik] = max(classes.get(cik, 0), count)

    added: set[str] = set()
    added_etfs: list[str] = []
    notes: list[str] = []
    for symbol in choice.custom_symbols:
        # BRK.B as some sites write it is BRK-B on the SEC's list.
        listed = next((s for s in (symbol, symbol.replace(".", "-")) if s in by_symbol), None)
        if listed is not None:
            added.add(listed)
        elif symbol_kind(symbol) == "international":
            try:
                stand_ins = [s for s in await us_listings(symbol) if s in by_symbol]
            except DataFetchError:
                stand_ins = []
            if stand_ins:
                added.update(stand_ins)
                notes.append(
                    f"{symbol}: free plans have no figures for listings abroad, so its "
                    f"company's US listing, {listed_text(stand_ins)}, was refreshed instead."
                )
            else:
                notes.append(
                    f"{symbol}: free plans have no figures for listings abroad, and no US "
                    "listing of its company was found, so it was left out. A Finnhub plan "
                    "covering its exchange would include it."
                )
        else:
            added_etfs.append(symbol)
    picked = broad | in_indexes | added
    return _Chosen(
        listings=[(listing, count) for listing, count in listings if listing.symbol in picked],
        index_only=in_indexes - broad - added,
        classes=classes,
        added=added,
        added_etfs=added_etfs,
        notes=notes,
    )


async def _added_etfs(symbols: list[str], notes: list[str]) -> list[_Etf]:
    """The ETFs among ``symbols``, tickers the user added, with Finnhub's metrics, kept
    whatever the floors; a note in ``notes`` for each that is not one.

    Raises:
        ProviderNotConfiguredError: If the user's Finnhub key is missing or rejected.
    """
    try:
        listed = {etf.symbol: etf for etf in await fetch_etf_listings()}
    except ProviderNotConfiguredError:
        raise
    except DataFetchError as exc:
        notes.append(
            f"{listed_text(symbols)} could not be looked up, since Finnhub's list of ETFs "
            f"failed ({exc})."
        )
        return []
    found = {symbol: listed[symbol] for symbol in symbols if symbol in listed}
    for symbol in symbols:
        if symbol not in found:
            notes.append(
                f"{symbol} is not a stock or ETF listed in the US that PyQxel could find, so "
                "it was left out."
            )
    provided = await fetch_etf_metrics(found) if found else {}
    for symbol in found:
        if symbol not in provided:
            notes.append(f"Finnhub had no figures for {symbol}, so it was left out.")
    return [_Etf(found[symbol], metrics) for symbol, metrics in provided.items()]


async def build_user_universe(
    user_id: str,
    now: datetime,
    price_history_limits: PriceLimits | None = None,
    etfs_scanned_at: datetime | None = None,
    choice: UniverseChoice | None = None,
) -> UserRefreshResult:
    """Build and store ``user_id``'s own universe of the sets and tickers in ``choice``
    (every US stock and ETF unless given) on the current provider keys.

    Raises:
        RuntimeError: If the SEC first stage has not stored any listings yet, or an index's
            companies could not be read.
        ProviderNotConfiguredError: If the user's Finnhub key is missing or rejected.
    """
    choice = choice or UniverseChoice()
    settings = get_settings()
    today = now.date()
    async with get_sessionmaker()() as session:
        listings = await load_listings(session)
    if not listings:
        raise RuntimeError("The shared SEC stage has not run yet; try again tomorrow.")
    every_symbol = {listing.symbol for listing, _ in listings}
    chosen = await choose_listings(choice, listings)
    notes = list(chosen.notes)
    shares = {listing.symbol: count for listing, count in chosen.listings}
    by_symbol = {listing.symbol: listing for listing, _ in chosen.listings}

    provided = await fetch_screen_metrics(by_symbol) if by_symbol else {}
    stocks = [
        _Stock(by_symbol[symbol], shares[symbol], metrics)
        for symbol, metrics in provided.items()
        if (metrics.market_cap or 0.0) >= settings.screener_min_market_cap or symbol in chosen.added
    ]
    for symbol in sorted(chosen.added - set(provided)):
        notes.append(f"Finnhub had no figures for {symbol}, so it was left out.")
    stocks = main_classes(stocks, chosen.classes, chosen.index_only)
    stocks.sort(key=lambda stock: -(stock.metrics.market_cap or 0.0))
    logger.info(
        "User %s: Finnhub had metrics for %d of %d stocks; %d pass the market cap floor.",
        user_id,
        len(provided),
        len(chosen.listings),
        len(stocks),
    )

    known_etfs: list[EtfListing] | None = None
    etfs: list[_Etf] = []
    every_etf = "us_etfs" in choice.universes
    if every_etf:
        if not scans_every_etf(etfs_scanned_at, now):
            async with get_sessionmaker()() as session:
                known_etfs = [EtfListing(*row) for row in await user_etfs(session, user_id)]
            if not known_etfs:
                # None were kept last time, as when that refresh failed: check them all.
                known_etfs = None
        # The ETFs the user added are asked about once, below.
        etfs = await _etfs(every_symbol | set(chosen.added_etfs), known_etfs)
    if chosen.added_etfs:
        etfs += await _added_etfs(chosen.added_etfs, notes)
    added = chosen.added | set(chosen.added_etfs)

    # The user chose how many stocks and ETFs each price-history key fetches histories
    # for: one request each, which free plans allow only so many of a day. Those they
    # added by ticker come first.
    ranked = interleave(
        [stock.listing.symbol for stock in stocks], [etf.listing.symbol for etf in etfs]
    )
    priced_first = [symbol for symbol in ranked if symbol in added] + [
        symbol for symbol in ranked if symbol not in added
    ]
    histories = await fetch_histories(
        priced_first, today - timedelta(days=PRICE_HISTORY_DAYS), price_history_limits
    )
    histories = completed_sessions(histories, now)

    # A stock with neither price history nor SEC shares outstanding takes a quote for its
    # price, so it is not left out.
    unpriced = [
        stock.listing.symbol
        for stock in stocks
        if stock.listing.symbol not in histories and not stock.shares
    ]
    unpriced += [etf.listing.symbol for etf in etfs if etf.listing.symbol not in histories]
    prices = await fetch_quote_prices(unpriced) if unpriced else {}
    etfs = [
        (
            replace(etf, metrics=etf.metrics.model_copy(update={"price": price}))
            if (price := prices.get(etf.listing.symbol)) is not None
            else etf
        )
        for etf in etfs
    ]
    stocks = [
        (
            replace(stock, metrics=stock.metrics.model_copy(update={"price": price}))
            if (price := prices.get(stock.listing.symbol)) is not None
            else stock
        )
        for stock in stocks
    ]

    ciks = {stock.listing.cik for stock in stocks}
    async with get_sessionmaker()() as session:
        financials = await load_fundamentals(session, ciks)
        companies = await load_companies(session, ciks)
    factors: pd.DataFrame | None = None
    if histories:
        try:
            factors = await fetch_factors("carhart4")
        except DataFetchError as exc:
            logger.warning("Factor betas are left out: %s", exc)
    exchange_rates = await fetch_exchange_rates(
        {company.currency for company in financials.values()} - {"USD"}
    )
    screened = await asyncio.to_thread(
        _score,
        stocks,
        histories,
        financials,
        companies,
        factors,
        exchange_rates,
        today,
        etfs,
        added,
    )
    if not screened:
        raise RuntimeError("No stock passed the baseline filters; your universe was kept.")
    async with get_sessionmaker()() as session:
        count = await replace_user_universe(session, user_id, screened)
    kept = {stock.symbol for stock in screened}
    priced = [symbol for symbol in histories if symbol in kept]
    last_closes = [pd.Timestamp(histories[symbol].index[-1]).date() for symbol in priced]
    return UserRefreshResult(
        stocks=count,
        priced=len(priced),
        prices_as_of=max(last_closes, default=None),
        scanned_etfs=every_etf and known_etfs is None,
        notes=notes,
    )


def run_cost_micros(seconds: float, sharing: int) -> int:
    """What ``seconds`` of a refresh cost on Cloud Run, in millionths of a dollar, when
    ``sharing`` refreshes ran at once on the job's CPU and memory, each paying its share."""
    settings = get_settings()
    per_second = (
        settings.nightly_job_cpus * settings.job_cpu_second_usd
        + settings.nightly_job_memory_gib * settings.job_gib_second_usd
    )
    return round(seconds * per_second / max(sharing, 1) * MICROS_PER_DOLLAR)


async def refresh_user(due: DueRefresh, now: datetime | None = None, sharing: int = 1) -> None:
    """Run one user's refresh on their own keys, recording how it went and what it cost.

    A failure is recorded for the user to see in Settings, never raised, so one user's
    rejected key does not stop anyone else's refresh. Its cost is the refresh's share of
    the job's time, with ``sharing`` refreshes running at once; it is charged to the user
    (:func:`app.db.billing.charge_usage`) whether the refresh succeeded or failed, since
    the job ran either way, except to the owner's account, which pays the cloud bill.
    """
    now = now or datetime.now(timezone.utc)
    started = time.monotonic()
    async with get_sessionmaker()() as session:
        user = await get_user(session, due.user_id)
        run_id = await start_run(
            session, due.user_id, now, run_choice(due.choice, due.price_history_limits)
        )
    # The user's stored keys, with the same data access their own requests have.
    # The user's stored keys, with the same data access their own requests have. Its
    # thousands of price histories are not kept in the user's stored prices.
    use_provider_keys(
        replace(
            due.keys,
            yfinance=user is not None and uses_yfinance(user),
            unlicensed=user is not None and sees_unlicensed_data(user),
            restricted_sources=user is not None and uses_restricted_sources(user),
            user_id=due.user_id,
            store_history=False,
        )
    )
    with usage.track() as sent:
        try:
            result = await build_user_universe(
                due.user_id, now, due.price_history_limits, due.etfs_scanned_at, due.choice
            )
        except Exception as exc:
            logger.warning("User %s's nightly refresh failed: %s", due.user_id, exc)
            cost = run_cost_micros(time.monotonic() - started, sharing)
            async with get_sessionmaker()() as session:
                await finish_run(
                    session,
                    run_id,
                    error=str(exc) or type(exc).__name__,
                    usage=dict(sent),
                    cost_micros=cost,
                )
                await _charge(session, user, run_id, cost)
            await usage.flush()
            return
    cost = run_cost_micros(time.monotonic() - started, sharing)
    async with get_sessionmaker()() as session:
        if result.scanned_etfs:
            await mark_etfs_scanned(session, due.user_id, now)
        await finish_run(
            session,
            run_id,
            stocks=result.stocks,
            priced=result.priced,
            prices_as_of=result.prices_as_of,
            usage=dict(sent),
            notes=result.notes,
            cost_micros=cost,
        )
        await _charge(session, user, run_id, cost)
    await usage.flush()
    logger.info(
        "User %s: %d stocks, %d with price history.", due.user_id, result.stocks, result.priced
    )


async def _charge(session: AsyncSession, user: User | None, run_id: str, cost: int) -> None:
    """Charge ``user`` ``cost`` for a refresh, unless they are the owner."""
    if user is None or user.is_admin:
        return
    await charge_usage(session, user.id, cost, run_id)


async def refresh_due_users(now: datetime | None = None) -> int:
    """Refresh every user whose refresh is due, ``NIGHTLY_REFRESH_CONCURRENCY`` at once;
    return how many ran."""
    now = now or datetime.now(timezone.utc)
    await init_db()
    async with get_sessionmaker()() as session:
        due = await due_refreshes(session, now)
    concurrency = get_settings().nightly_refresh_concurrency
    slots = asyncio.Semaphore(concurrency)
    # How many refreshes share the job's CPU and memory at a time, and so its cost.
    sharing = min(concurrency, len(due))

    async def run(refresh: DueRefresh) -> None:
        async with slots:
            await refresh_user(refresh, sharing=sharing)

    # Each refresh is its own task, so the keys it sets are visible to it alone.
    await asyncio.gather(*(asyncio.create_task(run(refresh)) for refresh in due))
    return len(due)


async def _main() -> None:
    """Run the due refreshes and close the database."""
    try:
        count = await refresh_due_users()
    finally:
        await close_database()
    print(f"Refreshed {count} users' own screeners.")


def main() -> None:
    """Command-line entry point."""
    install_log_redaction()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(_main())


if __name__ == "__main__":
    main()
