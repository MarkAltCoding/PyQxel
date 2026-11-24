"""Storage of AI-written portfolio reviews, and of the coding assistant's answer count."""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.tables import AssistantRequestRecord, PortfolioAnalysisRecord, stated_effort
from app.models.portfolio import HoldingShares
from app.models.portfolio_analysis import (
    PortfolioAnalysisResponse,
    PortfolioReviewList,
    PortfolioReviewSummary,
)


def holdings_key(holdings: list[HoldingShares], profile: str = "") -> str:
    """A fingerprint of the holdings and what the review was asked to tailor to, so a
    review is reused only for the same portfolio and profile."""
    text = ";".join(f"{h.symbol}:{h.shares!r}" for h in sorted(holdings, key=lambda h: h.symbol))
    return hashlib.sha256(f"{text}|{profile}".encode()).hexdigest()


@dataclass(frozen=True)
class ReviewGroup:
    """Which portfolios a review of several covers, and how it reviewed them."""

    mode: str
    """``combined`` or ``separate``."""
    members: list[int]

    @property
    def key(self) -> str:
        """The members as stored: ``,1,3,``, so one can be matched as ``,3,``."""
        return "," + ",".join(str(number) for number in sorted(self.members)) + ","


def _response(record: PortfolioAnalysisRecord) -> PortfolioAnalysisResponse:
    return PortfolioAnalysisResponse.model_validate(
        {**record.result, "id": record.id, "effort": stated_effort(record.effort)}
    )


async def save_portfolio_analysis(
    session: AsyncSession,
    user_id: str,
    key: str,
    response: PortfolioAnalysisResponse,
    requested_model: str,
    effort: str,
    portfolio: int = 1,
    group: ReviewGroup | None = None,
) -> PortfolioAnalysisResponse:
    """Store ``response``, written for the holdings ``key`` of ``user_id``'s
    ``portfolio``, or of the portfolios ``group`` names; return it with its ID.

    Raises:
        sqlalchemy.exc.SQLAlchemyError: If the database cannot be written.
    """
    saved = response.model_copy(
        update={"id": uuid4(), "cached": False, "effort": stated_effort(effort)}
    )
    session.add(
        PortfolioAnalysisRecord(
            id=str(saved.id),
            user_id=user_id,
            created_at=saved.generated_at,
            portfolio=portfolio,
            group_mode=None if group is None else group.mode,
            group_members=None if group is None else group.key,
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
    session: AsyncSession, user_id: str, portfolio: int = 1, group: ReviewGroup | None = None
) -> PortfolioAnalysisResponse | None:
    """The account's newest review of ``portfolio``, whatever it was written for, or of
    the portfolios ``group`` names, reviewed as it says. A portfolio's newest review may be
    one of several portfolios that gave each its own plan (``separate``)."""
    if group is not None:
        which = and_(
            PortfolioAnalysisRecord.group_mode == group.mode,
            PortfolioAnalysisRecord.group_members == group.key,
        )
    else:
        which = or_(
            PortfolioAnalysisRecord.portfolio == portfolio,
            and_(
                PortfolioAnalysisRecord.group_mode == "separate",
                PortfolioAnalysisRecord.group_members.contains(f",{portfolio},"),
            ),
        )
    record = await session.scalar(
        select(PortfolioAnalysisRecord)
        .where(PortfolioAnalysisRecord.user_id == user_id, which)
        .order_by(PortfolioAnalysisRecord.created_at.desc())
        .limit(1)
    )
    return None if record is None else _response(record)


def _members(record: PortfolioAnalysisRecord) -> list[int]:
    if record.group_members:
        return [int(n) for n in record.group_members.strip(",").split(",") if n]
    return [record.portfolio]


async def list_portfolio_analyses(
    session: AsyncSession, user_id: str, limit: int = 30
) -> PortfolioReviewList:
    """The account's stored reviews, newest first, each with what it was asked for."""
    records = await session.scalars(
        select(PortfolioAnalysisRecord)
        .where(PortfolioAnalysisRecord.user_id == user_id)
        .order_by(PortfolioAnalysisRecord.created_at.desc(), PortfolioAnalysisRecord.id)
        .limit(limit)
    )
    items = []
    for record in records:
        profile = (record.result.get("context") or {}).get("profile") or {}
        items.append(
            PortfolioReviewSummary(
                id=UUID(record.id),
                generated_at=(
                    record.created_at
                    if record.created_at.tzinfo
                    else record.created_at.replace(tzinfo=timezone.utc)
                ),
                portfolios=_members(record),
                group_mode=record.group_mode,  # type: ignore[arg-type]
                model=record.model,
                effort=stated_effort(record.effort),
                headline=record.headline,
                risk_tolerance=profile.get("risk_tolerance"),
                horizon_years=profile.get("horizon_years"),
                notes=profile.get("notes"),
            )
        )
    return PortfolioReviewList(items=items)


async def get_portfolio_analysis(
    session: AsyncSession, user_id: str, analysis_id: UUID
) -> PortfolioAnalysisResponse | None:
    """``user_id``'s stored review with ``analysis_id``, or ``None`` if they have none."""
    record = await session.get(PortfolioAnalysisRecord, str(analysis_id))
    return None if record is None or record.user_id != user_id else _response(record)


async def delete_portfolio_analysis(session: AsyncSession, user_id: str, analysis_id: UUID) -> bool:
    """Delete ``user_id``'s stored review with ``analysis_id``; return whether it existed."""
    record = await session.get(PortfolioAnalysisRecord, str(analysis_id))
    if record is None or record.user_id != user_id:
        return False
    await session.delete(record)
    await session.commit()
    return True


async def record_assistant_request(session: AsyncSession, user_id: str, model: str) -> None:
    """Count one coding assistant answer against the account's AI limits."""
    session.add(
        AssistantRequestRecord(
            id=str(uuid4()), user_id=user_id, created_at=datetime.now(timezone.utc), model=model
        )
    )
    await session.commit()
