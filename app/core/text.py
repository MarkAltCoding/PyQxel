"""Writing that code assembles, so it reads as the rest of PyQxel's text does."""

from collections.abc import Sequence


def listed(items: Sequence[str], conjunction: str = "and") -> str:
    """``items`` as an English list, with the serial comma from three items on, as in
    "A, B, and C"; two items are joined by the conjunction alone."""
    if len(items) < 3:
        return f" {conjunction} ".join(items)
    return f"{', '.join(items[:-1])}, {conjunction} {items[-1]}"
