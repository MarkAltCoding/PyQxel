"""Tests of the native app's settings that the server depends on: its Content Security
Policy must allow the server it is built to call, and be no looser than the website's.
"""

import json
import re

from app.core.config import PROJECT_ROOT, Settings
from app.web import PAGE_CONTENT_SECURITY_POLICY

WEB = PROJECT_ROOT / "web"


def _app_policy(name: str = "tauri.conf.json") -> dict[str, str]:
    config = json.loads((WEB / "src-tauri" / name).read_text())
    policy: dict[str, str] = config["app"]["security"]["csp"]
    return policy


def _api_origin() -> str:
    found = re.search(r"^VITE_API_ORIGIN=(\S+)$", (WEB / ".env.native").read_text(), re.M)
    assert found, "web/.env.native names the server the app calls"
    return found.group(1)


def test_the_app_may_call_only_its_server_and_its_own_native_side() -> None:
    origin = _api_origin()
    allowed = _app_policy()["connect-src"].split()
    assert sorted(allowed) == sorted(
        ["'self'", "ipc:", "http://ipc.localhost", origin, origin.replace("https:", "wss:", 1)]
    )


def test_the_app_policy_matches_the_websites_apart_from_where_it_connects() -> None:
    """Every directive the website sets, the app sets the same, except connect-src (the
    app calls the server by address) and frame-ancestors, which the app also sets."""
    website = dict(part.split(" ", 1) for part in PAGE_CONTENT_SECURITY_POLICY.split("; ") if part)
    for name in ("tauri.conf.json", "tauri.local.conf.json"):
        app = _app_policy(name)
        assert set(app) == set(website), name
        for directive, sources in website.items():
            if directive != "connect-src":
                assert app[directive] == sources, (name, directive)


def test_the_local_policy_reaches_only_a_local_server() -> None:
    allowed = _app_policy("tauri.local.conf.json")["connect-src"].split()
    assert all(
        source in ("'self'", "ipc:", "http://ipc.localhost") or "localhost:8000" in source
        for source in allowed
    )


def test_the_server_answers_the_apps_origins() -> None:
    assert {"tauri://localhost", "http://tauri.localhost"} <= set(Settings().cors_origins)
