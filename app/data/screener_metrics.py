"""Market data for a user's own screener, fetched on that user's keys by their nightly
refresh.

Finnhub's free basic financials give every listed stock's market cap, valuation, margins,
growth, and 26-week return, one request per stock. Price-history columns (momentum,
volatility, drawdown, factor betas) need daily prices, fetched through
:func:`app.data.fetcher.fetch_price_history` on whichever price-history key the user has,
cheapest first. Free plans allow only some stocks a night, so the largest are fetched
first and the pass stops once the keys' limits are reached.

Nothing here falls back to yfinance: what the user sees in their universe comes from
their own licensed keys and the SEC's public data.
"""

import asyncio
import logging
import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, TypeVar

import httpx2
import pandas as pd

from app.data.fetcher import (
    HTTP_TIMEOUT_SECONDS,
    FINNHUB_FREE_PER_MINUTE,
    DataFetchError,
    FinnhubRateLimitError,
    ProviderLimitError,
    ProviderNotConfiguredError,
    SUFFIX_CURRENCIES,
    fetch_price_history,
    finnhub_key,
    finnhub_symbol,
    finnhub_get,
    finnhub_requests_per_minute,
)
from app.core.credentials import current_provider_keys, use_provider_keys
from app.data.exchange_rates import fetch_exchange_rates
from app.data.universe import RequestPacer
from app.models.screener import ProviderScreenMetrics

logger = logging.getLogger(__name__)

FINNHUB_NIGHTLY_SHARE: float = 0.85
"""The share of the key's per-minute limit the nightly refresh sends: 51 a minute on the
free plan's 60, 765 on a paid plan's 900. A paid plan's refresh is that much faster, and so
cheaper, since a refresh is paid for by its time on the server. Until Finnhub's first
answer reports the key's limit, the refresh sends at the free plan's pace."""

FINNHUB_THROTTLED_SHARE: float = 0.7
"""What the pace is cut to, as a share, each time Finnhub answers that the limit was
reached anyway, as a plan whose fundamentals allow less than its market data might."""

FINNHUB_WORKERS: int = 16
"""Finnhub requests in flight at once, enough for a paid plan's pace; the pacer keeps them
within the minute's limit, so on the free plan most of them wait."""

FINNHUB_RETRIES: int = 3
"""Times a stock is retried after Finnhub says the minute's limit was reached."""

HISTORY_WORKERS: int = 6
"""Price histories asked of one key at a time; its pace in :data:`HISTORY_PER_MINUTE`
keeps them within its plan's limit a minute."""

HISTORY_PER_MINUTE: dict[str, int] = {
    "tiingo": 150,
    "fmp": 250,
    "eodhd": 800,
    "alpha_vantage": 60,
}
"""Price histories asked of each key a minute, under the lowest paid plan's limit, so a
paid key is never cut off for the night by asking too fast: Tiingo's Power plan allows
10,000 an hour (166 a minute), FMP's Starter 300 a minute, every EODHD plan 1,000, and
Alpha Vantage's smallest premium plan 75. A free plan's daily or hourly limit stops its
key long before its pace matters."""

HISTORY_GIVE_UP_AFTER: int = 25
"""Price histories that may fail in a row before the pass stops, taking the user's
price-history keys to have reached their plans' limits for the night."""

PERCENT, MILLIONS, PLAIN = 0.01, 1e6, 1.0
FIELDS: dict[str, tuple[str, float]] = {
    "market_cap": ("marketCapitalization", MILLIONS),
    "pe_ratio": ("peTTM", PLAIN),
    "price_to_sales": ("psTTM", PLAIN),
    "ev_to_ebitda": ("evEbitdaTTM", PLAIN),
    "gross_margin": ("grossMarginTTM", PERCENT),
    "operating_margin": ("operatingMarginTTM", PERCENT),
    "net_margin": ("netProfitMarginTTM", PERCENT),
    "return_on_equity": ("roeTTM", PERCENT),
    "revenue_growth": ("revenueGrowthTTMYoy", PERCENT),
    "earnings_growth": ("epsGrowthTTMYoy", PERCENT),
    "return_6m": ("26WeekPriceReturnDaily", PERCENT),
    "return_52w": ("52WeekPriceReturnDaily", PERCENT),
    "volatility_3m": ("3MonthADReturnStd", PERCENT),
    "beta": ("beta", PLAIN),
    "average_volume": ("3MonthAverageTradingVolume", MILLIONS),
}
"""The Finnhub basic financials field each :class:`ProviderScreenMetrics` field comes
from, and the factor converting it: Finnhub gives ratios in percent and totals in
millions."""


