"""Version 1 API router that groups every v1 endpoint module."""

from fastapi import APIRouter

from app.api.v1.endpoints import (
    account,
    backtest,
    factors,
    fundamentals,
    portfolio,
    quotes,
    research,
    screener,
    stocks,
)

api_router = APIRouter()
api_router.include_router(account.router, prefix="/me", tags=["account"])
api_router.include_router(stocks.router, prefix="/stocks", tags=["stocks"])
api_router.include_router(research.router, prefix="/stocks", tags=["research"])
api_router.include_router(research.results_router, prefix="/analyses", tags=["research"])
api_router.include_router(backtest.router, prefix="/stocks", tags=["backtest"])
api_router.include_router(factors.router, prefix="/stocks", tags=["factors"])
api_router.include_router(fundamentals.router, prefix="/stocks", tags=["fundamentals"])
api_router.include_router(backtest.results_router, prefix="/backtests", tags=["backtest"])
api_router.include_router(portfolio.router, prefix="/portfolio", tags=["portfolio"])
api_router.include_router(screener.router, prefix="/screener", tags=["screener"])
api_router.include_router(quotes.router, prefix="/ws", tags=["quotes"])
