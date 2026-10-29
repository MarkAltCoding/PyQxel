"""Keeping secrets out of logs and error messages.

Financial Modeling Prep and Alpha Vantage take keys in the query string, and HTTP errors
quote the URL they failed on, so an error can carry a user's key. Every log record is
scrubbed as it is made, tracebacks included, and so is every error detail the API sends;
PyQxel and Anthropic keys are scrubbed wherever they appear, and query parameters that
hold keys, tokens or sign-in codes are scrubbed by name.
"""

import logging
import re
import traceback
from typing import Any

REDACTED: str = "[redacted]"

_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?i)\b((?:api_?key|apikey|token|access_token|refresh_token|client_secret|code|state)"
        r"(?:=|%3D))[^&\s'\"<>]+"
    ),
    re.compile(r"\bpq_[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{16,}"),
)

_installed = False


def redact(text: str) -> str:
    """``text`` with API keys, tokens and sign-in codes replaced by ``[redacted]``."""
    for pattern in _PATTERNS:
        text = pattern.sub(
            lambda match: (match.group(1) if match.groups() else "") + REDACTED, text
        )
    return text


def _redact_arg(value: Any) -> Any:
    """A log argument with secrets scrubbed: errors are logged as their text, which can
    quote a URL with a key in it."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, BaseException):
        return redact(str(value))
    return value


def install_log_redaction() -> None:
    """Scrub every log record from now on, from any logger and through any handler.

    The record factory, rather than a handler filter, does it, so records reach no
    handler unscrubbed: not Python's last-resort handler, not uvicorn's access log
    (whose formatter reads the arguments directly), and not tracebacks, which are
    rendered and scrubbed here. Installing it again does nothing.
    """
    global _installed
    if _installed:
        return
    make_record = logging.getLogRecordFactory()

    def scrubbed(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = make_record(*args, **kwargs)
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_redact_arg(arg) for arg in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: _redact_arg(arg) for key, arg in record.args.items()}
        if record.exc_info and record.exc_info[1] is not None:
            record.exc_text = redact("".join(traceback.format_exception(*record.exc_info)))
            record.exc_text = record.exc_text.rstrip("\n")
        return record

    logging.setLogRecordFactory(scrubbed)
    # HTTP clients log whole URLs, and database drivers every statement with its values,
    # emails included, below WARNING; none of it is worth keeping.
    for name in ("httpx", "httpx2", "httpcore", "aiosqlite", "asyncpg", "sqlalchemy.engine"):
        logging.getLogger(name).setLevel(logging.WARNING)
    _installed = True
