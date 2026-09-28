"""Version 1 API router that groups every v1 endpoint module."""

from fastapi import APIRouter

from app.api.v1.endpoints import stocks

api_router = APIRouter()
api_router.include_router(stocks.router, prefix="/stocks", tags=["stocks"])
