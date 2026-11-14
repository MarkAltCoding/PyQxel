"""Tests for what an account enters in the app being kept on the server, so it follows
the account to every browser and device and through every update."""

import asyncio

from fastapi.testclient import TestClient

from app.db.session import get_sessionmaker
from app.db.users import User, create_api_key
from app.main import app
from app.models.saved_state import MAX_SAVED_KEYS, MAX_SAVED_VALUE_LENGTH
from tests.conftest import make_user

client = TestClient(app)

SAVED = "/api/v1/me/saved"


def _put(items: dict[str, str | None], headers: dict[str, str] | None = None) -> dict[str, str]:
    response = client.put(SAVED, json={"items": items}, headers=headers)
    assert response.status_code == 200, response.text
    saved: dict[str, str] = response.json()["items"]
    return saved


def test_nothing_is_saved_at_first(user: User) -> None:
    assert client.get(SAVED).json() == {"account": user.id, "items": {}}


def test_values_are_saved_replaced_and_forgotten_by_name() -> None:
    code = "def strategy(bars):\n    return bars['Close'] * 0  # 'quotes' and \"more\"\n"
    _put({"pyqxel.strategyDraft.python.one": code, "pyqxel.screen": '{"universe":"sp500"}'})

    after = _put({"pyqxel.screen": '{"universe":"all"}', "pyqxel.theme": "dark"})

    assert after == {
        "pyqxel.strategyDraft.python.one": code,
        "pyqxel.screen": '{"universe":"all"}',
        "pyqxel.theme": "dark",
    }
    assert _put({"pyqxel.theme": None, "pyqxel.never-saved": None}) == {
        "pyqxel.strategyDraft.python.one": code,
        "pyqxel.screen": '{"universe":"all"}',
    }
    assert client.get(SAVED).json()["items"].keys() == {
        "pyqxel.strategyDraft.python.one",
        "pyqxel.screen",
    }


def test_keys_and_sign_ins_are_never_saved() -> None:
    """The browser's sign-in key and provider keys stay in it, whatever a client sends."""
    for name in ("pyqxel.apiKey", "pyqxel.providerKeys", "pyqxel.appSignIn", "my.password"):
        response = client.put(SAVED, json={"items": {name: "sk-secret"}})
        assert response.status_code == 422, name
    assert client.get(SAVED).json()["items"] == {}


def test_names_and_sizes_are_bounded() -> None:
    assert client.put(SAVED, json={"items": {"has space": "x"}}).status_code == 422
    too_long = "x" * (MAX_SAVED_VALUE_LENGTH + 1)
    assert client.put(SAVED, json={"items": {"pyqxel.big": too_long}}).status_code == 422

    _put({f"pyqxel.k{i}": "v" for i in range(MAX_SAVED_KEYS)})
    full = client.put(SAVED, json={"items": {"pyqxel.one-more": "v"}})

    assert full.status_code == 413
    assert _put({"pyqxel.k0": "changed"})["pyqxel.k0"] == "changed", "replacing still works"
    assert _put({"pyqxel.k1": None, "pyqxel.one-more": "v"})["pyqxel.one-more"] == "v"


def test_each_account_has_its_own(user: User, real_auth: None) -> None:
    async def key_for(email: str) -> tuple[str, dict[str, str]]:
        other = await make_user(email)
        async with get_sessionmaker()() as session:
            key = (await create_api_key(session, other.id, "test")).key
        return other.id, {"Authorization": f"Bearer {key}"}

    alice_id, alice = asyncio.run(key_for("alice@example.invalid"))
    bob_id, bob = asyncio.run(key_for("bob@example.invalid"))

    _put({"pyqxel.screen": "alice's"}, alice)
    _put({"pyqxel.screen": "bob's"}, bob)

    assert client.get(SAVED, headers=alice).json() == {
        "account": alice_id,
        "items": {"pyqxel.screen": "alice's"},
    }
    assert client.get(SAVED, headers=bob).json()["items"] == {"pyqxel.screen": "bob's"}
    assert client.get(SAVED).status_code == 401


def test_saved_values_survive_a_new_sign_in(user: User, real_auth: None) -> None:
    """A value saved under one key of the account is there under another: what follows
    the account is the account's, not the browser's or the sign-in's."""

    async def key() -> dict[str, str]:
        async with get_sessionmaker()() as session:
            return {"Authorization": f"Bearer {(await create_api_key(session, user.id, 't')).key}"}

    first, second = asyncio.run(key()), asyncio.run(key())

    _put({"pyqxel.reviewProfile": '{"risk":"moderate"}'}, first)

    assert client.get(SAVED, headers=second).json()["items"] == {
        "pyqxel.reviewProfile": '{"risk":"moderate"}'
    }
