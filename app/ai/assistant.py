"""The coding assistant for custom backtest strategies.

Each request carries the conversation so far; the code in the editor and how its last
run went are added to the newest question, so Claude always answers about the current
code rather than an earlier version from the conversation.
"""

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from app.ai.agent import Written, stream_structured, write_structured
from app.ai.prompts import ASSISTANT_SYSTEM_PROMPT
from app.models.assistant import AssistantReply, AssistantRequest, LastRun
from app.models.research import AnalysisDelta, ModelFallback

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic
    from anthropic.types.beta import BetaMessageParam

LANGUAGE_NAMES: dict[str, str] = {"python": "Python", "cpp": "C++"}


def _last_run(run: LastRun) -> str:
    """The last run's outcome, tagged, with only the parts it has."""
    if run.error_kind is None:
        parts = ['<last_run outcome="succeeded">']
    else:
        line = f' line="{run.line}"' if run.line is not None else ""
        parts = [f'<last_run outcome="failed" error="{run.error_kind}"{line}>']
        if run.message:
            parts.append(f"<message>\n{run.message}\n</message>")
        if run.traceback:
            parts.append(f"<traceback>\n{run.traceback}\n</traceback>")
    if run.output:
        parts.append(f"<printed>\n{run.output}\n</printed>")
    parts.append("</last_run>")
    return "\n".join(parts)


def assistant_messages(request: AssistantRequest) -> list[BetaMessageParam]:
    """The conversation for Claude: earlier turns, then the question with the code."""
    turns = list(request.history)
    # The API needs the conversation to open with a user turn.
    while turns and turns[0].role == "assistant":
        turns.pop(0)
    messages: list[BetaMessageParam] = [
        {"role": turn.role, "content": turn.content} for turn in turns
    ]
    about = [f'language="{LANGUAGE_NAMES[request.language]}"']
    if request.symbol:
        about.append(f'symbol="{request.symbol}"')
    if request.interval:
        about.append(f'interval="{request.interval}"')
    parts = [f"<editor {' '.join(about)}>\n{request.code}\n</editor>"]
    if request.last_run is not None:
        parts.append(_last_run(request.last_run))
    parts.append(request.message)
    messages.append({"role": "user", "content": "\n\n".join(parts)})
    return messages


async def write_assistant_reply(
    request: AssistantRequest, *, client: AsyncAnthropic
) -> tuple[AssistantReply, str]:
    """Have Claude answer ``request``; return the reply and the model that wrote it.

    Errors are as for :func:`app.ai.agent.write_analysis`.
    """
    return await write_structured(
        ASSISTANT_SYSTEM_PROMPT, assistant_messages(request), AssistantReply, client=client
    )


async def stream_assistant_reply(
    request: AssistantRequest, *, client: AsyncAnthropic
) -> AsyncIterator[AnalysisDelta | ModelFallback | Written[AssistantReply]]:
    """Stream :func:`write_assistant_reply`, as :func:`app.ai.agent.stream_structured` does."""
    async for item in stream_structured(
        ASSISTANT_SYSTEM_PROMPT, assistant_messages(request), AssistantReply, client=client
    ):
        yield item
