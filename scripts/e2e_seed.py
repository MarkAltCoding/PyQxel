"""Seed a fresh database for the browser tests (``web/e2e``), and write the test account's
API key to the file named on the command line.

The browser tests run the real API on a throwaway SQLite database, with no provider keys,
so they reach no market data provider: the account, a small screened universe, and a
portfolio are stored here instead. Run by ``scripts/e2e-server.sh`` before it starts the
server, with ``DATABASE_URL`` pointing at that database.
"""

import asyncio
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from app.db.holdings import replace_holdings
from app.db.picks import save_picks
from app.db.screener import finish_run, replace_universe, start_run
from app.db.session import close_database, get_sessionmaker, init_db
from app.db.users import create_api_key, create_user
from app.models.picks import (
    Horizon,
    Pick,
    PicksContext,
    PicksProfile,
    PicksReport,
    PicksResponse,
    PicksUniverse,
)
from app.models.portfolio import HoldingShares
from app.models.screener import ScreenedStock

EMAIL = "e2e@example.invalid"
"""The test account, on a domain that can never receive mail."""


def stock(symbol: str, rank: int, exchange: str, **metrics: Any) -> ScreenedStock:
    """A screened stock with plausible figures, ``rank`` largest by market cap."""
    fields: dict[str, Any] = {
        "symbol": symbol,
        "name": f"{symbol.title()} Example Inc.",
        "exchange": exchange,
        "cik": 1_000 + rank,
        "sector": "Technology",
        "market_cap_source": "provider",
        "market_cap_rank": rank,
        "price": 100.0,
        "market_cap": 1e12 / rank,
        "avg_dollar_volume": 5e8 / rank,
        "pe_ratio": 10.0 + rank,
    }
    return ScreenedStock(**(fields | metrics))


UNIVERSE = [
    stock("ALFA", 1, "Nasdaq", sector="Technology"),
    stock("ALFB", 1, "Nasdaq", name="Alfa Example Inc.", avg_dollar_volume=1e8),
    stock("BRAVO", 2, "NYSE", sector="Finance", pe_ratio=8.0),
    stock("CHARL", 3, "Nasdaq", sector="Health Care", pe_ratio=40.0),
    stock("DELTA", 4, "NYSE", sector="Energy"),
]
"""Two companies on Nasdaq, the first in two share classes (ALFA and ALFB), and two on
the NYSE, largest first."""


def picks() -> PicksResponse:
    """A list of Claude's picks as the app stores one, so the pages that show picks have
    some without calling Claude: two from the universe, and a fund outside it."""

    def pick(symbol: str, kind: str, weight: float) -> Pick:
        return Pick(
            symbol=symbol,
            name=f"{symbol.title()} Example Inc.",
            kind=kind,  # type: ignore[arg-type]
            conviction="medium",
            weight=weight,
            thesis=f"{symbol} is cheap for its growth.",
            risk="Its growth could slow.",
            entry="Buy at market.",
            exit="Look again at the end of the period.",
        )

    profile = PicksProfile(horizon=Horizon(months=6), risk_tolerance="moderate", amount=10_000)
    picked = [pick("BRAVO", "stock", 0.5), pick("CHARL", "stock", 0.3), pick("BNDX", "etf", 0.2)]
    return PicksResponse(
        report=PicksReport(
            headline="Cheap growth, with a bond fund to steady it.",
            market_view="Leadership is narrow.",
            approach="Weighted by conviction for a six-month hold.",
            picks=picked,
            passed_over=["ALFA: priced for more than it delivers."],
            considerations=["Short-term gains are taxed as income."],
            data_limitations=["No news."],
        ),
        securities=[stock for stock in UNIVERSE if stock.symbol in ("BRAVO", "CHARL")],
        context=PicksContext(
            profile=profile,
            horizon=profile.horizon.label,
            horizon_weeks=round(profile.horizon.total_weeks, 1),
            today=date(2026, 11, 10),
            universe=PicksUniverse(
                securities=len(UNIVERSE), own=False, prices_as_of=date(2026, 11, 10), has_etfs=False
            ),
            candidates=[],
        ),
        model="claude-opus-5-5",
        generated_at=datetime(2026, 11, 10, 12, tzinfo=timezone.utc),
    )


async def seed(key_file: Path) -> None:
    """Create the account, its portfolio, a list of Claude's picks, and the shared
    universe; write the key."""
    await init_db()
    async with get_sessionmaker()() as session:
        user = await create_user(session, EMAIL)
        issued = await create_api_key(session, user.id, "e2e")
        await replace_holdings(
            session,
            user.id,
            [HoldingShares(symbol="ALFA", shares=3.5), HoldingShares(symbol="BRAVO", shares=10)],
        )
        await replace_universe(session, UNIVERSE)
        await save_picks(session, user.id, "seeded", picks(), "claude-opus-5-5", "medium")
        run_id = await start_run(session)
        await finish_run(
            session, run_id, stocks=len(UNIVERSE), prices_as_of=date(2026, 11, 10), licensed=True
        )
    await close_database()
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_text(issued.key)


if __name__ == "__main__":
    asyncio.run(seed(Path(sys.argv[1])))
