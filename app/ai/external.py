"""Prompts to take to Claude outside the app, and the answers brought back from it.

The app's own requests hold a system prompt, the messages, and a JSON schema that
structured outputs enforce. Outside the app, that becomes one Markdown document to paste
into a single message: the instructions, the request, and the schema with a request to
answer in JSON alone, since nothing then enforces it. A pasted answer is checked against
the same schema the app's requests use, so it is stored like any other.
"""

import json
import re
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from app.ai.agent import Report

if TYPE_CHECKING:
    from anthropic.types.beta import BetaMessageParam, BetaTextBlockParam

PASTED_MODEL: str = "claude (pasted)"
"""What a pasted answer's model is recorded as; the app never reuses such an answer in
place of writing a new one."""

MAX_RESPONSE_LENGTH: int = 200_000
"""Longest pasted answer accepted."""


class ExternalResponseError(ValueError):
    """Raised when a pasted answer is not the JSON the prompt asked for; the message
    says what is wrong, for the person who pasted it."""


def _text(content: Any) -> str:
    """The text of a message's content: a string, or text blocks joined."""
    if isinstance(content, str):
        return content
    return "\n\n".join(block["text"] for block in content if block.get("type") == "text")


def render_prompt(
    system: str | list[BetaTextBlockParam],
    messages: list[BetaMessageParam],
    schema: type[BaseModel],
) -> str:
    """The request the app would send Claude, as one Markdown document to paste.

    An earlier conversation, as the coding assistant has, is laid out turn by turn
    before the newest message.
    """
    instructions = _text(system)
    *earlier, latest = messages
    parts = [
        "Answer the request below as the assistant these instructions describe. Your "
        "answer will be pasted back into PyQxel, so follow the answer format exactly.",
        f"## Instructions\n\n{instructions}",
    ]
    if earlier:
        turns = "\n\n".join(
            f"### {'Me' if turn['role'] == 'user' else 'You'}\n\n{_text(turn['content'])}"
            for turn in earlier
        )
        parts.append(f"## Our conversation so far\n\n{turns}")
    parts.append(f"## Request\n\n{_text(latest['content'])}")
    parts.append(
        "## Answer format\n\nReply with a single JSON object that matches this JSON Schema, "
        "and nothing else: no Markdown fences, no text before or after it.\n\n"
        f"```json\n{json.dumps(schema.model_json_schema(), indent=2)}\n```"
    )
    return "\n\n".join(parts) + "\n"


def _json_object(text: str) -> Any:
    """The JSON object in ``text``, which may sit in a code fence or among other text."""
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text.strip()
    try:
        return json.loads(candidate)
    except ValueError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ExternalResponseError(
            "No JSON object found. Paste Claude's whole answer: the prompt asks it to "
            "reply with one JSON object."
        )
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ExternalResponseError(
            f"The answer is not valid JSON ({exc.msg} near character {exc.pos}). Paste it "
            "again in full, or ask Claude to reply with the JSON object alone."
        ) from exc


def parse_response(text: str, schema: type[Report]) -> Report:
    """Read a pasted answer as ``schema``.

    Raises:
        ExternalResponseError: If it holds no JSON object, or one that does not match.
    """
    if len(text) > MAX_RESPONSE_LENGTH:
        raise ExternalResponseError("The answer is too long to be one of these reports.")
    data = _json_object(text)
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'answer'}: {error['msg']}"
            for error in exc.errors()[:5]
        )
        raise ExternalResponseError(
            f"The answer does not match the expected format: {problems}. Ask Claude to "
            "follow the answer format in the prompt."
        ) from exc
