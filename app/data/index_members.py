"""The companies in the S&P 500 and the Nasdaq-100, from index funds' SEC filings.

The indexes' owners license their member lists, but a fund that tracks an index reports
every stock it holds to the SEC each quarter (:mod:`app.data.fund_filings`), which is public
data with no key. IVV holds the S&P 500 and QQQM the Nasdaq-100; SPY and QQQ are unit
investment trusts, which file no holdings. The SEC publishes a quarter's holdings about 60
days after it ends, so a company that joined or left the index since is a few months late.

A filing names each holding but gives no ticker, so holdings are matched to the SEC's
listings by company name, written the way both spell it: without the state an EDGAR name
ends with ("APPLIED MATERIALS INC /DE"), punctuation, or legal endings. A company whose
name changed since its filing with the SEC (GE Aerospace, still General Electric there) is
left out; :func:`index_members` logs how many were.

Every listing of a matched company is kept at first, since the filing does not say which
of them the fund holds: a company's notes and preferred shares (GOOGM, METAM) are listed
under it as well as its common shares. :attr:`IndexMembers.classes` says how many classes
of its shares the fund holds (two of Alphabet, one of Berkshire), so the caller keeps that
many once it knows which trade most.
"""

import logging
import re
import time
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from app.data.fund_filings import fetch_fund_filing
from app.data.universe import Listing

logger = logging.getLogger(__name__)

Index = Literal["sp500", "nasdaq100"]

INDEX_FUNDS: dict[Index, str] = {"sp500": "IVV", "nasdaq100": "QQQM"}
"""The fund whose SEC filing lists each index's companies."""

INDEX_NAMES: dict[Index, str] = {"sp500": "S&P 500", "nasdaq100": "Nasdaq-100"}

ENDINGS: frozenset[str] = frozenset(
    {
        "inc", "incorporated", "corp", "corporation", "co", "cos", "company", "companies",
        "ltd", "limited", "plc", "public", "nv", "sa", "se", "ag", "the", "holding",
        "holdings", "group", "lp", "llc",
    }
)  # fmt: skip
"""Words that say what kind of company it is, not which, so they are left out."""


US_STATES: frozenset[str] = frozenset(
    {
        "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id", "il", "in",
        "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv",
        "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri", "sc", "sd", "tn",
        "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy",
    }
)  # fmt: skip
"""Two-letter state codes, which EDGAR may end a name with when its slashes are gone, as
in "US BANCORP DE"."""

RENAMED: dict[str, str] = {
    "geaerospace": "generalelectric",
    "franklinresources": "franklintempleton",
    "equityresidential": "vivmarkresidential",
    "paramountskydance": "skydance",
}
"""Companies a fund's filing names differently from EDGAR, by :func:`name_keys`' first key:
the filing's name, then EDGAR's. GE Aerospace is still General Electric there; Franklin
Resources became Franklin Templeton, Equity Residential became Vivmark Residential when
it merged with AvalonBay in August 2026, and Paramount Skydance became Skydance when it
took over Warner Bros. Discovery. :func:`index_members` logs the names it could not
match, so a new one can be added here."""


def name_keys(name: str) -> tuple[str, str]:
    """Two ways of writing a company's name that match however a filing or EDGAR spells
    it: its words run together (``exxonmobil`` for both "ExxonMobil" and "Exxon Mobil"),
    and its words in alphabetical order ("Charles Schwab" and "SCHWAB CHARLES").

    Initials written apart are joined ("HORTON D R" and "DR Horton" both give ``dr``), and
    a state code EDGAR ends a name with is dropped ("US BANCORP DE")."""
    plain = re.split(r"[/\\]", name)[0].lower()
    plain = re.sub(r"['’.]", "", plain)
    words = [word for word in re.split(r"[^a-z0-9]+", plain) if word and word not in ENDINGS]
    joined: list[str] = []
    initials = False  # Whether the last word kept is a run of single letters.
    for word in words:
        if len(word) == 1 and initials:
            joined[-1] += word  # "j b" becomes "jb".
            continue
        joined.append(word)
        initials = len(word) == 1
    if len(joined) > 2 and joined[-1] in US_STATES:
        joined.pop()
    return "".join(joined), " ".join(sorted(joined))


@dataclass(frozen=True)
class IndexMembers:
    """The listings of an index's companies, and how many share classes of each it holds."""

    listings: list[Listing]
    """Every listing of each company in the index, including its notes and preferreds."""
    classes: dict[int, int]
    """CIK -> how many classes of the company's shares the index holds."""


async def index_members(index: Index, listings: Iterable[Listing]) -> IndexMembers:
    """The ``listings`` of companies in ``index``, and how many share classes of each.

    Raises:
        DataFetchError: If the fund's filing cannot be read from the SEC.
    """
    listings = list(listings)
    by_key: dict[str, set[int]] = defaultdict(set)
    for listing in listings:
        for key in name_keys(listing.name):
            by_key[key].add(listing.cik)
    filing = await fetch_fund_filing(INDEX_FUNDS[index])
    classes: Counter[int] = Counter()
    missed: list[str] = []
    for holding in filing.stocks:
        joined, ordered = name_keys(holding.name)
        found = by_key.get(joined) or by_key.get(ordered) or by_key.get(RENAMED.get(joined, ""))
        if found:
            # Two companies written alike are both taken, which is rare; leaving out a
            # member would be worse than one company too many.
            for cik in found:
                classes[cik] += 1
        else:
            missed.append(holding.name)
    logger.info(
        "%s: matched %d of %d companies in %s's filing as of %s.%s",
        INDEX_NAMES[index],
        len(filing.stocks) - len(missed),
        len(filing.stocks),
        INDEX_FUNDS[index],
        filing.as_of,
        f" Not matched: {', '.join(missed)}." if missed else "",
    )
    return IndexMembers(
        listings=[listing for listing in listings if listing.cik in classes],
        classes=dict(classes),
    )


MEMBERS_TTL_SECONDS: float = 12 * 3600
"""How long an index's member list is reused by the screener: the fund files quarterly."""

_members_cache: dict[Index, tuple[float, IndexMembers]] = {}


async def cached_index_members(index: Index, listings: Iterable[Listing]) -> IndexMembers:
    """:func:`index_members`, reused for :data:`MEMBERS_TTL_SECONDS`, for screens that
    narrow to an index on every request.

    Raises:
        DataFetchError: If the fund's filing cannot be read from the SEC.
    """
    found = _members_cache.get(index)
    if found is not None and time.monotonic() - found[0] < MEMBERS_TTL_SECONDS:
        return found[1]
    members = await index_members(index, listings)
    if members.classes:  # Nothing matched, as with no listings stored: ask again next time.
        _members_cache[index] = (time.monotonic(), members)
    return members
