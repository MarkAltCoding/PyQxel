"""Factor models and the Fama-French factors they are built from."""

from typing import Literal

FactorModel = Literal["ff3", "carhart4", "ff5"]
"""Fama-French three-factor, Carhart four-factor (three plus momentum), Fama-French five-factor."""

FactorName = Literal["Mkt-RF", "SMB", "HML", "RMW", "CMA", "Mom"]
"""Factor returns as Ken French names them: market excess return, size, value,
profitability, investment and momentum."""

RISK_FREE: str = "RF"
"""Column holding the daily risk-free rate, the one-month T-bill compounded daily."""

MODEL_FACTORS: dict[FactorModel, list[FactorName]] = {
    "ff3": ["Mkt-RF", "SMB", "HML"],
    "carhart4": ["Mkt-RF", "SMB", "HML", "Mom"],
    "ff5": ["Mkt-RF", "SMB", "HML", "RMW", "CMA"],
}
"""The factors each model regresses on, in the order they are reported."""