ETF_EXCHANGES: dict[str, str] = {
    "ARCX": "NYSE Arca",
    "BATS": "Cboe BZX",
    "XNAS": "Nasdaq",
    "XNYS": "NYSE",
    "XASE": "NYSE American",
}
"""Exchanges whose ETFs are screened, by the market identifier code Finnhub gives, and the
name shown; ETFs traded over the counter are not."""

SYMBOL_LIST_TIMEOUT_SECONDS: float = 120.0
"""Time allowed for Finnhub's list of every US symbol, about 7 MB."""


@dataclass(frozen=True)
class EtfListing:
    """An exchange-traded fund listed on a US exchange, from Finnhub's symbol list."""

    symbol: str
    name: str
    exchange: str


def parse_etf_listings(rows: Any) -> list[EtfListing]:
    """The exchange-listed ETFs in Finnhub's list of US symbols, whose ``type`` is ``ETP``
    (exchange-traded products: ETFs, and the exchange-traded notes beside them)."""
    found: dict[str, EtfListing] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or row.get("type") != "ETP":
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        exchange = ETF_EXCHANGES.get(str(row.get("mic") or ""))
        if not symbol or exchange is None or not symbol.isalpha() or len(symbol) > 5:
            continue
        name = str(row.get("description") or symbol).strip()
        found[symbol] = EtfListing(symbol=symbol, name=name, exchange=exchange)
    return sorted(found.values(), key=lambda listing: listing.symbol)


async def fetch_etf_listings() -> list[EtfListing]:
    """Every ETF on a US exchange, from Finnhub's free list of US symbols on the user's
    key: one request.

    Raises:
        ProviderNotConfiguredError: If the user has no Finnhub key, or it is rejected.
        DataFetchError: If Finnhub fails.
    """
    api_key = finnhub_key()
    if api_key is None:
        raise ProviderNotConfiguredError("The nightly refresh needs your Finnhub key.")
    async with httpx2.AsyncClient(
        timeout=SYMBOL_LIST_TIMEOUT_SECONDS, follow_redirects=True
    ) as client:
        rows = await finnhub_get("stock/symbol", {"exchange": "US"}, api_key, client)
    return parse_etf_listings(rows)


def _number(value: object) -> float | None:
    """``value`` as a finite float, or ``None``."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def parse_screen_metrics(payload: Any) -> ProviderScreenMetrics | None:
    """Read Finnhub's basic financials; ``None`` when they hold no market cap."""
    values = payload.get("metric") if isinstance(payload, dict) else None
    if not isinstance(values, dict):
        return None
    fields: dict[str, float] = {}
    for name, (source, factor) in FIELDS.items():
        number = _number(values.get(source))
        if number is not None:
            fields[name] = number * factor
    price_to_fcf = _number(values.get("pfcfShareTTM"))
    if price_to_fcf is not None and price_to_fcf > 0:
        fields["fcf_yield"] = 1 / price_to_fcf
    if fields.get("market_cap", 0.0) <= 0:
        return None
    answered = payload.get("symbol")
    home = str(answered) if isinstance(answered, str) and "." in answered else None
    return ProviderScreenMetrics.model_validate(fields | {"home_listing": home})


def home_currency(listing: str) -> str | None:
    """The currency of a listing abroad, e.g. TWD for 2330.TW, from its suffix; ``None``
    for a suffix :data:`SUFFIX_CURRENCIES` does not know."""
    return SUFFIX_CURRENCIES.get(listing.rsplit(".", 1)[-1].upper())


