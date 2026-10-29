"""Refresh the screened universe: which stocks it holds and every metric they are screened on.

Run it daily, after the US close, with::

    python -m app.jobs.screener

Each run:

1. Reads every common stock listed on NYSE, Nasdaq and Cboe from the SEC's ticker list,
   leaving out preferreds, notes, warrants, units and rights by ticker and by name.
2. Reads market caps, sectors and industries from Financial Modeling Prep's company
   screener, and downloads about 13 months of daily prices from FMP for every stock not
   already known to be below the market cap floor.
3. Keeps those passing the baseline filter: price, average daily dollar volume and market
   cap above the ``SCREENER_MIN_*`` settings. Market caps FMP's company screener lacks
   come from SEC share counts, the last refresh, or FMP company profiles.
4. Rebuilds their financials from the SEC's bulk company facts archive when the stored
   ones are older than ``SCREENER_FUNDAMENTALS_REFRESH_DAYS`` (weekly by default),
   fetches any company that is new to the universe on its own, and refetches companies
   whose financials look a period behind, reading filings company facts left out.
5. Computes valuation, growth, momentum, volatility and Carhart factor betas, and
   replaces the stored universe with the result.

Every account sees the universe, so market data is fetched on the screener's own key,
``SCREENER_FMP_API_KEY``, which must be on an FMP plan licensed for showing its data to
your users, never on an account's key. Without that key the run fails, unless the
yfinance fallback is on for everyone (``YFINANCE_FALLBACK``, or the admin's switch), when
yfinance and Nasdaq's public screener feed stand in.

A full run takes one FMP request per listed stock, about 20 minutes at 300 requests a
minute (``SCREENER_FMP_REQUESTS_PER_MINUTE``), plus a 1.3 GB download on the days
fundamentals are rebuilt. Pass ``--fundamentals`` to rebuild them regardless of age.
"""

import argparse
import asyncio
import logging
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from app.core.config import PROJECT_ROOT, get_settings
from app.core.credentials import ProviderKeys, use_provider_keys
from app.core.redact import install_log_redaction
from app.data.exchange_rates import fetch_exchange_rates
from app.data.factors import fetch_factors
from app.data.fetcher import DataFetchError, ProviderNotConfiguredError, ProviderPlanError
from app.data.fundamentals import (
    download_bulk_company_facts,
    fetch_financials,
    read_bulk_financials,
)
from app.data.sec_edgar import FilingFetchError, edgar_client
from app.data.universe import (
    FmpSession,
    Listing,
    Profile,
    download_prices,
    fetch_listings,
    fetch_market_caps,
    fetch_profiles,
    fetch_share_counts,
    is_common_name,
)
from app.db.app_settings import yfinance_for_everyone
from app.db.screener import (
    finish_run,
    fundamentals_refreshed_at,
    load_fundamentals,
    previous_share_counts,
    replace_universe,
    save_fundamentals,
    start_run,
)
from app.db.session import close_database, get_sessionmaker, init_db
from app.models.fundamentals import AccountingStandard, Financials
from app.models.screener import MarketCapSource, ScreenedStock
from app.stats.screening import liquidity, market_cap_ranks, screen_metrics

logger = logging.getLogger(__name__)

PRICE_HISTORY_DAYS: int = 400
"""Calendar days of prices downloaded: a year of returns for betas plus the 12-1 month
momentum window."""

ARCHIVE_PATH = PROJECT_ROOT / "data_cache" / "companyfacts.zip"
"""Where the bulk company facts archive is downloaded; it is deleted once read."""

US_EASTERN = ZoneInfo("America/New_York")
SESSION_SETTLED: time = time(16, 30)
"""Eastern time after which the day's closing prices and volumes are final."""

MAX_SINGLE_FETCHES: int = 200
"""Companies new to the universe whose financials are fetched one by one between bulk
rebuilds; more wait for the next rebuild."""

