"""Tests for ranking a stock's screening metrics among its peers."""

import asyncio
from datetime import date
from typing import Any

import pytest

from app.db.screener import MIN_PEERS, PeerGroup, finish_run, peer_group, replace_universe
from app.db.screener import start_run
from app.db.session import get_sessionmaker
from app.models.screener import ScreenedStock
from app.stats.peers import PEER_FIELDS, compare_to_peers, percentile_of


def _stock(symbol: str, rank: int, **metrics: Any) -> ScreenedStock:
    """A stock with neutral metrics, overriding any."""
    fields: dict[str, Any] = {
        "symbol": symbol,
        "name": f"{symbol} Inc.",
        "exchange": "NYSE",
        "cik": rank,
        "market_cap_source": "provider",
        "market_cap_rank": rank,
        "price": 50.0,
        "market_cap": 1e12 / rank,
        "avg_dollar_volume": 1e7,
    }
    return ScreenedStock(**(fields | metrics))


def test_percentile_counts_ties_as_half() -> None:
    assert percentile_of(3.0, [1.0, 2.0, 3.0, 4.0]) == 62.5
    assert percentile_of(0.0, [1.0, 2.0]) == 0.0
    assert percentile_of(9.0, [1.0, 2.0]) == 100.0
    with pytest.raises(ValueError):
        percentile_of(1.0, [])


def test_peers_are_compared_on_every_peer_field() -> None:
    stock = _stock("ME", 1, net_margin=0.2)
    peers = [_stock(f"P{i}", i + 2, net_margin=0.05 * i) for i in range(8)]

    metrics = {metric.field: metric for metric in compare_to_peers(stock, peers)}

    assert list(metrics) == list(PEER_FIELDS)
    margin = metrics["net_margin"]
    # Peers' margins are 0 to 0.35: four are below 0.2 and one equals it.
    assert (margin.value, margin.percentile, margin.peers_with_data) == (0.2, 56.25, 8)
    assert margin.peer_median == pytest.approx(0.175)
    # Every peer is smaller, so the stock ranks at the top by size.
    assert metrics["market_cap"].percentile == 100.0


def test_pe_is_compared_among_profitable_peers_only() -> None:
    peers = [
        _stock(f"P{i}", i + 2, pe_ratio=pe) for i, pe in enumerate([-5, -2, 10, 20, 30, 40, 50])
    ]

    profitable = compare_to_peers(_stock("ME", 1, pe_ratio=25.0), peers)
    loss = compare_to_peers(_stock("ME", 1, pe_ratio=-3.0), peers)

    pe = next(metric for metric in profitable if metric.field == "pe_ratio")
    assert (pe.peers_with_data, pe.peer_median, pe.percentile) == (5, 30.0, 40.0)
    loss_pe = next(metric for metric in loss if metric.field == "pe_ratio")
    assert (loss_pe.value, loss_pe.percentile, loss_pe.peer_median) == (-3.0, None, 30.0)


def test_too_few_peers_with_a_metric_give_no_percentile() -> None:
    peers = [_stock(f"P{i}", i + 2, fcf_yield=0.05 if i < 4 else None) for i in range(10)]

    metrics = compare_to_peers(_stock("ME", 1, fcf_yield=0.03), peers)

    fcf = next(metric for metric in metrics if metric.field == "fcf_yield")
    assert (fcf.value, fcf.peer_median, fcf.percentile, fcf.peers_with_data) == (
        0.03,
        None,
        None,
        4,
    )


def _store(stocks: list[ScreenedStock]) -> None:
    async def run() -> None:
        async with get_sessionmaker()() as session:
            await replace_universe(session, stocks)
            run_id = await start_run(session)
            await finish_run(session, run_id, stocks=len(stocks), prices_as_of=date(2026, 10, 21))

    asyncio.run(run())


def _peer_group(symbol: str) -> PeerGroup | None:
    async def run() -> PeerGroup | None:
        async with get_sessionmaker()() as session:
            return await peer_group(session, symbol)

    return asyncio.run(run())


def _universe(industry_peers: int, sector_peers: int) -> list[ScreenedStock]:
    """ME, ``industry_peers`` stocks in its industry and ``sector_peers`` more in its sector."""
    sector = {"sector": "Technology"}
    stocks = [_stock("ME", 1, industry="Semiconductors", **sector)]
    stocks += [
        _stock(f"I{i}", i + 2, industry="Semiconductors", **sector) for i in range(industry_peers)
    ]
    stocks += [_stock(f"S{i}", i + 100, industry="Software", **sector) for i in range(sector_peers)]
    stocks += [_stock(f"O{i}", i + 200, sector="Energy") for i in range(MIN_PEERS)]
    return stocks


@pytest.mark.parametrize(
    ("industry_peers", "sector_peers", "kind", "name", "peers"),
    [
        (MIN_PEERS, 5, "industry", "Semiconductors", MIN_PEERS),
        (MIN_PEERS - 1, 1, "sector", "Technology", MIN_PEERS),
        (2, 2, "universe", "screened universe", 4 + MIN_PEERS),
    ],
)
def test_peer_group_is_the_narrowest_with_enough_stocks(
    industry_peers: int, sector_peers: int, kind: str, name: str, peers: int
) -> None:
    stocks = _universe(industry_peers, sector_peers)
    _store(stocks)

    group = _peer_group("ME")

    assert group is not None
    assert (group.kind, group.name, len(group.peers)) == (kind, name, peers)
    assert group.stock.symbol == "ME"
    assert "ME" not in {peer.symbol for peer in group.peers}
    assert group.universe_size == len(stocks)
    assert group.prices_as_of == date(2026, 10, 21)


def test_unknown_stock_has_no_peer_group() -> None:
    _store(_universe(MIN_PEERS, 0))

    assert _peer_group("NOPE") is None