def in_dollars(
    metrics: ProviderScreenMetrics, rates: Mapping[str, float]
) -> ProviderScreenMetrics | None:
    """``metrics`` with a market cap in US dollars.

    For a foreign company's US listing, Finnhub answers with its home market's figures: the
    market cap in the home currency (TSM's in Taiwan dollars, a thousand times too large
    if read as dollars) and the volume in home shares, which are not the US listing's
    (one TSM ADR is five shares in Taipei). The market cap is converted at ``rates`` (US
    dollars per unit) and the volume dropped, so the dollar volume comes from the US
    listing's own prices. Ratios, margins, growth, and returns need no conversion.

    Returns:
        The metrics, or ``None`` when the home currency or its rate is unknown, since a
        market cap in the wrong currency would rank and filter the stock wrongly.
    """
    if metrics.home_listing is None:
        return metrics
    currency = home_currency(metrics.home_listing)
    rate = None if currency is None else rates.get(currency)
    if rate is None or metrics.market_cap is None:
        logger.info("No dollar rate for %s's market cap; it is left out.", metrics.home_listing)
        return None
    return metrics.model_copy(
        update={"market_cap": metrics.market_cap * rate, "average_volume": None}
    )


T = TypeVar("T")


async def _paced_finnhub(
    symbols: Iterable[str],
    path: str,
    params: dict[str, str],
    parse: Callable[[Any], T | None],
) -> dict[str, T]:
    """Ask Finnhub's ``path`` about each of ``symbols`` on the user's key, paced at
    :data:`FINNHUB_NIGHTLY_SHARE` of the key's limit as Finnhub reports it, keeping what
    ``parse`` reads from each answer. Stocks Finnhub does not cover, or fails on, are left
    out.

    Raises:
        ProviderNotConfiguredError: If the user has no Finnhub key, or it is rejected.
    """
    api_key = finnhub_key()
    if api_key is None:
        raise ProviderNotConfiguredError("The nightly refresh needs your Finnhub key.")
    queue: asyncio.Queue[str] = asyncio.Queue()
    for symbol in symbols:
        queue.put_nowait(symbol)
    slowest = math.floor(FINNHUB_FREE_PER_MINUTE * FINNHUB_NIGHTLY_SHARE)
    pacer = RequestPacer(finnhub_requests_per_minute(api_key, FINNHUB_NIGHTLY_SHARE))
    # The most this refresh sends a minute, lowered each time Finnhub says it was too many.
    ceiling: float = float("inf")
    found: dict[str, T] = {}

    def follow_plan() -> None:
        """Set the pace from the key's limit as Finnhub last reported it."""
        rate = min(finnhub_requests_per_minute(api_key, FINNHUB_NIGHTLY_SHARE), ceiling)
        if rate != pacer.per_minute:
            pacer.set_rate(max(int(rate), 1))

    async def one(symbol: str, client: httpx2.AsyncClient) -> None:
        nonlocal ceiling
        query = {"symbol": finnhub_symbol(symbol)} | params
        for attempt in range(FINNHUB_RETRIES + 1):
            follow_plan()
            await pacer.wait()
            try:
                payload = await finnhub_get(path, query, api_key, client)
            except FinnhubRateLimitError as exc:
                ceiling = max(slowest, math.floor(pacer.per_minute * FINNHUB_THROTTLED_SHARE))
                if attempt == FINNHUB_RETRIES:
                    logger.info("Finnhub stayed at its limit; %s is left out.", symbol)
                    return
                await asyncio.sleep(exc.retry_after or 60)
                continue
            except ProviderNotConfiguredError:
                raise
            except DataFetchError as exc:
                logger.debug("No Finnhub %s for %s: %s", path, symbol, exc)
                return
            parsed = parse(payload)
            if parsed is not None:
                found[symbol] = parsed
            return

    async def worker(client: httpx2.AsyncClient) -> None:
        while not queue.empty():
            await one(queue.get_nowait(), client)

    async with httpx2.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        await asyncio.gather(*(worker(client) for _ in range(FINNHUB_WORKERS)))
    return found


