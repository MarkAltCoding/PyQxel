"""Storage of AI-written analyses, and lookup of a recent one to reuse."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.tables import AnalysisRecord, as_utc
from app.models.research import (
    AnalysisList,
    AnalysisRequest,
    AnalysisResponse,
    AnalysisSummary,
)


def _response(record: AnalysisRecord) -> AnalysisResponse:
    """Rebuild a stored response, with its ID."""
    return AnalysisResponse.model_validate({**record.result, "id": record.id})


async def save_analysis(
    session: AsyncSession,
    user_id: str,
    request: AnalysisRequest,
    response: AnalysisResponse,
    requested_model: str,
    effort: str,
    context_version: int,
) -> AnalysisResponse:
    """Store ``response``, written for ``user_id``'s ``request``; return it with its ``id``.

    Raises:
        sqlalchemy.exc.SQLAlchemyError: If the database cannot be written.
    """
    saved = response.model_copy(update={"id": uuid4(), "cached": False})
    session.add(
        AnalysisRecord(
            id=str(saved.id),
            user_id=user_id,
            created_at=saved.generated_at,
            symbol=saved.symbol,
            kind=request.kind,
            period=request.period,
            include_filings=request.include_filings,
            requested_model=requested_model,
            effort=effort,
            context_version=context_version,
            model=saved.model,
            headline=saved.report.headline,
            result=saved.model_dump(mode="json", exclude={"id"}),
        )
    )
    await session.commit()
    return saved


async def find_recent_analysis(
    session: AsyncSession,
    user_id: str,
    symbol: str,
    request: AnalysisRequest,
    requested_model: str,
    effort: str,
    context_version: int,
    since: datetime,
) -> AnalysisResponse | None:
    """Return ``user_id``'s newest report written since ``since`` for the same request and
    settings.

    Only reports written from the same ``context_version`` of data qualify; other users'
    reports never do.
    """
    record = await session.scalar(
        select(AnalysisRecord)
        .where(
            AnalysisRecord.user_id == user_id,
            AnalysisRecord.symbol == symbol.upper(),
            AnalysisRecord.kind == request.kind,
            AnalysisRecord.period == request.period,
            AnalysisRecord.include_filings == request.include_filings,
            AnalysisRecord.requested_model == requested_model,
            AnalysisRecord.effort == effort,
            AnalysisRecord.context_version == context_version,
            AnalysisRecord.created_at >= since,
        )
        .order_by(AnalysisRecord.created_at.desc())
        .limit(1)
    )
    return None if record is None else _response(record)


async def get_analysis(
    session: AsyncSession, user_id: str, analysis_id: UUID
) -> AnalysisResponse | None:
    """Return ``user_id``'s stored analysis with ``analysis_id``, or ``None`` if they have none."""
    record = await session.get(AnalysisRecord, str(analysis_id))
    return None if record is None or record.user_id != user_id else _response(record)


async def list_analyses(
    session: AsyncSession,
    user_id: str,
    symbol: str | None = None,
    kind: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> AnalysisList:
    """Return ``user_id``'s stored analyses newest first, optionally one symbol's or kind's."""
    query = select(AnalysisRecord).where(AnalysisRecord.user_id == user_id)
    if symbol is not None:
        query = query.where(AnalysisRecord.symbol == symbol.upper())
    if kind is not None:
        query = query.where(AnalysisRecord.kind == kind)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    records = await session.scalars(
        query.order_by(AnalysisRecord.created_at.desc(), AnalysisRecord.id)
        .limit(limit)
        .offset(offset)
    )
    return AnalysisList(
        items=[
            AnalysisSummary(
                id=UUID(record.id),
                generated_at=as_utc(record.created_at),
                symbol=record.symbol,
                kind=record.kind,  # type: ignore[arg-type]
                period=record.period,  # type: ignore[arg-type]
                include_filings=record.include_filings,
                model=record.model,
                headline=record.headline,
            )
            for record in records
        ],
        total=total or 0,
        limit=limit,
        offset=offset,
    )
