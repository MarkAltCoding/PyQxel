"""Figures read straight from a filing's XBRL instance, for filings company facts lacks.

EDGAR's company facts API occasionally leaves out a periodic report that was filed with
XBRL: TSMC's 2025 20-F, filed in April 2026, was missing months later. When a company's
company facts end before its latest periodic filing, :func:`newer_filings` finds the
filings left out and :func:`instance_facts` reads each one's XBRL instance (the
``*_htm.xml`` file EDGAR extracts from inline XBRL) into the same shape as company facts,
so :func:`merge_facts` can add them before normalizing.

Only facts without dimensions are read, as in company facts: a dimension marks a
breakdown, such as one segment's revenue, rather than the company's total.
"""

import xml.etree.ElementTree as ElementTree
from datetime import date
from typing import Any

XBRLI = "{http://www.xbrl.org/2003/instance}"
"""Namespace of the XBRL instance elements: contexts, units and periods."""

TAXONOMIES: tuple[tuple[str, str], ...] = (
    ("fasb.org/us-gaap/", "us-gaap"),
    ("xbrl.ifrs.org/taxonomy/", "ifrs-full"),
    ("xbrl.sec.gov/dei/", "dei"),
)
"""Namespace URI fragments, and the taxonomy name company facts files each under."""

MAX_NEWER_FILINGS: int = 4
"""Most filings missing from company facts that are read for one company."""

Facts = dict[str, dict[str, dict[str, Any]]]
"""Company facts' ``facts`` member: taxonomy -> concept -> {"units": unit -> rows}."""


def _taxonomy(namespace: str) -> str | None:
    """The company facts taxonomy a concept namespace belongs to, if one read here."""
    for fragment, name in TAXONOMIES:
        if fragment in namespace and (name != "ifrs-full" or namespace.endswith("/ifrs-full")):
            return name
    return None


def _date(text: str | None) -> str | None:
    """``text`` as an ISO date, or ``None`` when it is not one."""
    try:
        return date.fromisoformat((text or "").strip()[:10]).isoformat()
    except ValueError:
        return None


def _periods(root: ElementTree.Element) -> dict[str, tuple[str | None, str]]:
    """Context ID -> (start, end) for contexts without dimensions; start is None at an instant."""
    periods: dict[str, tuple[str | None, str]] = {}
    for context in root.iter(f"{XBRLI}context"):
        entity = context.find(f"{XBRLI}entity")
        if entity is None or entity.find(f"{XBRLI}segment") is not None:
            continue
        if context.find(f"{XBRLI}scenario") is not None:
            continue
        period = context.find(f"{XBRLI}period")
        if period is None:
            continue
        instant = _date(period.findtext(f"{XBRLI}instant"))
        start = _date(period.findtext(f"{XBRLI}startDate"))
        end = _date(period.findtext(f"{XBRLI}endDate"))
        if instant is not None:
            periods[context.get("id", "")] = (None, instant)
        elif start is not None and end is not None:
            periods[context.get("id", "")] = (start, end)
    return periods


def _units(root: ElementTree.Element) -> dict[str, str]:
    """Unit ID -> company facts unit name (``TWD``, ``shares``) for single-measure units."""
    units: dict[str, str] = {}
    for unit in root.iter(f"{XBRLI}unit"):
        measures = [element.text or "" for element in unit.findall(f"{XBRLI}measure")]
        if len(measures) == 1:
            units[unit.get("id", "")] = measures[0].strip().rsplit(":", 1)[-1]
    return units


def instance_facts(content: bytes, form: str, filed: date, accession: str) -> Facts:
    """Read a filing's XBRL instance into company facts' shape (blocking).

    Args:
        content: The instance document.
        form: The filing's form type, e.g. ``"20-F"``.
        filed: The date it was filed.
        accession: Its accession number.

    Raises:
        ValueError: If ``content`` is not XML.
    """
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise ValueError(f"The XBRL instance of {accession} is not XML: {exc}") from exc
    periods, units = _periods(root), _units(root)
    facts: Facts = {}
    for element in root:
        if not element.tag.startswith("{"):
            continue
        namespace, concept = element.tag[1:].split("}", 1)
        taxonomy = _taxonomy(namespace)
        period = periods.get(element.get("contextRef", ""))
        unit = units.get(element.get("unitRef", ""))
        if taxonomy is None or period is None or unit is None:
            continue
        try:
            value = float((element.text or "").strip())
        except ValueError:
            continue
        start, end = period
        row: dict[str, Any] = {
            "end": end,
            "val": value,
            "accn": accession,
            "form": form,
            "filed": filed.isoformat(),
        }
        if start is not None:
            row["start"] = start
        units_of = facts.setdefault(taxonomy, {}).setdefault(concept, {"units": {}})["units"]
        units_of.setdefault(unit, []).append(row)
    return facts


def merge_facts(company: dict[str, Any], extra: Facts) -> None:
    """Add ``extra``'s rows to a company facts document, in place."""
    facts = company.setdefault("facts", {})
    for taxonomy, concepts in extra.items():
        for concept, entry in concepts.items():
            target = facts.setdefault(taxonomy, {}).setdefault(concept, {})
            units = target.setdefault("units", {})
            for unit, rows in entry["units"].items():
                units.setdefault(unit, []).extend(rows)


def latest_filed(
    company: dict[str, Any], forms: frozenset[str], concepts: list[tuple[str, str]]
) -> date | None:
    """The latest filing date of any of ``forms`` among ``concepts``' figures.

    Only the main statement items should be passed: company facts may hold a few cover
    page figures from a filing whose statements it left out.
    """
    latest: date | None = None
    facts = company.get("facts", {})
    for taxonomy, concept in concepts:
        units = facts.get(taxonomy, {}).get(concept, {}).get("units", {})
        for rows in units.values():
            for row in rows:
                filed = _date(row.get("filed"))
                if row.get("form") in forms and filed is not None:
                    day = date.fromisoformat(filed)
                    latest = day if latest is None or day > latest else latest
    return latest


def newer_filings(
    submissions: dict[str, Any], forms: frozenset[str], after: date | None
) -> list[dict[str, Any]]:
    """Periodic filings with XBRL filed after ``after``, newest first.

    Each is a row of the submissions index, with ``filed`` parsed. At most
    :data:`MAX_NEWER_FILINGS` are returned.
    """
    recent: dict[str, list[Any]] = submissions.get("filings", {}).get("recent", {})
    rows = [
        {key: values[i] for key, values in recent.items() if i < len(values)}
        for i in range(len(recent.get("form", [])))
    ]
    newer: list[dict[str, Any]] = []
    for row in rows:
        filed = _date(row.get("filingDate"))
        if row.get("form") not in forms or not row.get("isXBRL") or filed is None:
            continue
        day = date.fromisoformat(filed)
        if (after is None or day > after) and row.get("accessionNumber"):
            newer.append(row | {"filed": day})
    newer.sort(key=lambda row: row["filed"], reverse=True)
    return newer[:MAX_NEWER_FILINGS]


def instance_name(index: dict[str, Any]) -> str | None:
    """The XBRL instance's file name in a filing's ``index.json``, if it has one."""
    names = [item.get("name", "") for item in index.get("directory", {}).get("item", [])]
    return next((name for name in names if name.endswith("_htm.xml")), None)
