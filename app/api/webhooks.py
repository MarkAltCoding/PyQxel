"""Routes services call to tell PyQxel what happened: left out of the API schema, and
refused unless the call is signed.

``POST /webhooks/stripe`` receives Stripe's events about payments and subscriptions
(:data:`app.billing.stripe_api.EVENTS`), verified by their signature, and applies them
(:func:`app.billing.stripe_api.handle_event`).
"""

import logging
from typing import Annotated

import stripe
from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from sqlalchemy.exc import SQLAlchemyError

from app.billing import stripe_api
from app.db.session import get_sessionmaker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", include_in_schema=False)


@router.post("/stripe", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def stripe_event(
    request: Request,
    stripe_signature: Annotated[str | None, Header()] = None,
) -> Response:
    """Apply one event from Stripe.

    Answers 404 on a server that does not bill, 400 unless Stripe's signature proves the
    event came from it, and 503 when the database is down, so Stripe sends it again later.
    """
    if not stripe_api.billing_enabled():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    payload = await request.body()
    try:
        event = stripe_api.verify_event(payload, stripe_signature or "")
    except stripe_api.BillingNotConfiguredError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found") from exc
    except (stripe.SignatureVerificationError, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Not a signed Stripe event.") from exc
    try:
        async with get_sessionmaker()() as session:
            await stripe_api.handle_event(session, event)
    except SQLAlchemyError as exc:
        logger.warning("Stripe event %s was not applied: %s", event.id, exc)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "The database cannot be written."
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