def parse_etf_metrics(payload: Any) -> ProviderScreenMetrics | None:
    """Read Finnhub's basic financials of an ETF: its returns, volatility, beta, volume, and
    52-week high, which it gives for ETFs as for stocks, with no market cap or company
    figures. ``None`` without an average volume, since then it cannot be judged liquid."""
    values = payload.get("metric") if isinstance(payload, dict) else None
    if not isinstance(values, dict):
        return None
    fields: dict[str, float] = {}
    for name in ("return_6m", "return_52w", "volatility_3m", "beta", "average_volume"):
        source, factor = FIELDS[name]
        number = _number(values.get(source))
        if number is not None:
            fields[name] = number * factor
    high = _number(values.get("52WeekHigh"))
    if high is not None and high > 0:
        fields["week_52_high"] = high
    if fields.get("average_volume", 0.0) <= 0:
        return None
    return ProviderScreenMetrics.model_validate(fields)


async def fetch_etf_metrics(symbols: Iterable[str]) -> dict[str, ProviderScreenMetrics]:
    """Finnhub's metrics for each of the ETFs ``symbols`` it has them for, on the user's
    key, one request each.

    Raises:
        ProviderNotConfiguredError: If the user has no Finnhub key, or it is rejected.
    """
    return await _paced_finnhub(symbols, "stock/metric", {"metric": "all"}, parse_etf_metrics)


async def fetch_screen_metrics(symbols: Iterable[str]) -> dict[str, ProviderScreenMetrics]:
    """Finnhub's metrics for each of ``symbols`` it has them for, on the user's key; about
    80 minutes for 4,000 stocks.

    Raises:
        ProviderNotConfiguredError: If the user has no Finnhub key, or it is rejected.
    """
    found = await _paced_finnhub(symbols, "stock/metric", {"metric": "all"}, parse_screen_metrics)
    return await all_in_dollars(found)


async def all_in_dollars(
    found: Mapping[str, ProviderScreenMetrics],
) -> dict[str, ProviderScreenMetrics]:
    """Each of ``found``, metrics by the symbol asked about, with its market cap in US
    dollars (:func:`in_dollars`), fetching each home currency's rate once. Those whose
    currency has no rate are left out."""
    asked: dict[str, ProviderScreenMetrics] = {}
    for symbol, metrics in found.items():
        # A US share class such as BRK.B comes back as asked; only another listing is abroad.
        same = metrics.home_listing == finnhub_symbol(symbol)
        asked[symbol] = metrics.model_copy(update={"home_listing": None}) if same else metrics
    currencies = {
        currency
        for metrics in asked.values()
        if metrics.home_listing and (currency := home_currency(metrics.home_listing))
    }
    rates = await fetch_exchange_rates(currencies - {"USD"}) if currencies else {}
    rates["USD"] = 1.0
    converted = {symbol: in_dollars(metrics, rates) for symbol, metrics in asked.items()}
    return {symbol: metrics for symbol, metrics in converted.items() if metrics is not None}


def parse_quote_price(payload: Any) -> float | None:
    """The current price in a Finnhub quote; ``None`` for its all-zero answer."""
    price = _number(payload.get("c")) if isinstance(payload, dict) else None
    return price if price is not None and price > 0 else None


async def fetch_quote_prices(symbols: Iterable[str]) -> dict[str, float]:
    """The current price of each of ``symbols``, from Finnhub quotes on the user's key.

    Raises:
        ProviderNotConfiguredError: If the user has no Finnhub key, or it is rejected.
    """
    return await _paced_finnhub(symbols, "quote", {}, parse_quote_price)


