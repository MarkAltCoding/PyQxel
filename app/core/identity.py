"""Telling people apart safely: the email address an account is known by, and the
network address a request came from.

Both are used to match and to limit. An address that two different mailboxes could
share, or a client address the sender can write, would let one person act as, or be
counted as, another.
"""

from starlette.requests import HTTPConnection


def normalize_email(email: str) -> str:
    """An address as accounts store it: trimmed, and in lower case.

    A few non-ASCII letters turn into plain ASCII letters in lower case, such as the
    Kelvin sign (U+212A), which becomes "k". Lowering those would make an address that
    is a different mailbox, which a sign-in provider could verify, read the same as
    someone else's account, so they are kept as they are. Every other letter is lowered
    as before, so stored addresses still match.
    """
    return "".join(
        letter.lower() if letter.isascii() or not letter.lower().isascii() else letter
        for letter in email.strip()
    )


def client_address(connection: HTTPConnection) -> str:
    """The address the request came from, for limiting attempts per client.

    Behind Cloud Run, the client's own address is the last entry of
    ``X-Forwarded-For``: Google's front end appends it to whatever the client sent, and
    the entries before it are the client's to write. Uvicorn's ``--proxy-headers`` takes
    the first entry instead, so it is not used here. Without the header, as in
    development, the connection's own address is used.
    """
    forwarded = [
        entry.strip()
        for value in connection.headers.getlist("x-forwarded-for")
        for entry in value.split(",")
        if entry.strip()
    ]
    if forwarded:
        return forwarded[-1]
    return connection.client.host if connection.client else "unknown"
