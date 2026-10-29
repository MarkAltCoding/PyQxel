"""Schemas for the coding assistant that helps write custom backtest strategies.

The conversation lives in the browser: each request carries the turns so far, the code
in the editor and how its last run went, and nothing of it is stored on the server.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.models.backtest import MAX_CODE_LENGTH, CodeLanguage

MAX_MESSAGE_LENGTH: int = 4_000
"""Longest question the user may send."""

MAX_TURNS: int = 20
"""Most earlier turns a request may carry; the browser drops the oldest beyond it."""

MAX_TURN_LENGTH: int = MAX_CODE_LENGTH + 8_000
"""Longest earlier turn, which may hold a full revision of the code."""

MAX_OUTPUT_LENGTH: int = 4_000
"""Most characters of a run's printed output or traceback sent along."""

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class AssistantTurn(BaseModel):
    """An earlier message in the conversation."""

    role: Literal["user", "assistant"]
    content: Annotated[Text, StringConstraints(max_length=MAX_TURN_LENGTH)]


class LastRun(BaseModel):
    """How the code in the editor fared when last run, for the assistant to diagnose."""

    error_kind: str | None = Field(
        default=None,
        max_length=32,
        description="``syntax``, ``compile``, ``runtime``, ``result`` or ``timeout``; null "
        "when the run succeeded.",
    )
    message: str | None = Field(default=None, max_length=MAX_OUTPUT_LENGTH)
    line: int | None = Field(default=None, ge=1)
    traceback: str | None = Field(default=None, max_length=MAX_OUTPUT_LENGTH)
    output: str | None = Field(
        default=None, max_length=MAX_OUTPUT_LENGTH, description="What the code printed."
    )


class AssistantRequest(BaseModel):
    """A question about a custom strategy, with the code it is about."""

    language: CodeLanguage = "python"
    code: str = Field(default="", max_length=MAX_CODE_LENGTH, description="The editor's code.")
    message: Annotated[Text, StringConstraints(max_length=MAX_MESSAGE_LENGTH)]
    history: list[AssistantTurn] = Field(default_factory=list, max_length=MAX_TURNS)
    symbol: str | None = Field(
        default=None, max_length=16, description="The symbol the strategy is backtested on."
    )
    interval: str | None = Field(default=None, max_length=8, description="Bar size, e.g. 1d.")
    last_run: LastRun | None = None


class AssistantReply(BaseModel):
    """The assistant's answer: an explanation, and the revised code when it changes it."""

    explanation: str = Field(
        description="The answer: what was wrong or what changed and why, in plain prose. "
        "Short code snippets may appear in backticks."
    )
    code: str | None = Field(
        description="The complete revised strategy source, ready to replace the editor's "
        "code; null when the answer needs no change to the code."
    )


class AssistantResponse(BaseModel):
    """The assistant's reply and the model that wrote it."""

    reply: AssistantReply
    model: str
