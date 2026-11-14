"""Claude's picks: the securities Claude would buy and hold for a period, chosen from the
screener's candidates."""

import csv
import io
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from app.ai.agent import Written, stream_structured, write_structured
from app.ai.prompts import PICKS_SYSTEM_PROMPT, PICKS_TASK
from app.core.text import listed
from app.models.picks import PickCandidate, PicksContext, PicksReport
from app.models.research import AnalysisDelta, ModelFallback
from app.models.screener import ScreenMetrics

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic
    from anthropic.types.beta import BetaMessageParam

METRIC_COLUMNS: tuple[str, ...] = tuple(ScreenMetrics.model_fields)
"""The screening metrics, in the order the candidates table lists them."""

COLUMNS: tuple[str, ...] = (
    "symbol", "name", "asset_type", "sector", "industry", "lenses", *METRIC_COLUMNS
)  # fmt: skip
"""The candidates table's columns."""


def _cell(value: object) -> str:
    """A table cell: empty when unknown, and a number to four significant figures, which
    is all a pick turns on and a third of the characters."""
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4g}"
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    return str(value)


def candidates_table(candidates: list[PickCandidate]) -> str:
    """The candidates as comma-separated values under a header row: a tenth of the tokens
    the same rows cost as JSON."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(COLUMNS)
    for candidate in candidates:
        writer.writerow(_cell(getattr(candidate, column)) for column in COLUMNS)
    return out.getvalue().strip()


def picks_messages(context: PicksContext) -> list[BetaMessageParam]:
    """The request: the task, the snapshot without its candidates, then the candidates as
    a table."""
    snapshot = context.model_dump_json(indent=2, exclude={"candidates"})
    return [
        {
            "role": "user",
            "content": f"{PICKS_TASK}\n\n<snapshot>\n{snapshot}\n</snapshot>\n\n"
            f"<candidates>\n{candidates_table(context.candidates)}\n</candidates>",
        }
    ]


def picks_research_task(context: PicksContext) -> str:
    """What the web is searched for before picks are made: the backdrop for the holding
    period, and news on the candidates most likely to be picked."""
    profile = context.profile
    kinds = listed([f"{kind}s" for kind in profile.asset_types]) or "stocks and ETFs"
    lines = [
        f"Today is {context.today:%B %-d, %Y}. The report will pick US-listed {kinds} to buy "
        f"now and hold for {context.horizon}"
        + (
            f", for an investor whose risk tolerance is {profile.risk_tolerance}"
            if profile.risk_tolerance
            else ""
        )
        + ".",
        "The tool already has each candidate's price, valuation multiples, margins, growth, "
        "momentum, volatility, and factor betas"
        + (
            f" as of {context.universe.prices_as_of:%B %-d, %Y}"
            if context.universe.prices_as_of
            else ""
        )
        + ", and a calendar of FOMC meetings and major earnings reports. It has no news on "
        "the candidates, no analyst estimates, and nothing on the economy.",
        "Search for the market and economic backdrop that matters over that holding period "
        "(interest rates and the Federal Reserve, inflation, earnings trends, and what is "
        "leading or lagging the market), and for recent news on the candidates below that "
        "look strongest for this profile: results and guidance, analyst actions, deals, and "
        "legal or regulatory trouble.",
    ]
    if profile.notes:
        lines.append(f"The person added: {profile.notes}")
    lines.append(f"<candidates>\n{candidates_table(context.candidates)}\n</candidates>")
    return "\n\n".join(lines)


async def write_picks(context: PicksContext, *, client: AsyncAnthropic) -> tuple[PicksReport, str]:
    """Have Claude make its picks from ``context``; return them and the model that did.

    Errors are as for :func:`app.ai.agent.write_analysis`.
    """
    return await write_structured(
        PICKS_SYSTEM_PROMPT, picks_messages(context), PicksReport, client=client
    )


async def stream_picks(
    context: PicksContext, *, client: AsyncAnthropic
) -> AsyncIterator[AnalysisDelta | ModelFallback | Written[PicksReport]]:
    """Stream :func:`write_picks`, as :func:`app.ai.agent.stream_structured` does."""
    async for item in stream_structured(
        PICKS_SYSTEM_PROMPT, picks_messages(context), PicksReport, client=client
    ):
        yield item
