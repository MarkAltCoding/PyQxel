"""Seed a fresh database for the browser tests (``web/e2e``), and write the test account's
API key to the file named on the command line.

The browser tests run the real API on a throwaway SQLite database, with no provider keys,
so they reach no market data provider: the account, a small screened universe, and a
portfolio are stored here instead. Run by ``scripts/e2e-server.sh`` before it starts the
server, with ``DATABASE_URL`` pointing at that database.
"""

import asyncio
import sys
from datetime import date
from pathlib import Path
from typing import Any

from app.db.holdings import replace_holdings
from app.db.screener import finish_run, replace_universe, start_run
from app.db.session import close_database, get_sessionmaker, init_db
from app.db.users import create_api_key, create_user
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


async def seed(key_file: Path) -> None:
    """Create the account, its portfolio, and the shared universe; write the key."""
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
        run_id = await start_run(session)
        await finish_run(
            session, run_id, stocks=len(UNIVERSE), prices_as_of=date(2026, 11, 10), licensed=True
        )
    await close_database()
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_text(issued.key)


if __name__ == "__main__":
    asyncio.run(seed(Path(sys.argv[1])))