MAX_CATCH_UPS: int = 500
"""Companies whose financials look behind that are refetched in one run; each takes two
or three EDGAR requests, so 500 take about three minutes."""

BEHIND_AFTER_DAYS: dict[AccountingStandard, int] = {"US GAAP": 135, "IFRS": 485}
"""Age of a company's latest period after which a newer filing is probably out: a 10-Q is
due within 45 days of the quarter's end, and an IFRS filer's 20-F within four months of
the fiscal year's."""


@dataclass(frozen=True)
class Candidate:
    """A listed stock that passed the price and liquidity filters."""

    listing: Listing
    prices: pd.DataFrame


@dataclass(frozen=True)
class RefreshResult:
    """What a refresh produced."""

    stocks: int
    prices_as_of: date | None
    fundamentals_refreshed: bool
    factor_data_end: date | None


def _liquid(listings: list[Listing], prices: dict[str, pd.DataFrame]) -> list[Candidate]:
    """Listings whose price and average dollar volume clear the baseline thresholds."""
    settings = get_settings()
    candidates: list[Candidate] = []
    for listing in listings:
        frame = prices.get(listing.symbol)
        trading = None if frame is None else liquidity(frame)
        if trading is None or frame is None:
            continue
        if (
            trading.price >= settings.screener_min_price
            and trading.avg_dollar_volume >= settings.screener_min_dollar_volume
        ):
            candidates.append(Candidate(listing, frame))
    return candidates


async def _market_caps(
    candidates: list[Candidate],
    all_listings: list[Listing],
    profiles: dict[str, Profile],
    market: FmpSession | None,
) -> dict[str, tuple[float, MarketCapSource]]:
    """Market caps from FMP's company screener, then SEC shares, then the last refresh,
    then company profiles.

    The company screener covers nearly every stock, including those with several share
    classes. For the rest, price times SEC shares is used when the company has one listed
    share class; then the share count implied by the stock's market cap in the previous
    refresh; and last, profiles one stock at a time, most traded first so that yfinance,
    which throttles long runs of lookups in development, reaches them.
    """

    def last_close(candidate: Candidate) -> float:
        return float(candidate.prices["Close"].dropna().iloc[-1])

    caps: dict[str, tuple[float, MarketCapSource]] = {}
    for candidate in candidates:
        profile = profiles.get(candidate.listing.symbol)
        if profile is not None and profile.market_cap is not None:
            caps[candidate.listing.symbol] = (profile.market_cap, "provider")
    from_screener = len(caps)

    classes = Counter(listing.cik for listing in all_listings)
    async with edgar_client() as client:
        shares = await fetch_share_counts(client)
    async with get_sessionmaker()() as session:
        previous = await previous_share_counts(session)
    rest: list[Candidate] = []
    from_sec = reused = 0
    for candidate in candidates:
        symbol, cik = candidate.listing.symbol, candidate.listing.cik
        if symbol in caps:
            continue
        if cik in shares and classes[cik] == 1:
            caps[symbol] = (last_close(candidate) * shares[cik], "sec_shares")
            from_sec += 1
        elif symbol in previous:
            caps[symbol] = (previous[symbol] * last_close(candidate), "provider")
            reused += 1
        else:
            rest.append(candidate)

    rest.sort(key=lambda c: -float((c.prices["Close"] * c.prices["Volume"]).tail(63).mean()))
    provided = await fetch_market_caps((candidate.listing.symbol for candidate in rest), market)
    caps |= {symbol: (value, "provider") for symbol, value in provided.items()}
    logger.info(
        "Market caps: %d from the company screener, %d from SEC share counts, %d from the "
        "last refresh, %d from company profiles, %d unknown.",
        from_screener,
        from_sec,
        reused,
        len(provided),
        len(candidates) - len(caps),
    )
    return caps


