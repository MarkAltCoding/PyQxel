"""Claude-written reviews of a saved portfolio, grounded in its risk statistics."""

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from app.ai.agent import Written, stream_structured, write_structured
from app.ai.prompts import PORTFOLIO_SYSTEM_PROMPT, PORTFOLIO_TASK
from app.models.portfolio_analysis import PortfolioContext, PortfolioReview
from app.models.research import AnalysisDelta, ModelFallback

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic
    from anthropic.types.beta import BetaMessageParam


def _messages(context: PortfolioContext) -> list[BetaMessageParam]:
    snapshot = context.model_dump_json(indent=2)
    return [{"role": "user", "content": f"{PORTFOLIO_TASK}\n\n<snapshot>\n{snapshot}\n</snapshot>"}]


async def write_portfolio_review(
    context: PortfolioContext, *, client: AsyncAnthropic
) -> tuple[PortfolioReview, str]:
    """Have Claude review the portfolio in ``context``; return the review and its model.

    Errors are as for :func:`app.ai.agent.write_analysis`.
    """
    return await write_structured(
        PORTFOLIO_SYSTEM_PROMPT, _messages(context), PortfolioReview, client=client
    )


async def stream_portfolio_review(
    context: PortfolioContext, *, client: AsyncAnthropic
) -> AsyncIterator[AnalysisDelta | ModelFallback | Written[PortfolioReview]]:
    """Stream :func:`write_portfolio_review`, as :func:`app.ai.agent.stream_structured` does."""
    async for item in stream_structured(
        PORTFOLIO_SYSTEM_PROMPT, _messages(context), PortfolioReview, client=client
    ):
        yield item
