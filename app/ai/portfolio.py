"""Claude-written reviews of a saved portfolio, grounded in its risk statistics."""

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from app.ai.agent import Written, stream_structured, write_structured
from app.ai.prompts import PORTFOLIO_SYSTEM_PROMPT, PORTFOLIO_TASK
from app.core.text import listed
from app.models.portfolio_analysis import PortfolioContext, PortfolioReview
from app.models.research import AnalysisDelta, ModelFallback

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic
    from anthropic.types.beta import BetaMessageParam


def review_messages(context: PortfolioContext) -> list[BetaMessageParam]:
    snapshot = context.model_dump_json(indent=2)
    return [{"role": "user", "content": f"{PORTFOLIO_TASK}\n\n<snapshot>\n{snapshot}\n</snapshot>"}]


RESEARCHED_HOLDINGS: int = 12
"""Most holdings, largest first, the web search for a review is pointed at."""


def review_research_task(context: PortfolioContext) -> str:
    """What the web is searched for before a portfolio is reviewed: news on its largest
    holdings, and the backdrop for the person's horizon."""
    held = sorted((h for h in context.holdings if not h.cash), key=lambda h: -(h.weight or 0.0))[
        :RESEARCHED_HOLDINGS
    ]
    names = [
        f"{h.symbol}{f' ({h.name})' if h.name else ''}"
        + (f", {h.weight:.0%} of the portfolio" if h.weight is not None else "")
        for h in held
    ]
    horizon = context.profile.horizon_years
    lines = [
        f"Today is {context.portfolio.end:%B %-d, %Y}. The report will review a person's "
        "investment portfolio and recommend what to buy, sell, and hold"
        + (f", for money needed in about {horizon:g} years" if horizon else "")
        + ".",
        f"Its largest holdings are {listed(names)}.",
        "The tool already has each holding's prices, volatility, drawdowns, and "
        "correlations, and what each fund holds. It has no news, valuations, or forecasts.",
        "Search for recent news that bears on keeping, adding to, or selling the largest "
        "holdings (results and guidance, analyst actions, deals, legal or regulatory "
        "trouble, a fund's changes), and for the market and economic backdrop: interest "
        "rates and the Federal Reserve, inflation, and what is leading or lagging.",
    ]
    if context.profile.notes:
        lines.append(f"The person added: {context.profile.notes}")
    return "\n\n".join(lines)


async def write_portfolio_review(
    context: PortfolioContext, *, client: AsyncAnthropic
) -> tuple[PortfolioReview, str]:
    """Have Claude review the portfolio in ``context``; return the review and its model.

    Errors are as for :func:`app.ai.agent.write_analysis`.
    """
    return await write_structured(
        PORTFOLIO_SYSTEM_PROMPT, review_messages(context), PortfolioReview, client=client
    )


async def stream_portfolio_review(
    context: PortfolioContext, *, client: AsyncAnthropic
) -> AsyncIterator[AnalysisDelta | ModelFallback | Written[PortfolioReview]]:
    """Stream :func:`write_portfolio_review`, as :func:`app.ai.agent.stream_structured` does."""
    async for item in stream_structured(
        PORTFOLIO_SYSTEM_PROMPT, review_messages(context), PortfolioReview, client=client
    ):
        yield item