async def fetch_histories(
    symbols: Iterable[str], start: date, limits: Mapping[Any, int | None] | None = None
) -> dict[str, pd.DataFrame]:
    """Daily ``Close`` and ``Volume`` since ``start`` for as many of ``symbols`` as the
    user's price-history keys allow tonight, in the order given.

    With ``limits``, each price-history key named fetches on its own, cheapest first, up
    to its limit (``None`` for as many as its plan answers), taking the symbols the keys
    before it did not get; one request is counted per symbol tried. Without, the keys
    answer together, cheapest first, for every symbol. Without any price-history key,
    nothing is fetched.
    """
    histories: dict[str, pd.DataFrame] = {}
    wanted = list(symbols)
    if limits is None:
        await _fetch_into(histories, wanted, start, None)
        return histories
    keys = current_provider_keys()
    try:
        for provider, limit in limits.items():
            if getattr(keys, provider, None) is None:
                continue
            # This key alone: no other key, and no fallback, answers in its turn.
            use_provider_keys(
                replace(
                    keys,
                    yfinance=False,
                    tiingo=keys.tiingo if provider == "tiingo" else None,
                    fmp=keys.fmp if provider == "fmp" else None,
                    eodhd=keys.eodhd if provider == "eodhd" else None,
                    alpha_vantage=keys.alpha_vantage if provider == "alpha_vantage" else None,
                )
            )
            left = [symbol for symbol in wanted if symbol not in histories]
            await _fetch_into(histories, left, start, limit, provider)
    finally:
        use_provider_keys(keys)
    return histories


async def _fetch_into(
    histories: dict[str, pd.DataFrame],
    symbols: list[str],
    start: date,
    limit: int | None,
    provider: str | None = None,
) -> None:
    """Add to ``histories`` the histories of ``symbols`` the current keys answer, trying at
    most ``limit`` of them, in order, and stopping once a key's limit for the night is
    reached or :data:`HISTORY_GIVE_UP_AFTER` fail in a row.

    One key on its own (``provider``) is asked :data:`HISTORY_WORKERS` at a time, paced
    under :data:`HISTORY_PER_MINUTE`, so a paid plan's thousands of histories take minutes
    rather than most of an hour; a free plan reaches its limit as soon as before. The keys
    answering together are asked one at a time.
    """
    cutoff = pd.Timestamp(start)
    who = provider or "the price-history keys"
    pace = HISTORY_PER_MINUTE.get(provider) if provider is not None else None
    pacer = RequestPacer(pace) if pace is not None else None
    taken = failures = 0
    stopped = False

    def stop(message: str, *args: object) -> None:
        nonlocal stopped
        if not stopped:
            logger.info(message, *args)
        stopped = True

    # The symbols this key may try: no more than its limit, claimed in order.
    allowed = len(symbols) if limit is None else min(limit, len(symbols))

    async def fetch_one() -> None:
        nonlocal taken, failures
        # Claimed before any wait, so the symbols are tried in order.
        symbol = symbols[taken]
        taken += 1
        if pacer is not None:
            await pacer.wait()
        if stopped:
            return
        try:
            frame = await fetch_price_history(symbol, "2y", "1d")
        except ProviderNotConfiguredError:
            stop("No key for %s, so it fetches no price histories.", who)
            return
        except ProviderLimitError:
            stop("%s's plan reached its limit for now after %d price histories.", who, taken - 1)
            return
        except DataFetchError as exc:
            failures += 1
            logger.debug("No price history for %s: %s", symbol, exc)
            if failures >= HISTORY_GIVE_UP_AFTER:
                stop(
                    "Price histories on %s failed %d times in a row, so its plan's limit is "
                    "taken to be reached.",
                    who,
                    failures,
                )
            return
        failures = 0
        index = pd.DatetimeIndex(frame.index)
        if index.tz is not None:
            index = index.tz_localize(None)
        window = frame.set_axis(index)[["Close", "Volume"]]
        window = window[window.index >= cutoff]
        if not window.empty:
            histories[symbol] = window

    async def worker() -> None:
        while not stopped and taken < allowed:
            await fetch_one()

    await asyncio.gather(*(worker() for _ in range(HISTORY_WORKERS if pacer is not None else 1)))
    if not stopped and limit is not None and taken >= limit and len(symbols) > limit:
        logger.info("%s reached its limit of %d price histories.", who, limit)
