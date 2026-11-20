"""The economy's backdrop, from the Treasury's yield curve and the BLS's figures."""

from typing import Any

import pytest

from app.data.economy import parse_bls, parse_yield_curve
from app.data.fetcher import DataFetchError
from app.stats.economy import economy_context

CURVE = """Date,"1 Mo","3 Mo","2 Yr","10 Yr","30 Yr"
11/19/2026,4.20,4.34,4.76,5.04,5.40
10/17/2026,4.10,4.30,4.70,4.90,5.30
"""

PAST = """Date,"1 Mo","3 Mo","2 Yr","10 Yr","30 Yr"
11/19/2025,4.50,4.40,4.00,4.30,4.60
"""


def _months(values: dict[str, float]) -> list[dict[str, Any]]:
    return [
        {"year": key[:4], "period": f"M{key[5:]}", "value": str(value)}
        for key, value in values.items()
    ]


BLS: dict[str, Any] = {
    "status": "REQUEST_SUCCEEDED",
    "Results": {
        "series": [
            {"seriesID": "CUUR0000SA0", "data": _months({"2026-10": 309.0, "2025-10": 300.0})},
            {
                "seriesID": "CUUR0000SA0L1E",
                "data": _months({"2026-10": 315.0, "2026-07": 312.0, "2025-10": 306.0}),
            },
            {"seriesID": "LNS14000000", "data": _months({"2026-10": 4.1, "2025-10": 4.3})},
            {
                "seriesID": "CES0000000001",
                "data": _months({"2026-10": 159293, "2026-09": 159128, "2026-07": 158900}),
            },
        ]
    },
}


def test_the_curve_and_the_figures_are_read() -> None:
    curve = {**parse_yield_curve(PAST), **parse_yield_curve(CURVE)}

    context = economy_context(curve, parse_bls(BLS))

    assert context is not None
    assert str(context.yields_as_of) == "2026-11-19"
    ten = next(point for point in context.yields if point.tenor == "10 Yr")
    assert (ten.latest, ten.month_ago, ten.year_ago) == (5.04, 4.9, 4.3)
    assert context.spread_10y_2y == pytest.approx(0.28)
    assert context.cpi_yoy == pytest.approx(0.03)
    assert context.core_cpi_3m_annualized == pytest.approx((315 / 312) ** 4 - 1)
    assert context.unemployment == pytest.approx(0.041)
    assert context.unemployment_year_ago == pytest.approx(0.043)
    assert context.payrolls_change == 165
    assert context.payrolls_change_3m == pytest.approx(131.0)


def test_a_missing_source_leaves_its_part_out() -> None:
    assert economy_context(None, None) is None
    only_curve = economy_context(parse_yield_curve(CURVE), None)
    assert only_curve is not None and only_curve.cpi_yoy is None


def test_a_refused_bls_query_is_an_error() -> None:
    with pytest.raises(DataFetchError):
        parse_bls({"status": "REQUEST_NOT_PROCESSED", "message": ["Daily threshold reached"]})


def test_a_missing_year_ago_month_uses_the_nearest_at_a_yearly_rate() -> None:
    """BLS published no October 2025 figures (that fall's shutdown)."""
    figures = parse_bls(BLS)
    figures["cpi"] = {"2026-10": 309.0, "2025-11": 300.0}
    figures["unemployment"] = {"2026-10": 4.1, "2025-11": 4.4}

    context = economy_context(None, figures)

    assert context is not None
    assert context.cpi_yoy == pytest.approx((309 / 300) ** (12 / 11) - 1)
    assert context.unemployment_year_ago == pytest.approx(0.044)
