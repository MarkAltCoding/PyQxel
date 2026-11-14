"""Schemas for what an account enters in the app, kept so it follows the account.

The app stores small pieces of text under names it chooses: a strategy draft, a screen's
filters, the answers given for a review, a display choice. None is a secret; provider keys
and sign-in keys are never sent here.
"""

from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field, StringConstraints

MAX_SAVED_KEYS: int = 300
"""Most named values one account may keep."""

MAX_SAVED_VALUE_LENGTH: int = 200_000
"""Longest value kept, in characters: room for a long strategy's source."""

MAX_SAVED_TOTAL_LENGTH: int = 2_000_000
"""Most characters an account's values may add up to."""


SavedValue = Annotated[str, StringConstraints(max_length=MAX_SAVED_VALUE_LENGTH)]

SECRET_NAME_PARTS: tuple[str, ...] = ("apikey", "providerkeys", "signin", "password", "secret")
"""Parts of a name the server refuses, whatever a client sends, so a browser's sign-in key
and provider keys can never be saved here by mistake."""


def _not_a_secret(key: str) -> str:
    lowered = key.lower()
    if any(part in lowered for part in SECRET_NAME_PARTS):
        raise ValueError(f"{key} names a key or sign-in, which is never saved to the account.")
    return key


SavedKey = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9_.:-]{1,120}$"), AfterValidator(_not_a_secret)
]
"""A value's name, as the app chose it."""


class SavedState(BaseModel):
    """Everything the account has saved from the app, by name."""

    account: str = Field(
        description="The account these belong to, so a browser can tell them from another "
        "account's left in it."
    )
    items: dict[str, str] = Field(description="Each saved value by its name.")


class SavedStateUpdate(BaseModel):
    """Values to save or, with null, to forget; names not given are left as they are."""

    items: dict[SavedKey, SavedValue | None] = Field(max_length=MAX_SAVED_KEYS)
