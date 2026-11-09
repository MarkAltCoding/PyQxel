"""Starting a Cloud Run job from the service, and checking that Cloud Scheduler is the caller.

The nightly refresh job has work only at the hours users chose, but Cloud Scheduler cannot
know those hours. So Scheduler calls the service every hour instead
(:mod:`app.api.internal`), which is kept warm anyway; the service checks the database and
starts the job only when a refresh is due, rather than the job starting every hour to find
nothing to do.

Scheduler signs each call with a Google identity token for its own service account. The
token is checked with Google's ``tokeninfo`` endpoint, which verifies its signature and
expiry; this module then checks who it names and who it was made for. The job is started
through the Cloud Run Admin API with the service's own account, whose token comes from the
metadata server every Cloud Run instance has.
"""

import time
from collections.abc import Mapping
from typing import Any

import httpx2

TOKENINFO_URL: str = "https://oauth2.googleapis.com/tokeninfo"
"""Google's endpoint that verifies an identity token and returns its claims."""

METADATA_TOKEN_URL: str = (
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
)
"""Where a Cloud Run instance gets an access token for its own service account."""

RUN_API_URL: str = "https://run.googleapis.com/v2"

GOOGLE_ISSUERS: frozenset[str] = frozenset({"https://accounts.google.com", "accounts.google.com"})
"""The issuers of Google's identity tokens."""

HTTP_TIMEOUT_SECONDS: float = 10.0


class JobStartError(RuntimeError):
    """Raised when a Cloud Run job could not be started."""


def caller_matches(claims: Mapping[str, Any], audience: str, email: str, now: float) -> bool:
    """Whether verified token ``claims`` name ``email``'s service account, were made for
    ``audience``, come from Google, and have not expired."""
    try:
        expires = float(claims.get("exp", 0))
    except TypeError, ValueError:
        return False
    return (
        claims.get("iss") in GOOGLE_ISSUERS
        and claims.get("aud") == audience
        and claims.get("email") == email
        and str(claims.get("email_verified", "")).lower() == "true"
        and expires > now
    )


async def verify_caller(token: str, audience: str, email: str) -> bool:
    """Whether ``token`` is a valid Google identity token for ``email`` and ``audience``;
    ``False`` as well when Google cannot be reached to check it."""
    try:
        async with httpx2.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            response = await client.get(TOKENINFO_URL, params={"id_token": token})
    except httpx2.HTTPError:
        return False
    if response.status_code != 200:
        return False
    try:
        claims = response.json()
    except ValueError:
        return False
    return isinstance(claims, dict) and caller_matches(claims, audience, email, time.time())


async def run_job(name: str) -> None:
    """Start an execution of the Cloud Run job ``name`` (``projects/P/locations/R/jobs/J``)
    with the service's own account, without waiting for it to finish.

    Raises:
        JobStartError: If no access token could be had, or the Admin API refused.
    """
    try:
        async with httpx2.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            token_response = await client.get(
                METADATA_TOKEN_URL, headers={"Metadata-Flavor": "Google"}
            )
            if token_response.status_code != 200:
                raise JobStartError("No access token: this is not running on Cloud Run.")
            token = token_response.json()["access_token"]
            response = await client.post(
                f"{RUN_API_URL}/{name}:run", headers={"Authorization": f"Bearer {token}"}
            )
    except (httpx2.HTTPError, KeyError, ValueError) as exc:
        raise JobStartError(f"The job could not be started: {exc}") from exc
    if response.status_code >= 400:
        raise JobStartError(
            f"Cloud Run refused to start the job ({response.status_code}): {response.text[:300]}"
        )
