"""About how many requests a page would send on each of the user's keys, before it loads.

Pages ask before calling providers. To say what loading would cost, each thing a page
would fetch is walked through the same order of keys the fetch itself would try
(:data:`app.data.fetcher.PROVIDER_ORDER`): a key whose limit is reached or whose plan
refused is passed over, as the fetch would pass it, and a price history the user's stored
prices already hold costs nothing. Caches can make the real count lower, and a provider
failing on the day can pass a request on, so the figures are "about".
"""

from collections import Counter
from datetime import UTC, date, datetime

from app.core.cache import cache_get
from app.core.credentials import current_provider_keys
from app.data import fetcher, usage
from app.data.fetcher import (
    INTRADAY_INTERVALS,
    STORE_INTERVALS,
    Provider,
    ProviderNotConfiguredError,
    ProviderPlanError,
    available_providers,
)

FINNHUB_COSTS: dict[str, int] = {
    "news": 1,
    "metrics": 2,
    "signals": 5,
    "alternative": 4,
    "market_news": 1,
    "calendar": 4,
}
"""Finnhub requests each Finnhub-backed section sends per symbol (or once, for the Markets
page's), before its own caches, which keep answers for minutes to a day."""

PAUSE_PREFIXES: dict[Provider, str] = {
    "tiingo": "tiingo",
    "fmp": "fmp",
    "eodhd": "eodhd",
    "finnhub": "finnhub",
    "alpha_vantage": "alphavantage",
}
"""The prefix of each provider's remembered pauses, as :mod:`app.data.fetcher` keeps them."""


async def paused_until(provider: str, api_key: str) -> datetime | None:
    """When PyQxel will ask ``provider`` again on ``api_key``, if it answered that the key's
    limit was reached; ``None`` when it did not."""
    prefix = PAUSE_PREFIXES.get(provider)  # type: ignore[call-overload]
    if prefix is None:
        return None
    return await fetcher._recall(f"{prefix}:paused:{fetcher._key_digest(api_key)}")


async def paused(provider: Provider, api_key: str) -> bool:
    """Whether ``provider`` is left out for ``api_key`` because its limit was reached."""
    return await paused_until(provider, api_key) is not None


async def _first_open(kind: fetcher.DataKind, symbol: str) -> tuple[Provider, str] | None:
    """The first of the user's keys that would be asked for ``kind`` of ``symbol``."""
    try:
        providers = available_providers(kind, symbol)
    except ProviderNotConfiguredError, ProviderPlanError:
        return None
    for provider, api_key in providers:
        if provider == "yfinance" or api_key is None:
            continue
        if not await paused(provider, api_key):
            return provider, api_key
    return None


async def _history_cost(symbol: str, period: str, interval: str) -> tuple[Provider | None, bool]:
    """The provider a history would be asked of, or ``None``, and whether the user's
    stored prices would serve it with no request."""
    keys = current_provider_keys()
    intraday = interval in INTRADAY_INTERVALS
    if not intraday and interval in STORE_INTERVALS and keys.user_id is not None:
        try:
            providers = [p for p, _ in available_providers("history", symbol) if p != "yfinance"]
        except ProviderNotConfiguredError, ProviderPlanError:
            return None, False
        stored = await fetcher._load_stored(keys.user_id, symbol, providers)
        ready = fetcher._daily_ready(symbol, datetime.now(UTC))
        start = fetcher._window_start(period, date.today())
        for provider in providers:
            kept = stored.get(provider)
            if kept is None or kept.fetched_at < ready:
                continue
            if kept.notice is not None or fetcher._covers(kept, start):
                return None, True
    found = await _first_open("intraday" if intraday else "history", symbol)
    return (None if found is None else found[0]), False


