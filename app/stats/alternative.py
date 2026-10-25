"""Summaries of a company's lobbying, government contracts, patents and SEC filings."""

from collections import Counter, defaultdict
from datetime import datetime

from app.models.alternative import (
    AgencyTotal,
    AlternativeContext,
    AlternativeData,
    AlternativeRecords,
    LobbyingYear,
)

LISTED: int = 10
"""Disclosures, awards, patents and filings listed."""
TOP_AGENCIES: int = 5
"""Agencies listed by what they awarded."""
CONTEXT_CONTRACTS: int = 5
"""Largest awards given to Claude."""
CONTEXT_PATENTS: int = 8
"""Newest patent descriptions given to Claude."""
CONTEXT_8KS: int = 10
"""Recent 8-K dates given to Claude."""


def summarize_alternative(records: AlternativeRecords, as_of: datetime) -> AlternativeData:
    """Total lobbying by year and awards by agency, count patents and filings, and list
    the newest of each."""
    lobbying, contracts = records.lobbying or [], records.contracts or []
    filings = records.filings or []
    years: dict[int, list[float]] = defaultdict(list)
    for filing in lobbying:
        years[filing.year].append(filing.amount or 0.0)
    agencies: dict[str, list[float]] = defaultdict(list)
    for award in contracts:
        agencies[award.agency or "Unknown agency"].append(award.value)
    return AlternativeData(
        symbol=records.symbol,
        lobbying_years=[
            LobbyingYear(year=year, amount=max(0.0, sum(amounts)), filings=len(amounts))
            for year, amounts in sorted(years.items(), reverse=True)
        ],
        lobbying=lobbying[:LISTED],
        contracts_total=(
            None if records.contracts is None else sum(award.value for award in contracts)
        ),
        contract_agencies=sorted(
            (
                AgencyTotal(agency=agency, value=sum(values), awards=len(values))
                for agency, values in agencies.items()
            ),
            key=lambda total: total.value,
            reverse=True,
        )[:TOP_AGENCIES],
        contracts=sorted(contracts, key=lambda award: award.value, reverse=True)[:LISTED],
        patents_count=None if records.patents is None else len(records.patents),
        patents=(records.patents or [])[:LISTED],
        filing_counts=dict(Counter(filing.form for filing in filings).most_common()),
        filings=filings[:LISTED],
        as_of=as_of,
        notice=f"Left out: {'; '.join(records.missing)}." if records.missing else None,
    )


def alternative_context(data: AlternativeData, records: AlternativeRecords) -> AlternativeContext:
    """The parts of ``data`` given to Claude: totals, the largest awards, the newest
    patents' subjects, and when the company filed 8-Ks."""
    return AlternativeContext(
        lobbying_years=data.lobbying_years,
        contracts_total=data.contracts_total,
        contract_agencies=data.contract_agencies,
        largest_contracts=data.contracts[:CONTEXT_CONTRACTS],
        patents_count=data.patents_count,
        recent_patents=[patent.description for patent in data.patents[:CONTEXT_PATENTS]],
        filing_counts=data.filing_counts,
        recent_8k_dates=[
            filing.filed for filing in records.filings or [] if filing.form.startswith("8-K")
        ][:CONTEXT_8KS],
    )
