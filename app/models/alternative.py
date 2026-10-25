"""Schemas for a company's lobbying, government contracts, patents and SEC filings, from
Finnhub."""

from datetime import date, datetime

from pydantic import BaseModel, Field


class LobbyingFiling(BaseModel):
    """One quarterly lobbying disclosure filed with the Senate or House."""

    year: int
    period: str | None = Field(default=None, description="The quarter, e.g. Q3.")
    in_house: bool = Field(
        description="True when the company lobbied itself, false when it hired a firm."
    )
    amount: float | None = Field(
        default=None,
        description="Spent on lobbying in the period, in US dollars: the company's expenses "
        "when it lobbied itself, or the hired firm's income from it.",
    )
    url: str | None = None


class LobbyingYear(BaseModel):
    """Lobbying spending disclosed for one year."""

    year: int
    amount: float = Field(ge=0)
    filings: int = Field(ge=0)


class GovernmentAward(BaseModel):
    """A federal contract or grant awarded to the company or a subsidiary."""

    action_date: date
    recipient: str
    agency: str | None = None
    sub_agency: str | None = None
    value: float = Field(description="The award's total obligated value, in US dollars.")
    description: str | None = None
    performance_end: date | None = None
    url: str | None = None


class AgencyTotal(BaseModel):
    """What one federal agency awarded the company over the window."""

    agency: str
    value: float
    awards: int = Field(ge=1)


class Patent(BaseModel):
    """A US patent or patent application naming the company."""

    filing_date: date | None = None
    publication_date: date | None = None
    number: str | None = None
    description: str
    status: str | None = Field(default=None, description="Application or Patent.")
    kind: str | None = Field(default=None, description="e.g. Utility or Design.")


class SecFiling(BaseModel):
    """A filing with the SEC, as listed by Finnhub."""

    form: str
    filed: date
    url: str | None = None


class AlternativeRecords(BaseModel):
    """What Finnhub returned for each part; ``None`` for a part it could not supply."""

    symbol: str
    lobbying: list[LobbyingFiling] | None = None
    contracts: list[GovernmentAward] | None = None
    patents: list[Patent] | None = None
    filings: list[SecFiling] | None = None
    missing: list[str] = Field(
        default_factory=list, description="Why each part left out could not be supplied."
    )


class AlternativeData(BaseModel):
    """A company's lobbying, government contracts, patents and SEC filings."""

    symbol: str
    lobbying_years: list[LobbyingYear] = Field(
        description="Lobbying spending last year and this year, newest first."
    )
    lobbying: list[LobbyingFiling] = Field(description="The newest disclosures, newest first.")
    contracts_total: float | None = Field(
        default=None, description="Federal awards over the last year, in US dollars."
    )
    contract_agencies: list[AgencyTotal] = Field(
        description="Agencies that awarded the most over the last year."
    )
    contracts: list[GovernmentAward] = Field(description="The largest awards of the last year.")
    patents_count: int | None = Field(
        default=None, description="Patents and applications published in the last year."
    )
    patents: list[Patent] = Field(description="The newest patents and applications.")
    filing_counts: dict[str, int] = Field(
        default_factory=dict, description="SEC filings in the last 90 days, by form."
    )
    filings: list[SecFiling] = Field(description="The newest SEC filings, newest first.")
    as_of: datetime
    notice: str | None = Field(
        default=None, description="Says which parts Finnhub could not supply, if any."
    )


class AlternativeContext(BaseModel):
    """A company's lobbying, government contracts, patents and recent filings, as given to
    Claude."""

    lobbying_years: list[LobbyingYear]
    contracts_total: float | None = None
    contract_agencies: list[AgencyTotal]
    largest_contracts: list[GovernmentAward]
    patents_count: int | None = None
    recent_patents: list[str] = Field(description="Descriptions of the newest patents.")
    filing_counts: dict[str, int]
    recent_8k_dates: list[date] = Field(
        description="When the company filed 8-Ks, which report material events, lately."
    )
