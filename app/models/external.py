"""Schemas for taking a Claude request outside the app, and bringing its answer back.

An export holds the prompt to paste into Claude and the data it was built from; an
import sends that data back with the answer, so the stored report keeps the data it
was written from.
"""

from typing import Literal

from pydantic import BaseModel, Field

from app.ai.external import MAX_RESPONSE_LENGTH
from app.models.picks import PicksContext, PicksRequest
from app.models.portfolio_analysis import PortfolioAnalysisRequest, PortfolioContext
from app.models.research import AnalysisContext, AnalysisRequest


class PromptExport(BaseModel):
    """The request the app would send Claude, as one document to paste outside the app."""

    prompt: str = Field(description="Markdown to paste into one message to Claude.")
    filename: str = Field(description="A name to save the prompt under.")


class AnalysisPromptExport(PromptExport):
    """A stock analysis prompt and the data it holds; send ``context`` back with the answer."""

    context: AnalysisContext


class PortfolioPromptExport(PromptExport):
    """A portfolio review prompt and the data it holds; send ``context`` back with the answer."""

    context: PortfolioContext


class PicksPromptExport(PromptExport):
    """A Claude's Picks prompt and the data it holds; send ``context`` back with the answer."""

    context: PicksContext


class OutsideSettings(BaseModel):
    """The effort a pasted analysis was written at, as the person noted it, so a saved
    report, and its performance, say exactly what wrote it: Claude cannot tell its own
    effort, which is a setting of the request, not part of what it reads, while it names
    its model in the answer (``written_by``)."""

    effort_used: Literal["low", "medium", "high", "xhigh", "max"] | None = Field(
        default=None,
        description="The effort used outside the app; null to take an ``Effort: high`` line "
        "at the end of the pasted answer, or to record none.",
    )


class AnalysisImport(OutsideSettings):
    """An answer Claude wrote outside the app to an exported stock analysis prompt."""

    request: AnalysisRequest = Field(description="The options the prompt was exported with.")
    context: AnalysisContext = Field(description="The context the export returned.")
    response: str = Field(min_length=1, max_length=MAX_RESPONSE_LENGTH)


class PortfolioReviewImport(OutsideSettings):
    """An answer Claude wrote outside the app to an exported portfolio review prompt."""

    request: PortfolioAnalysisRequest = Field(
        default_factory=PortfolioAnalysisRequest,
        description="The options the prompt was exported with.",
    )
    context: PortfolioContext = Field(description="The context the export returned.")
    response: str = Field(min_length=1, max_length=MAX_RESPONSE_LENGTH)


class PicksImport(OutsideSettings):
    """An answer Claude wrote outside the app to an exported Claude's Picks prompt."""

    request: PicksRequest = Field(
        default_factory=PicksRequest, description="The options the prompt was exported with."
    )
    context: PicksContext = Field(description="The context the export returned.")
    response: str = Field(min_length=1, max_length=MAX_RESPONSE_LENGTH)


class AssistantImport(BaseModel):
    """An answer Claude wrote outside the app to an exported coding assistant prompt."""

    response: str = Field(min_length=1, max_length=MAX_RESPONSE_LENGTH)