def _completed_sessions(prices: dict[str, pd.DataFrame], now: datetime) -> dict[str, pd.DataFrame]:
    """Drop today's bar while the US market is still open: its close and volume are partial."""
    eastern = now.astimezone(US_EASTERN)
    if eastern.time() >= SESSION_SETTLED:
        return prices
    today = eastern.date()
    return {
        symbol: frame[[pd.Timestamp(moment).date() != today for moment in frame.index]]
        for symbol, frame in prices.items()
    }


async def _refresh_fundamentals(ciks: set[int], force: bool) -> bool:
    """Rebuild the universe's financials from the bulk archive if due; return whether it ran."""
    async with get_sessionmaker()() as session:
        last = await fundamentals_refreshed_at(session)
    age = None if last is None else datetime.now(timezone.utc) - last
    due = timedelta(days=get_settings().screener_fundamentals_refresh_days)
    if not force and age is not None and age < due:
        return False
    logger.info("Downloading the SEC bulk company facts archive.")
    async with edgar_client() as client:
        await download_bulk_company_facts(client, ARCHIVE_PATH)
    try:
        financials = await asyncio.to_thread(
            lambda: list(read_bulk_financials(ARCHIVE_PATH, sorted(ciks)))
        )
    finally:
        ARCHIVE_PATH.unlink(missing_ok=True)
    async with get_sessionmaker()() as session:
        saved = await save_fundamentals(session, financials, datetime.now(timezone.utc))
    logger.info("Stored financials for %d of %d companies.", saved, len(ciks))
    return True


async def _fill_missing_fundamentals(ciks: set[int], symbols: dict[int, str]) -> None:
    """Fetch the financials of companies new to the universe since the last bulk rebuild."""
    async with get_sessionmaker()() as session:
        stored = await load_fundamentals(session, ciks)
    missing = sorted(ciks - set(stored))[:MAX_SINGLE_FETCHES]
    if not missing:
        return
    fetched: list[Financials] = []
    async with edgar_client() as client:
        for cik in missing:
            try:
                fetched.append(await fetch_financials(symbols[cik], client=client))
            except FilingFetchError as exc:
                logger.debug("No financials for %s: %s", symbols[cik], exc)
    async with get_sessionmaker()() as session:
        await save_fundamentals(session, fetched, datetime.now(timezone.utc))
    logger.info("Fetched financials for %d of %d new companies.", len(fetched), len(missing))


async def _catch_up_fundamentals(ciks: set[int], symbols: dict[int, str], today: date) -> None:
    """Refetch companies whose stored financials look a period behind.

    Company facts, in bulk and per company, occasionally leaves out a filing; fetching a
    company on its own reads such filings from their XBRL instances. Companies that are
    simply late filers are checked again on each run, up to :data:`MAX_CATCH_UPS`.
    """
    async with get_sessionmaker()() as session:
        stored = await load_fundamentals(session, ciks)
    behind = [
        cik
        for cik, company in stored.items()
        if company.latest_period_end is not None
        and (today - company.latest_period_end).days
        > BEHIND_AFTER_DAYS[company.accounting_standard or "US GAAP"]
    ]
    behind.sort(key=lambda cik: stored[cik].latest_period_end or date.min, reverse=True)
    caught_up: list[Financials] = []
    async with edgar_client() as client:
        for cik in behind[:MAX_CATCH_UPS]:
            try:
                fetched = await fetch_financials(symbols[cik], client=client)
            except FilingFetchError as exc:
                logger.debug("Could not refetch %s: %s", symbols[cik], exc)
                continue
            previous = stored[cik].latest_period_end
            if fetched.latest_period_end and previous and fetched.latest_period_end > previous:
                caught_up.append(fetched)
    async with get_sessionmaker()() as session:
        await save_fundamentals(session, caught_up, datetime.now(timezone.utc))
    logger.info(
        "Caught up %d of %d companies whose financials looked behind.",
        len(caught_up),
        len(behind),
    )


