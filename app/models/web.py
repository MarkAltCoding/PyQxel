"""Schemas for what Claude finds on the web for a report, each fact with its sources."""

from datetime import datetime

from pydantic import BaseModel, Field

MAX_FINDINGS: int = 40
"""Most findings one search for a report keeps."""

MAX_FINDING_LENGTH: int = 600
"""Longest statement kept as one finding, in characters."""


class WebSource(BaseModel):
    """A web page a finding came from, as the search returned it."""

    url: str = Field(description="The page's address.")
    title: str | None = Field(default=None, description="The page's title.")
    cited_text: str | None = Field(
        default=None,
        description="The passage of the page the finding rests on, up to 150 characters.",
    )


class WebFinding(BaseModel):
    """One fact found on the web, and the pages that state it."""

    id: int = Field(ge=1, description="Its number, which a report cites as [id].")
    text: str = Field(description="The fact, as Claude stated it from the pages.")
    sources: list[WebSource] = Field(min_length=1, description="The pages it came from.")


class WebResearch(BaseModel):
    """What a web search made for one report found. Only statements the search tied to a
    page are kept, so every finding has a source."""

    searched_at: datetime
    queries: list[str] = Field(default_factory=list, description="What was searched for.")
    searches: int = Field(default=0, ge=0, description="Searches made, each billed by Anthropic.")
    findings: list[WebFinding] = Field(default_factory=list)


class WebSearchQuery(BaseModel):
    """One search Claude made while a report streams, sent as a ``search`` event."""

    query: str
