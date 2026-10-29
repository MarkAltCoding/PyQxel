"""Storage of AI-written portfolio reviews, and of the coding assistant's answer count."""

import hashlib
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.tables import AssistantRequestRecord, PortfolioAnalysisRecord
from app.models.portfolio import HoldingShares
from app.models.portfolio_analysis import PortfolioAnalysisResponse


def holdings_key(holdings: list[HoldingShares], profile: str = "") -> str:
    """A fingerprint of the holdings and what the review was asked to tailor to, so a
    review is reused only for the same portfolio and profile."""
    text = ";".join(f"{h.symbol}:{h.shares!r}" for h in sorted(holdings, key=lambda h: h.symbol))
    return hashlib.sha256(f"{text}|{profile}".encode()).hexdigest()


def _response(record: PortfolioAnalysisRecord) -> PortfolioAnalysisResponse:
    return PortfolioAnalysisResponse.model_validate({**record.result, "id": record.id})


async def save_portfolio_analysis(
    session: AsyncSession,
    user_id: str,
    key: str,
    response: PortfolioAnalysisResponse,
    requested_model: str,
    effort: str,
    portfolio: int = 1,
) -> PortfolioAnalysisResponse:
    """Store ``response``, written for the holdings ``key`` of ``user_id``'s
    ``portfolio``; return it with its ID.

    Raises:
        sqlalchemy.exc.SQLAlchemyError: If the database cannot be written.
    """
    saved = response.model_copy(update={"id": uuid4(), "cached": False})
    session.add(
        PortfolioAnalysisRecord(
            id=str(saved.id),
            user_id=user_id,
            created_at=saved.generated_at,
            portfolio=portfolio,
            period=saved.period,
            holdings_key=key,
            requested_model=requested_model,
            effort=effort,
            model=saved.model,
            headline=saved.report.headline,
            result=saved.model_dump(mode="json", exclude={"id"}),
        )
    )
    await session.commit()
    return saved


async def find_recent_portfolio_analysis(
    session: AsyncSession,
    user_id: str,
    period: str,
    key: str,
    requested_model: str,
    effort: str,
    since: datetime,
    portfolio: int = 1,
) -> PortfolioAnalysisResponse | None:
    """The account's newest review since ``since`` of ``portfolio`` with the same
    holdings and options."""
    record = await session.scalar(
        select(PortfolioAnalysisRecord)
        .where(
            PortfolioAnalysisRecord.user_id == user_id,
            PortfolioAnalysisRecord.portfolio == portfolio,
            PortfolioAnalysisRecord.period == period,
            PortfolioAnalysisRecord.holdings_key == key,
            PortfolioAnalysisRecord.requested_model == requested_model,
            PortfolioAnalysisRecord.effort == effort,
            PortfolioAnalysisRecord.created_at >= since,
        )
        .order_by(PortfolioAnalysisRecord.created_at.desc())
        .limit(1)
    )
    return None if record is None else _response(record)


async def latest_portfolio_analysis(
    session: AsyncSession, user_id: str, portfolio: int = 1
) -> PortfolioAnalysisResponse | None:
    """The account's newest review of ``portfolio``, whatever it was written for."""
    record = await session.scalar(
        select(PortfolioAnalysisRecord)
        .where(
            PortfolioAnalysisRecord.user_id == user_id,
            PortfolioAnalysisRecord.portfolio == portfolio,
        )
        .order_by(PortfolioAnalysisRecord.created_at.desc())
        .limit(1)
    )
    return None if record is None else _response(record)


async def record_assistant_request(session: AsyncSession, user_id: str, model: str) -> None:
    """Count one coding assistant answer against the account's AI limits."""
    session.add(
        AssistantRequestRecord(
            id=str(uuid4()), user_id=user_id, created_at=datetime.now(timezone.utc), model=model
        )
    )
    await session.commit()