def _score(
    universe: list[Candidate],
    caps: dict[str, tuple[float, MarketCapSource]],
    profiles: dict[str, Profile],
    financials: dict[int, Financials],
    factors: pd.DataFrame | None,
    exchange_rates: dict[str, float],
    today: date,
) -> list[ScreenedStock]:
    """Compute every stock's metrics (blocking)."""
    ranks = market_cap_ranks({c.listing.symbol: caps[c.listing.symbol][0] for c in universe})
    stocks: list[ScreenedStock] = []
    for candidate in universe:
        listing = candidate.listing
        market_cap, source = caps[listing.symbol]
        company = financials.get(listing.cik)
        metrics = screen_metrics(
            candidate.prices, market_cap, company, factors, today, exchange_rates
        )
        if metrics is None:
            continue
        profile = profiles.get(listing.symbol)
        stocks.append(
            ScreenedStock(
                symbol=listing.symbol,
                name=listing.name,
                exchange=listing.exchange,
                sector=None if profile is None else profile.sector,
                industry=None if profile is None else profile.industry,
                cik=listing.cik,
                market_cap_source=source,
                market_cap_rank=ranks[listing.symbol],
                fundamentals_period_end=None if company is None else company.latest_period_end,
                **metrics.model_dump(),
            )
        )
    return stocks


async def refresh_universe(force_fundamentals: bool = False) -> RefreshResult:
    """Rebuild the screened universe and its metrics; see the module docstring for the steps.

    The run is recorded, with its error if it fails, so the screener can report it.

    Raises:
        FilingFetchError: If SEC EDGAR cannot be reached or ``SEC_USER_AGENT`` is unset.
    """
    await init_db()
    async with get_sessionmaker()() as session:
        run_id = await start_run(session)
    try:
        result = await _refresh(force_fundamentals)
    except Exception as exc:
        async with get_sessionmaker()() as session:
            await finish_run(session, run_id, error=f"{type(exc).__name__}: {exc}")
        raise
    async with get_sessionmaker()() as session:
        await finish_run(
            session,
            run_id,
            stocks=result.stocks,
            prices_as_of=result.prices_as_of,
            fundamentals_refreshed=result.fundamentals_refreshed,
            factor_data_end=result.factor_data_end,
        )
    return result


@asynccontextmanager
async def _market_data() -> AsyncIterator[FmpSession | None]:
    """The screener's FMP session, or ``None`` to use yfinance in development.

    The screener's key also becomes the current provider key, so exchange rates are
    fetched on it.

    Raises:
        ProviderNotConfiguredError: If ``SCREENER_FMP_API_KEY`` is unset and the yfinance
            fallback is off, both in ``YFINANCE_FALLBACK`` and in the admin's settings.
    """
    settings = get_settings()
    key = settings.screener_fmp_api_key
    if key is None:
        async with get_sessionmaker()() as session:
            everyone = await yfinance_for_everyone(session)
        if not settings.yfinance_fallback and not everyone:
            raise ProviderNotConfiguredError(
                "The screener needs SCREENER_FMP_API_KEY: a Financial Modeling Prep key on a "
                "plan licensed for showing its data to your users."
            )
        logger.warning(
            "SCREENER_FMP_API_KEY is unset, so unlicensed yfinance and Nasdaq data stand in "
            "(development and personal use only)."
        )
        yield None
        return
    use_provider_keys(ProviderKeys(fmp=key))
    async with FmpSession(
        key.get_secret_value(), settings.screener_fmp_requests_per_minute
    ) as session:
        yield session


async def _refresh(force_fundamentals: bool) -> RefreshResult:
    """The steps of :func:`refresh_universe`."""
    async with _market_data() as market:
        return await _refresh_with(market, force_fundamentals)


