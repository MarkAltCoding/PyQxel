"""Tests for valuation multiples, margins and returns."""

import pytest

from app.stats.valuation import value_company
from tests.financials import balance, flow, sample_financials


def test_multiples_margins_and_returns() -> None:
    """Every ratio follows from the market cap and the TTM and balance sheet figures."""
    valuation = value_company(sample_financials(), price=100.0, market_cap=1_000.0)

    assert valuation is not None
    assert (valuation.market_cap, valuation.market_cap_source) == (1_000.0, "provider")
    assert valuation.enterprise_value == 1_050.0  # 1,000 + 100 debt - 50 cash
    assert valuation.pe_ratio == 10.0
    assert valuation.price_to_sales == 2.5
    assert valuation.ev_to_ebitda == 7.0
    assert valuation.fcf_yield == 0.1
    assert (valuation.gross_margin, valuation.operating_margin) == (0.5, 0.3)
    assert (valuation.net_margin, valuation.fcf_margin) == (0.25, 0.25)
    assert valuation.return_on_equity == 0.4  # 100 over average equity of 250
    assert valuation.notes == []


def test_market_cap_is_estimated_from_shares_without_a_provider_value() -> None:
    """Price times shares stands in for a missing market cap, with a caveat."""
    valuation = value_company(sample_financials(), price=50.0, market_cap=None)

    assert valuation is not None
    assert (valuation.market_cap, valuation.market_cap_source) == (500.0, "shares_outstanding")
    assert "share classes" in valuation.notes[0]


def test_no_market_value_means_no_valuation() -> None:
    """Without a market cap, or a price and share count, nothing can be valued."""
    assert value_company(sample_financials(), price=None, market_cap=None) is None
    assert value_company(sample_financials(shares_outstanding=None), 50.0, None) is None


def test_losses_leave_earnings_multiples_out() -> None:
    """A P/E and EV/EBITDA on negative earnings are not meaningful."""
    financials = sample_financials(
        net_income=flow("NetIncomeLoss", -20.0), ebitda=flow("EBITDA", -5.0)
    )

    valuation = value_company(financials, price=None, market_cap=1_000.0)

    assert valuation is not None
    assert (valuation.pe_ratio, valuation.ev_to_ebitda) == (None, None)
    assert valuation.net_margin == -0.05
    assert any("P/E" in note for note in valuation.notes)
    assert any("EBITDA is not positive" in note for note in valuation.notes)


def test_unknown_debt_leaves_enterprise_value_out() -> None:
    """A bank without recognized debt or EBITDA gets no enterprise value or EV/EBITDA."""
    financials = sample_financials(
        total_debt=None, ebitda=None, operating_income=None, free_cash_flow=None
    )

    valuation = value_company(financials, price=None, market_cap=1_000.0)

    assert valuation is not None
    assert (valuation.enterprise_value, valuation.ev_to_ebitda) == (None, None)
    assert (valuation.fcf_yield, valuation.operating_margin) == (None, None)
    assert valuation.pe_ratio == 10.0
    assert len(valuation.notes) == 3


def test_return_on_equity_uses_latest_equity_without_a_year_earlier() -> None:
    """Without last year's balance sheet, ROE divides by the latest equity alone."""
    financials = sample_financials(stockholders_equity=balance("StockholdersEquity", 500.0))

    valuation = value_company(financials, price=None, market_cap=1_000.0)

    assert valuation is not None and valuation.return_on_equity == pytest.approx(0.2)


def test_negative_equity_gives_no_return_on_equity() -> None:
    """Buybacks can push equity below zero, which makes ROE meaningless."""
    financials = sample_financials(
        stockholders_equity=balance("StockholdersEquity", -10.0, year_ago=-30.0)
    )

    valuation = value_company(financials, price=None, market_cap=1_000.0)

    assert valuation is not None and valuation.return_on_equity is None
    assert any("equity is not positive" in note for note in valuation.notes)