async def _info_cost(symbol: str) -> Counter[Provider]:
    """The requests a company profile would send: the first open key's profile, and the
    Tiingo lookup that fills in a missing sector, unless they are cached."""
    cost: Counter[Provider] = Counter()
    found = await _first_open("info", symbol)
    if found is None:
        return cost
    provider, _ = found
    if await cache_get(f"info:{provider}:{symbol}") is not None:
        return cost
    if provider == "finnhub":
        cached = await cache_get(f"finnhub:profile:{symbol}") is not None
        cost["finnhub"] += 1 if cached else 2
    elif provider == "tiingo":
        # Its listing and its company meta (or, once a week, what kind of listing it is),
        # and a quote for the price Tiingo's profile lacks.
        cost["tiingo"] += 2
        quote = await _first_open("quote", symbol)
        if quote is not None:
            cost[quote[0]] += 1
    else:
        cost[provider] += 1
    keys = current_provider_keys()
    if keys.tiingo is not None and provider != "tiingo" and fetcher.symbol_kind(symbol) == "us":
        meta = f"tiingo:meta:{symbol}"
        secret = keys.tiingo.get_secret_value()
        withheld = f"tiingo:withheld:{fetcher._key_digest(secret)}"
        if meta not in fetcher._tiingo_meta_memory and await cache_get(meta) is None:
            if not await paused("tiingo", secret) and await fetcher._recall(withheld) is None:
                cost["tiingo"] += 1
    return cost


async def _fund_cost(symbol: str) -> Counter[Provider]:
    """The requests a fund's holdings and costs would send: only for a symbol already known
    to be a fund, as the page asks for them only then. Alpha Vantage's profile, unless
    cached, and Finnhub's figures for up to :data:`app.data.funds.FINNHUB_LOOKUPS` of its
    largest holdings, fewer when the user's screener or the cache has them; the SEC's
    filings need no key."""
    from app.data.funds import FINNHUB_LOOKUPS  # It imports the database layer.

    cost: Counter[Provider] = Counter()
    keys = current_provider_keys()
    if fetcher.symbol_kind(symbol) != "us":
        return cost
    type_key = f"tiingo:type:{symbol}"
    known = fetcher._tiingo_meta_memory.get(type_key)
    quote_type = known[1].get("type") if known is not None else await cache_get(type_key)
    if quote_type not in fetcher.FUND_TYPES or quote_type == fetcher.MONEY_MARKET_TYPE:
        return cost
    if keys.alpha_vantage is not None:
        secret = keys.alpha_vantage.get_secret_value()
        cached = f"alphavantage:ETF_PROFILE:symbol={symbol}"
        if (
            not await paused("alpha_vantage", secret)
            and cached not in fetcher._alpha_vantage_memory
            and await cache_get(cached) is None
        ):
            cost["alpha_vantage"] += 1
    if keys.finnhub is not None and not await paused("finnhub", keys.finnhub.get_secret_value()):
        cost["finnhub"] += FINNHUB_LOOKUPS
    return cost


async def estimate(
    fetches: list[tuple[str, list[str], str, str]],
) -> tuple[Counter[Provider], dict[Provider, set[str]], int]:
    """About how many requests ``fetches`` would send on each key.

    Args:
        fetches: Each thing a page would fetch: its kind, symbols, period and interval.

    Returns:
        The requests per provider, the symbols asked of each provider, and how many
        histories the user's stored prices would serve free.
    """
    requests: Counter[Provider] = Counter()
    symbols: dict[Provider, set[str]] = {}
    from_store = 0
    keys = current_provider_keys()
    for kind, names, period, interval in fetches:
        cleaned = [name.strip().upper() for name in names if name.strip()]
        if kind in FINNHUB_COSTS:
            if keys.finnhub is None:
                continue
            requests["finnhub"] += FINNHUB_COSTS[kind] * max(1, len(cleaned))
            continue
        for symbol in cleaned:
            if kind == "history":
                provider, stored = await _history_cost(symbol, period, interval)
                if stored:
                    from_store += 1
                elif provider is not None:
                    requests[provider] += 1
                    symbols.setdefault(provider, set()).add(symbol)
            elif kind == "quote":
                found = await _first_open("quote", symbol)
                if found is not None:
                    requests[found[0]] += 1
                    symbols.setdefault(found[0], set()).add(symbol)
            elif kind == "fund":
                cost = await _fund_cost(symbol)
                requests.update(cost)
                for provider in cost:
                    symbols.setdefault(provider, set()).add(symbol)
            elif kind == "info":
                cost = await _info_cost(symbol)
                requests.update(cost)
                for provider in cost:
                    symbols.setdefault(provider, set()).add(symbol)
    return requests, symbols, from_store


async def new_symbols(provider: usage.UsageProvider, api_key: str, asked: set[str]) -> int:
    """How many of ``asked`` have not been asked of ``provider`` on this key this month."""
    seen = await usage.symbols_counted(
        provider, api_key, usage.month_of(provider, datetime.now(UTC))
    )
    return len({fetcher.tiingo_symbol(name).upper() for name in asked} - seen)