async def _refresh_with(market: FmpSession | None, force_fundamentals: bool) -> RefreshResult:
    """:func:`_refresh` with its market data source open."""
    settings = get_settings()
    today = date.today()
    async with edgar_client() as client:
        listings = await fetch_listings(client)
    try:
        profiles = await fetch_profiles(market)
    except ProviderNotConfiguredError, ProviderPlanError:
        raise
    except DataFetchError as exc:
        logger.warning("The stock screener feed is unavailable, so sectors are left out: %s", exc)
        profiles = {}
    listings = [
        listing
        for listing in listings
        if listing.symbol not in profiles or is_common_name(profiles[listing.symbol].name)
    ]
    logger.info("%d common stocks are listed on US exchanges.", len(listings))

    # Stocks the company screener puts below the market cap floor would be dropped anyway,
    # so their prices are not downloaded.
    def known_too_small(listing: Listing) -> bool:
        profile = profiles.get(listing.symbol)
        cap = None if profile is None else profile.market_cap
        return cap is not None and cap < settings.screener_min_market_cap

    prices = await download_prices(
        (listing.symbol for listing in listings if not known_too_small(listing)),
        today - timedelta(days=PRICE_HISTORY_DAYS),
        market,
    )
    prices = _completed_sessions(prices, datetime.now(timezone.utc))
    candidates = _liquid(listings, prices)
    logger.info("%d pass the price and dollar volume floors.", len(candidates))

    caps = await _market_caps(candidates, listings, profiles, market)
    universe = [
        candidate
        for candidate in candidates
        if candidate.listing.symbol in caps
        and caps[candidate.listing.symbol][0] >= settings.screener_min_market_cap
    ]
    logger.info("%d pass the market cap floor and form the universe.", len(universe))
    if not universe:
        raise RuntimeError("No stock passed the baseline filters; the universe was kept.")

    ciks = {candidate.listing.cik for candidate in universe}
    symbols = {candidate.listing.cik: candidate.listing.symbol for candidate in universe}
    refreshed = await _refresh_fundamentals(ciks, force_fundamentals)
    if not refreshed:
        await _fill_missing_fundamentals(ciks, symbols)
    await _catch_up_fundamentals(ciks, symbols, today)
    async with get_sessionmaker()() as session:
        financials = await load_fundamentals(session, ciks)

    factors: pd.DataFrame | None
    try:
        factors = await fetch_factors("carhart4")
    except DataFetchError as exc:
        logger.warning("Factor betas are left out: %s", exc)
        factors = None

    # Foreign issuers' statements are in their own currencies; valuation needs dollars.
    exchange_rates = await fetch_exchange_rates(
        {company.currency for company in financials.values()} - {"USD"}
    )
    stocks = await asyncio.to_thread(
        _score, universe, caps, profiles, financials, factors, exchange_rates, today
    )
    async with get_sessionmaker()() as session:
        count = await replace_universe(session, stocks)
    logger.info("Stored %d stocks in the screened universe.", count)

    last_closes = [pd.Timestamp(c.prices.index[-1]).date() for c in universe]
    return RefreshResult(
        stocks=count,
        prices_as_of=max(last_closes, default=None),
        fundamentals_refreshed=refreshed,
        factor_data_end=None if factors is None else pd.Timestamp(factors.index[-1]).date(),
    )


async def _main(force_fundamentals: bool) -> None:
    """Run one refresh and close the database."""
    try:
        result = await refresh_universe(force_fundamentals)
    finally:
        await close_database()
    fundamentals = "rebuilt" if result.fundamentals_refreshed else "reused"
    print(
        f"Screened universe refreshed: {result.stocks} stocks, prices as of "
        f"{result.prices_as_of}, fundamentals {fundamentals}."
    )


def main() -> None:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description="Refresh the screened stock universe.")
    parser.add_argument(
        "--fundamentals",
        action="store_true",
        help="Rebuild financials from the SEC bulk archive even if they are recent.",
    )
    arguments = parser.parse_args()
    install_log_redaction()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(_main(arguments.fundamentals))


if __name__ == "__main__":
    main()
