"""Version 1 API router that groups every v1 endpoint module."""

from fastapi import APIRouter

from app.api.v1.endpoints import backtest, quotes, research, stocks

api_router = APIRouter()
api_router.include_router(stocks.router, prefix="/stocks", tags=["stocks"])
api_router.include_router(research.router, prefix="/stocks", tags=["research"])
api_router.include_router(backtest.router, prefix="/stocks", tags=["backtest"])
api_router.include_router(quotes.router, prefix="/ws", tags=["quotes"])
