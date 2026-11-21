"""Writing that code assembles, so it reads as the rest of PyQxel's text does."""

import re
from collections.abc import Sequence


def listed(items: Sequence[str], conjunction: str = "and") -> str:
    """``items`` as an English list, with the serial comma from three items on, as in
    "A, B, and C"; two items are joined by the conjunction alone."""
    if len(items) < 3:
        return f" {conjunction} ".join(items)
    return f"{', '.join(items[:-1])}, {conjunction} {items[-1]}"


SMALL_WORDS: frozenset[str] = frozenset(
    {"a", "an", "and", "at", "for", "in", "of", "on", "or", "the", "to"}
)
"""Words left in lower case inside a tidied name, as "Bank of America"."""

KEPT_UPPER: frozenset[str] = frozenset(
    {
        "ADR",
        "ADS",
        "AG",
        "AI",
        "AS",
        "ASA",
        "BV",
        "ESG",
        "ETF",
        "ETFS",
        "ETN",
        "ETP",
        "II",
        "III",
        "IV",
        "IX",
        "MSCI",
        "NA",
        "NV",
        "OYJ",
        "REIT",
        "SA",
        "SAB",
        "SE",
        "SPA",
        "UK",
        "US",
        "USA",
        "VI",
        "VII",
        "VIII",
        "XI",
        "XII",
        "IBM",
        "ING",
        "AXA",
        "UBS",
        "USD",
        "CEO",
        "ESG",
        "AMC",
        "AMD",
        "BYD",
        "NYSE",
        "EUR",
        "JPY",
        "TIPS",
        "ACWI",
        "EAFE",
    }
)
"""Words kept in capitals: acronyms, company forms abroad, and Roman numerals. A word
with no vowel (PLC, HSBC, SPDR) or with a digit or ampersand (3M, S&P) is kept too."""


SPELLED: dict[str, str] = {
    "ISHARES": "iShares",
    "IONQ": "IonQ",
    "JPMORGAN": "JPMorgan",
    "NVIDIA": "NVIDIA",
    "PAYPAL": "PayPal",
    "BLACKROCK": "BlackRock",
    "LTD": "Ltd",
    "INTL": "Intl",
    "HLDGS": "Hldgs",
    "HLDG": "Hldg",
    "GRP": "Grp",
    "MFG": "Mfg",
    "SVCS": "Svcs",
    "PPTYS": "Pptys",
}
"""Words written as the company writes them, or abbreviations with no vowel that are
not acronyms."""

SHORT_WORDS: frozenset[str] = frozenset(
    {
        "INC",
        "CO",
        "COM",
        "OIL",
        "GAS",
        "BIO",
        "ONE",
        "TWO",
        "NEW",
        "CAR",
        "AIR",
        "SKY",
        "BAY",
        "BIG",
        "TOP",
        "SUN",
        "FOX",
        "ART",
        "LAB",
        "NET",
        "WEB",
        "AGE",
        "ACE",
        "ALL",
        "BOX",
        "CAP",
        "DAY",
        "EYE",
        "FIT",
        "FUN",
        "HUB",
        "KEY",
        "MAX",
        "PAY",
        "PET",
        "RED",
        "SEA",
        "TEA",
        "TOY",
        "WAY",
        "ZOO",
        "ICE",
        "GOLD",
        "BAR",
        "BEE",
        "OAK",
        "DIG",
    }
)
"""Words of three letters or fewer that are words, not acronyms (AES, EOG, CSX)."""

STATE_TAG: re.Pattern[str] = re.compile(r"\s*/[A-Z]{2,3}/?\s*$")
"""The state of incorporation the SEC appends to some names, as in "BANK OF AMERICA CORP
/DE/"."""


def _tidy_part(part: str, first: bool) -> str:
    """One letters-only part of a name in capitals, in normal case."""
    if part in SPELLED:
        return SPELLED[part]
    if len(part) == 1:
        return part
    lower = part.lower()
    if lower in SMALL_WORDS:
        return lower.capitalize() if first else lower
    if (
        part in KEPT_UPPER
        or (len(part) <= 3 and part not in SHORT_WORDS)
        or not any(letter in "AEIOUY" for letter in part)
    ):
        return part
    if lower.startswith("mc") and len(lower) > 3:
        return "Mc" + lower[2:].capitalize()
    return lower.capitalize()


def tidy_name(name: str) -> str:
    """A company's or fund's name written in capitals, as the SEC and some providers give
    them ("MICROSOFT CORP"), in normal case ("Microsoft Corp"), keeping acronyms such as
    "S&P", "PLC", and "ETF". A name with any lower-case letter is left as it is."""
    if not name or any(character.islower() for character in name):
        return name
    name = STATE_TAG.sub("", name)
    words = []
    for index, word in enumerate(name.split(" ")):
        if any(character.isdigit() or character == "&" for character in word):
            words.append(word)
            continue
        words.append(
            re.sub(
                r"[A-Z']+",
                lambda match: _tidy_part(match.group(0), index == 0 and match.start() == 0),
                word,
            )
        )
    return " ".join(words)
