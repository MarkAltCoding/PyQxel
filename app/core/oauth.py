"""Signing in with GitHub, Google or Apple, by the OAuth 2.0 authorization code flow.

The browser is sent to the provider with a random ``state``, which comes back with the
code and must match the one kept in a cookie, and a PKCE challenge, whose verifier the
code is exchanged with. The provider then names the person by its own permanent ID
(``subject``) and a verified email address; an address the provider has not verified is
refused, since accounts are matched by it.

Apple differs in three ways: it posts the code back as a form instead of a redirect, its
client secret is a short-lived token this server signs with a private key from Apple, and
it names the person in an ID token that comes with the access token. Sign in with Apple
stays off until ``APPLE_SIGN_IN_ENABLED`` is set along with Apple's four values.
"""

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlencode

import httpx2
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from pydantic import SecretStr

from app.core.config import Settings

ProviderName = Literal["github", "google", "apple"]

PROVIDER_LABELS: dict[str, str] = {"github": "GitHub", "google": "Google", "apple": "Apple"}
"""Each provider's name as people know it."""

APPLE_ISSUER = "https://appleid.apple.com"
"""Apple's sign-in service: the audience of the client secrets signed for it, and the
issuer of the ID tokens it returns."""
APPLE_CLIENT_SECRET_SECONDS = 5 * 60
"""How long a client secret signed for Apple is valid; one is signed for each sign-in."""

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

    @property
    def posts_back(self) -> bool:
        """Whether the provider returns by posting a form across sites, which needs the
        sign-in cookie sent with cross-site requests."""
        return False

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

    def _token_fields(self, verifier: str) -> dict[str, str]:
        """The client's credentials and PKCE verifier, sent to exchange a code."""
        return {
            "client_secret": self.client_secret.get_secret_value(),
            "code_verifier": verifier,
        }

    async def identity(self, code: str, redirect_uri: str, verifier: str) -> Identity:
        """Exchange the code the provider sent back for who signed in.

        Raises:
            OAuthError: If the provider refuses the code or has no verified email.
        """
        async with httpx2.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            try:
                payload = await self._token(client, code, redirect_uri, verifier)
                return await self._identity(client, payload)
            except httpx2.HTTPError as exc:
                raise OAuthError(f"{self.label} could not be reached: {exc}") from exc

    async def _identity(self, client: httpx2.AsyncClient, payload: dict[str, Any]) -> Identity:
        """Who signed in, from the provider's token response."""
        token = str(payload["access_token"])
        if self.name == "github":
            return await _github_identity(client, token)
        return await _google_identity(client, token)

    async def _token(
        self, client: httpx2.AsyncClient, code: str, redirect_uri: str, verifier: str
    ) -> dict[str, Any]:
        """The provider's token response for ``code``, which holds an access token."""
        response = await client.post(
            self.token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": self.client_id,
                **self._token_fields(verifier),
            },
            headers={"Accept": "application/json"},
        )
        body: Any = response.json() if response.content else {}
        payload: dict[str, Any] = body if isinstance(body, dict) else {}
        token = payload.get("access_token")
        if response.status_code != 200 or not isinstance(token, str):
            detail = payload.get("error_description") or payload.get("error") or "no token"
            raise OAuthError(f"{self.label} did not accept the sign-in: {detail}.")
        return payload


