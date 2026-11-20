"""Schemas for what kind of account a saved portfolio is, such as a Roth IRA, how much
more may be put into it this year, and the yearly contribution limits of each kind."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

AccountType = Literal[
    "taxable",
    "traditional_ira",
    "roth_ira",
    "rollover_ira",
    "sep_ira",
    "simple_ira",
    "traditional_401k",
    "roth_401k",
    "403b",
    "457b",
    "solo_401k",
    "hsa",
    "529",
    "other",
]
"""What kind of account holds a portfolio: an ordinary taxable brokerage account, an
individual retirement account (traditional, Roth, rollover, SEP, or SIMPLE), an employer
plan (a traditional or Roth 401(k), a 403(b), a 457(b), or a solo 401(k)), a health
savings account, a 529 education savings plan, or something else."""

ACCOUNT_TYPE_NAMES: dict[str, str] = {
    "taxable": "Taxable brokerage account",
    "traditional_ira": "Traditional IRA",
    "roth_ira": "Roth IRA",
    "rollover_ira": "Rollover IRA",
    "sep_ira": "SEP IRA",
    "simple_ira": "SIMPLE IRA",
    "traditional_401k": "Traditional 401(k)",
    "roth_401k": "Roth 401(k)",
    "403b": "403(b)",
    "457b": "457(b)",
    "solo_401k": "Solo 401(k)",
    "hsa": "Health savings account (HSA)",
    "529": "529 education savings plan",
    "other": "Other account",
}
"""Each account type as the app names it."""

CAPPED_ACCOUNT_TYPES: frozenset[str] = frozenset(
    {"traditional_ira", "roth_ira", "rollover_ira", "sep_ira", "simple_ira",
     "traditional_401k", "roth_401k", "403b", "457b", "solo_401k", "hsa"}
)  # fmt: skip
"""Account types the IRS caps yearly contributions to, so how much more may go in this
year is worth knowing. A 529 plan has no yearly federal cap, only gift-tax rules."""

TAX_FREE_GROWTH: frozenset[str] = frozenset({"roth_ira", "roth_401k", "hsa", "529"})
"""Accounts whose growth is never taxed when withdrawn for their purpose."""

TAX_DEFERRED: frozenset[str] = frozenset(
    {"traditional_ira", "rollover_ira", "sep_ira", "simple_ira", "traditional_401k", "403b",
     "457b", "solo_401k"}
)  # fmt: skip
"""Accounts whose growth is taxed only when withdrawn, as ordinary income."""


def tax_treatment(account_type: str | None) -> str | None:
    """How the account's gains are taxed, in a few words; ``None`` when unknown."""
    if account_type is None or account_type == "other":
        return None
    if account_type == "taxable":
        return "taxed each year on dividends and on gains when sold"
    if account_type in TAX_FREE_GROWTH:
        return "grows tax-free; qualified withdrawals are not taxed"
    return "grows tax-deferred; withdrawals are taxed as ordinary income"


class AccountSettings(BaseModel):
    """What kind of account a saved portfolio is, and how much more may go into it."""

    account_type: AccountType | None = Field(
        default=None, description="What kind of account it is; null when not said."
    )
    contribution_room: float | None = Field(
        default=None,
        ge=0,
        le=1e7,
        allow_inf_nan=False,
        description="The most new money that may still be put into it this tax year, in "
        "dollars, as the person works it out: the year's limit, less what they have put in, "
        "and no more than they earned. Null when not said or not capped.",
    )


class ContributionLimit(BaseModel):
    """One kind of account's yearly contribution limit for one tax year."""

    account: str = Field(description="The kinds of account it applies to.")
    limit: float = Field(description="The yearly limit, in dollars.")
    catch_up_50: float | None = Field(
        default=None, description="What those aged 50 or over may add on top."
    )
    catch_up_60_63: float | None = Field(
        default=None,
        description="What those aged 60 to 63 may add instead of the age-50 catch-up.",
    )
    note: str | None = None


class LimitYear(BaseModel):
    """The contribution limits of one tax year, as the IRS published them."""

    year: int
    limits: list[ContributionLimit]
    roth_ira_phase_out_single: tuple[float, float] = Field(
        description="Modified adjusted gross income over which a single filer's Roth IRA "
        "contribution shrinks, and at which it reaches nothing."
    )
    roth_ira_phase_out_joint: tuple[float, float] = Field(
        description="The same for a married couple filing jointly."
    )
    source: str = Field(description="Where the figures come from.")
    source_url: str


class ContributionLimits(BaseModel):
    """The yearly contribution limits that apply now, with how current they are."""

    as_of: date = Field(description="The day the figures were last checked against the IRS.")
    current: LimitYear = Field(description="The limits of this tax year.")
    next: LimitYear | None = Field(
        default=None, description="Next year's limits, once the IRS has published them."
    )
    outdated: bool = Field(
        description="Whether this year is later than the newest year PyQxel has figures "
        "for, so the figures shown are an earlier year's and likely too low."
    )
    notes: list[str] = Field(
        description="How the limits work and how they usually change from year to year."
    )
