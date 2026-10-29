"""Signing in with GitHub or Google, by the OAuth 2.0 authorization code flow.

The browser is sent to the provider with a random ``state``, which comes back with the
code and must match the one kept in a cookie, and a PKCE challenge, whose verifier the
code is exchanged with. The provider then names the person by its own permanent ID
(``subject``) and a verified email address; an address the provider has not verified is
refused, since accounts are matched by it.
"""

import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlencode

import httpx2
from pydantic import SecretStr

from app.core.config import Settings

ProviderName = Literal["github", "google"]

HTTP_TIMEOUT_SECONDS = 10.0


class OAuthError(Exception):
    """Raised when signing in fails; the message is shown to the person signing in."""


@dataclass(frozen=True)
class Identity:
    """Who the provider says signed in: its permanent ID for them, and a verified email."""

    subject: str
    email: str


@dataclass(frozen=True)
class OAuthProvider:
    """A provider people sign in with, and this app's client registered with it."""

    name: ProviderName
    label: str
    authorize_url: str
    token_url: str
    scope: str
    client_id: str
    client_secret: SecretStr

    def authorization_url(self, redirect_uri: str, state: str, verifier: str) -> str:
        """Where to send the browser to sign in, coming back to ``redirect_uri``."""
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "scope": self.scope,
            "state": state,
            "code_challenge": code_challenge(verifier),
            "code_challenge_method": "S256",
        }
        if self.name == "google":
            params["prompt"] = "select_account"
        return f"{self.authorize_url}?{urlencode(params)}"

    async def identity(self, code: str, redirect_uri: str, verifier: str) -> Identity:
        """Exchange the code the provider sent back for who signed in.

        Raises:
            OAuthError: If the provider refuses the code or has no verified email.
        """
        async with httpx2.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            try:
                token = await self._token(client, code, redirect_uri, verifier)
                if self.name == "github":
                    return await _github_identity(client, token)
                return await _google_identity(client, token)
            except httpx2.HTTPError as exc:
                raise OAuthError(f"{self.label} could not be reached: {exc}") from exc

    async def _token(
        self, client: httpx2.AsyncClient, code: str, redirect_uri: str, verifier: str
    ) -> str:
        response = await client.post(
            self.token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": self.client_id,
                "client_secret": self.client_secret.get_secret_value(),
                "code_verifier": verifier,
            },
            headers={"Accept": "application/json"},
        )
        payload: Any = response.json() if response.content else {}
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if response.status_code != 200 or not isinstance(token, str):
            detail = payload.get("error_description") or payload.get("error") or "no token"
            raise OAuthError(f"{self.label} did not accept the sign-in: {detail}.")
        return token


async def _github_identity(client: httpx2.AsyncClient, token: str) -> Identity:
    """The GitHub user, and their primary verified email or else any verified one."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    user = (await client.get("https://api.github.com/user", headers=headers)).json()
    emails = (await client.get("https://api.github.com/user/emails", headers=headers)).json()
    verified = [entry for entry in emails if isinstance(entry, dict) and entry.get("verified")]
    verified.sort(key=lambda entry: not entry.get("primary"))
    if "id" not in user or not verified:
        raise OAuthError(
            "Your GitHub account has no verified email address. Verify one in GitHub's "
            "email settings, then sign in again."
        )
    return Identity(subject=str(user["id"]), email=str(verified[0]["email"]))


async def _google_identity(client: httpx2.AsyncClient, token: str) -> Identity:
    """The Google account, if its email is verified."""
    info = (
        await client.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {token}"},
        )
    ).json()
    if not info.get("sub") or not info.get("email") or info.get("email_verified") is not True:
        raise OAuthError("Your Google account's email address is not verified.")
    return Identity(subject=str(info["sub"]), email=str(info["email"]))


def code_challenge(verifier: str) -> str:
    """The PKCE S256 challenge for ``verifier``."""
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def new_secret() -> str:
    """A random, URL-safe value for a ``state`` or PKCE verifier."""
    return secrets.token_urlsafe(32)


def configured_providers(settings: Settings) -> dict[ProviderName, OAuthProvider]:
    """The providers this server has OAuth clients for, in the order they are shown."""
    providers: dict[ProviderName, OAuthProvider] = {}
    if settings.github_client_id and settings.github_client_secret:
        providers["github"] = OAuthProvider(
            name="github",
            label="GitHub",
            authorize_url="https://github.com/login/oauth/authorize",
            token_url="https://github.com/login/oauth/access_token",
            # Only the email addresses: the user's ID comes with any token.
            scope="user:email",
            client_id=settings.github_client_id,
            client_secret=settings.github_client_secret,
        )
    if settings.google_client_id and settings.google_client_secret:
        providers["google"] = OAuthProvider(
            name="google",
            label="Google",
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            # Only the ID and email: no name or photo.
            scope="openid email",
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
        )
    return providers