@dataclass(frozen=True)
class AppleProvider(OAuthProvider):
    """Sign in with Apple, through a Services ID and a private key from Apple.

    ``client_id`` is the Services ID. ``client_secret`` is unused: a secret is signed for
    each sign-in with ``private_key`` (the ``.p8`` file's text), named by ``key_id`` and
    issued by the Apple developer team ``team_id``.
    """

    team_id: str = ""
    key_id: str = ""
    private_key: SecretStr = SecretStr("")

    @property
    def posts_back(self) -> bool:
        return True

    def authorization_url(self, redirect_uri: str, state: str, verifier: str) -> str:
        """Apple's sign-in page. Asking for the email makes Apple post its answer back as
        a form; Apple does not take a PKCE challenge, so the ``state`` checked against the
        sign-in cookie is what ties the answer to this browser."""
        params = {
            "response_type": "code",
            "response_mode": "form_post",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "scope": self.scope,
            "state": state,
        }
        return f"{self.authorize_url}?{urlencode(params)}"

    def _token_fields(self, verifier: str) -> dict[str, str]:
        return {"client_secret": self.signed_client_secret()}

    def signed_client_secret(self, now: float | None = None) -> str:
        """A client secret for Apple: a token signed with ES256, valid for a few minutes.

        Raises:
            OAuthError: If the private key is not an elliptic-curve key in PEM form.
        """
        try:
            key = serialization.load_pem_private_key(
                self.private_key.get_secret_value().encode(), password=None
            )
        except ValueError as exc:
            raise OAuthError("Sign in with Apple is set up with an unreadable key.") from exc
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise OAuthError("Sign in with Apple needs the elliptic-curve key Apple issues.")
        issued = int(time.time() if now is None else now)
        header = {"alg": "ES256", "kid": self.key_id}
        claims = {
            "iss": self.team_id,
            "iat": issued,
            "exp": issued + APPLE_CLIENT_SECRET_SECONDS,
            "aud": APPLE_ISSUER,
            "sub": self.client_id,
        }
        signing_input = f"{_b64_json(header)}.{_b64_json(claims)}".encode()
        r, s = decode_dss_signature(key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
        signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        return f"{signing_input.decode()}.{_b64(signature)}"

    async def _identity(self, client: httpx2.AsyncClient, payload: dict[str, Any]) -> Identity:
        """The person named in Apple's ID token, if their email is verified.

        The token comes straight from Apple over TLS, in answer to this server's own
        signed request, so its claims are trusted without checking its signature, as
        OpenID Connect allows for tokens from the token endpoint.
        """
        claims = _unverified_claims(payload.get("id_token"))
        verified = claims.get("email_verified") in (True, "true")
        if (
            claims.get("iss") != APPLE_ISSUER
            or claims.get("aud") != self.client_id
            or not claims.get("sub")
        ):
            raise OAuthError("Apple did not say who signed in. Please try again.")
        if not claims.get("email") or not verified:
            raise OAuthError("Your Apple ID's email address is not verified.")
        return Identity(subject=str(claims["sub"]), email=str(claims["email"]))


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64_json(value: dict[str, Any]) -> str:
    return _b64(json.dumps(value, separators=(",", ":")).encode())


def _unverified_claims(token: Any) -> dict[str, Any]:
    """The claims of a JSON Web Token, without checking its signature; empty if it is
    not one."""
    if not isinstance(token, str) or token.count(".") != 2:
        return {}
    body = token.split(".")[1]
    try:
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except ValueError:
        return {}
    return claims if isinstance(claims, dict) else {}


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
            label=PROVIDER_LABELS["github"],
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
            label=PROVIDER_LABELS["google"],
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            # Only the ID and email: no name or photo.
            scope="openid email",
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
        )
    if (
        settings.apple_sign_in_enabled
        and settings.apple_client_id
        and settings.apple_team_id
        and settings.apple_key_id
        and settings.apple_private_key
    ):
        providers["apple"] = AppleProvider(
            name="apple",
            label=PROVIDER_LABELS["apple"],
            authorize_url=f"{APPLE_ISSUER}/auth/authorize",
            token_url=f"{APPLE_ISSUER}/auth/token",
            # Only the email: Apple sends the person's name once, and it is not kept.
            scope="email",
            client_id=settings.apple_client_id,
            client_secret=SecretStr(""),
            team_id=settings.apple_team_id,
            key_id=settings.apple_key_id,
            private_key=settings.apple_private_key,
        )
    return providers
