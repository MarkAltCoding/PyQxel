"""The economy's backdrop as Claude is given it: the yield curve's shape and how it moved,
and the latest inflation and jobs figures with their recent direction."""

from datetime import date, timedelta

from app.models.markets import EconomyContext, YieldPoint

CURVE_TENORS: tuple[str, ...] = ("1 Mo", "3 Mo", "6 Mo", "1 Yr", "2 Yr", "5 Yr", "10 Yr", "30 Yr")
"""Maturities given to Claude: enough to show the curve's shape without every point."""


def _on_or_before(curve: dict[str, dict[str, float]], day: date) -> dict[str, float] | None:
    """The yields of the last date on or before ``day``."""
    dates = [d for d in curve if d <= day.isoformat()]
    return curve[max(dates)] if dates else None


def _month(key: str, back: int) -> str:
    year, month = (int(part) for part in key.split("-"))
    month -= back
    while month <= 0:
        month += 12
        year -= 1
    return f"{year:04d}-{month:02d}"


def _change(series: dict[str, float], latest: str, back: int) -> float | None:
    before = series.get(_month(latest, back))
    now = series.get(latest)
    if before is None or now is None or before == 0:
        return None
    return now / before - 1


def _yearly(series: dict[str, float], latest: str) -> float | None:
    """The change over twelve months to ``latest``; when that month is missing, as BLS
    published no October 2025 figures during that fall's government shutdown, the change
    from eleven or thirteen months before, at a twelve-month rate."""
    for back in (12, 11, 13):
        change = _change(series, latest, back)
        if change is not None:
            return change if back == 12 else (1 + change) ** (12 / back) - 1
    return None


def _year_before(series: dict[str, float], latest: str) -> float | None:
    """The value twelve months before ``latest``, or the nearest month to it."""
    for back in (12, 11, 13):
        value = series.get(_month(latest, back))
        if value is not None:
            return value
    return None


BLS_NOTICE: str = (
    "BLS.gov cannot vouch for the data or analyses derived from these data after the data "
    "have been retrieved from BLS.gov."
)
"""The notice the Bureau of Labor Statistics asks to accompany analyses of its data."""


def economy_context(
    curve: dict[str, dict[str, float]] | None,
    figures: dict[str, dict[str, float]] | None,
    retrieved: date | None = None,
) -> EconomyContext | None:
    """The backdrop from the Treasury's yield curve and the BLS's figures, either of which
    may be missing; ``None`` when both are. ``retrieved`` is the day they were read, for the
    citation."""
    if not curve and not figures:
        return None
    day = f"{retrieved:%B %-d, %Y}" if retrieved else "today"
    named = [
        *(["U.S. Department of the Treasury, daily par yield curve rates"] if curve else []),
        *([f"U.S. Bureau of Labor Statistics, retrieved {day}. {BLS_NOTICE}"] if figures else []),
    ]
    context = EconomyContext(sources="; ".join(named))
    if curve:
        as_of = date.fromisoformat(max(curve))
        latest = curve[as_of.isoformat()]
        month_ago = _on_or_before(curve, as_of - timedelta(days=30)) or {}
        year_ago = _on_or_before(curve, as_of - timedelta(days=365)) or {}
        context.yields_as_of = as_of
        context.yields = [
            YieldPoint(
                tenor=tenor,
                latest=latest[tenor],
                month_ago=month_ago.get(tenor),
                year_ago=year_ago.get(tenor),
            )
            for tenor in CURVE_TENORS
            if tenor in latest
        ]
        if "10 Yr" in latest and "2 Yr" in latest:
            context.spread_10y_2y = round(latest["10 Yr"] - latest["2 Yr"], 2)
        if "10 Yr" in latest and "3 Mo" in latest:
            context.spread_10y_3m = round(latest["10 Yr"] - latest["3 Mo"], 2)
    if figures:
        cpi, core = figures.get("cpi", {}), figures.get("core_cpi", {})
        if cpi:
            month = max(cpi)
            context.inflation_month = month
            context.cpi_yoy = _yearly(cpi, month)
            context.core_cpi_yoy = _yearly(core, month) if month in core else None
            three = _change(core, month, 3) if month in core else None
            context.core_cpi_3m_annualized = None if three is None else (1 + three) ** 4 - 1
        jobless = figures.get("unemployment", {})
        if jobless:
            month = max(jobless)
            context.unemployment = jobless[month] / 100
            before = _year_before(jobless, month)
            context.unemployment_year_ago = None if before is None else before / 100
        payrolls = figures.get("payrolls", {})
        if payrolls:
            month = max(payrolls)
            context.jobs_month = month
            back1, back3 = payrolls.get(_month(month, 1)), payrolls.get(_month(month, 3))
            context.payrolls_change = None if back1 is None else payrolls[month] - back1
            context.payrolls_change_3m = (
                None if back3 is None else round((payrolls[month] - back3) / 3, 1)
            )
    return context
