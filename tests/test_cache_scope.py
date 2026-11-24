"""Tests that a provider's data, cached once, is served again only on the key that fetched
it, never to another user's key; public data is shared."""

import asyncio
from typing import Any

from pydantic import SecretStr

from app.core import cache
from app.core.credentials import ProviderKeys, use_provider_keys
from app.data import finnhub
from app.data.fetcher import key_scope


def test_the_scope_names_a_key_by_a_hash_never_the_key() -> None:
    assert key_scope(None) == ""
    scope = key_scope("secret-finnhub-key-123")
    assert scope.startswith("@") and "secret" not in scope
    assert scope == key_scope("secret-finnhub-key-123") != key_scope("another-key-456")


def test_one_users_finnhub_answer_is_not_served_to_another_key(monkeypatch: Any) -> None:
    asked: list[str] = []

    async def fake_get(path: str, params: dict[str, str], api_key: str, client: object) -> Any:
        asked.append(api_key)
        return {"answered_for": api_key}

    monkeypatch.setattr(finnhub, "finnhub_get", fake_get)
    cache.configure_cache(None, memory=True)
    try:
        for key in ("first-users-key-0001", "first-users-key-0001", "other-users-key-0002"):
            use_provider_keys(ProviderKeys(finnhub=SecretStr(key)))
            answer = asyncio.run(finnhub.cached_get("stock/metric", {"symbol": "AAPL"}, None))
            assert answer == {"answered_for": key}
    finally:
        cache.configure_cache(None)
        use_provider_keys(ProviderKeys())
        finnhub.clear_signals_memory()

    # The first user's repeat came from the cache; the other user's key was asked itself.
    assert asked == ["first-users-key-0001", "other-users-key-0002"]
