#!/usr/bin/env python3
"""
data_api - Polymarket data API for enriched trade data

pre-decoded trades with market metadata. easier to use than subgraph but
may miss some historical data. max 500 results per query.

usage:
    from data_api import get_trades, get_wallet_trades

    # recent trades
    trades = get_trades(limit=50)

    # wallet trades (already decoded with market info)
    trades = get_wallet_trades('0x6031...', limit=100)
"""
import requests
from typing import Optional

DATA_API_BASE = "https://data-api.polymarket.com"

def get_trades(limit: int = 100, offset: int = 0,
               market: Optional[str] = None,
               asset_id: Optional[str] = None) -> list:
    """
    fetch recent trades from data api

    args:
        limit: max 500
        offset: pagination offset
        market: condition_id to filter
        asset_id: token_id to filter

    returns list of trades with:
        proxyWallet, side, asset, conditionId, size, price, timestamp,
        title, slug, outcome, outcomeIndex, transactionHash, + profile info
    """
    params = {'limit': min(limit, 500), 'offset': offset}

    if market:
        params['market'] = market
    if asset_id:
        params['asset_id'] = asset_id

    resp = requests.get(f"{DATA_API_BASE}/trades", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()

def get_wallet_trades(wallet: str, limit: int = 100, offset: int = 0,
                      role: str = 'both') -> list:
    """
    fetch trades for a wallet

    args:
        wallet: ethereum address
        limit: max 500
        offset: pagination offset
        role: 'maker', 'taker', or 'both'

    note: data api returns same results for maker/taker params for some wallets
    """
    params = {'limit': min(limit, 500), 'offset': offset}

    if role == 'maker':
        params['maker'] = wallet
    elif role == 'taker':
        params['taker'] = wallet
    else:
        # fetch both
        params['maker'] = wallet

    resp = requests.get(f"{DATA_API_BASE}/trades", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()

def get_wallet_trades_all(wallet: str, max_trades: int = 5000) -> list:
    """
    fetch all trades for wallet (paginated)
    stops at max_trades to prevent infinite loops
    """
    all_trades = []
    offset = 0

    while len(all_trades) < max_trades:
        batch = get_wallet_trades(wallet, limit=500, offset=offset)
        if not batch:
            break
        all_trades.extend(batch)
        offset += len(batch)

        if len(batch) < 500:
            break

    return all_trades

def get_market_trades(condition_id: str, limit: int = 100) -> list:
    """fetch trades for a specific market"""
    return get_trades(limit=limit, market=condition_id)

def get_token_trades(token_id: str, limit: int = 100) -> list:
    """fetch trades for a specific token/outcome"""
    return get_trades(limit=limit, asset_id=token_id)

def summarize_trades(trades: list) -> dict:
    """
    summarize a list of trades

    returns dict with:
        count, total_volume, buy_count, sell_count,
        unique_markets, unique_outcomes, price_range
    """
    if not trades:
        return {'count': 0}

    buys = [t for t in trades if t.get('side') == 'BUY']
    sells = [t for t in trades if t.get('side') == 'SELL']

    total_volume = sum(t.get('size', 0) * t.get('price', 0) for t in trades)
    markets = set(t.get('slug', '') for t in trades)
    outcomes = set(t.get('outcome', '') for t in trades)

    prices = [t.get('price', 0) for t in trades if t.get('price')]

    return {
        'count': len(trades),
        'total_volume_usdc': round(total_volume, 2),
        'buy_count': len(buys),
        'sell_count': len(sells),
        'buy_volume': round(sum(t['size'] * t['price'] for t in buys), 2),
        'sell_volume': round(sum(t['size'] * t['price'] for t in sells), 2),
        'unique_markets': len(markets),
        'unique_outcomes': len(outcomes),
        'price_range': (min(prices), max(prices)) if prices else None,
        'avg_price': round(sum(prices) / len(prices), 4) if prices else None
    }


if __name__ == '__main__':
    import sys
    import json
    from datetime import datetime

    if len(sys.argv) < 2:
        print(__doc__)
        print("\nCommands:")
        print("  python data_api.py recent [--limit N]")
        print("  python data_api.py wallet <address> [--limit N]")
        print("  python data_api.py market <condition_id> [--limit N]")
        print("  python data_api.py summary <address>")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == 'recent':
        limit = 20
        for i, arg in enumerate(sys.argv):
            if arg == '--limit' and i + 1 < len(sys.argv):
                limit = int(sys.argv[i + 1])

        trades = get_trades(limit=limit)
        print(f"Recent {len(trades)} trades:\n")

        for t in trades:
            ts = datetime.fromtimestamp(t['timestamp']).strftime('%H:%M:%S')
            print(f"{ts} {t['side']} {t['size']:.2f} @ ${t['price']:.2f}")
            print(f"  {t['outcome']} - {t['title'][:50]}")
            print(f"  wallet: {t['proxyWallet'][:16]}...")
            print()

    elif cmd == 'wallet' and len(sys.argv) > 2:
        wallet = sys.argv[2]
        limit = 20
        for i, arg in enumerate(sys.argv):
            if arg == '--limit' and i + 1 < len(sys.argv):
                limit = int(sys.argv[i + 1])

        trades = get_wallet_trades(wallet, limit=limit)
        print(f"Found {len(trades)} trades for {wallet[:16]}...\n")

        for t in trades[:15]:
            ts = datetime.fromtimestamp(t['timestamp']).strftime('%m/%d %H:%M')
            print(f"{ts} {t['side']} {t['size']:.2f} @ ${t['price']:.2f}")
            print(f"  {t['outcome']} - {t['title'][:50]}")
            print()

    elif cmd == 'market' and len(sys.argv) > 2:
        condition_id = sys.argv[2]
        limit = 20
        for i, arg in enumerate(sys.argv):
            if arg == '--limit' and i + 1 < len(sys.argv):
                limit = int(sys.argv[i + 1])

        trades = get_market_trades(condition_id, limit=limit)
        print(f"Found {len(trades)} trades for market\n")

        for t in trades[:15]:
            ts = datetime.fromtimestamp(t['timestamp']).strftime('%H:%M:%S')
            print(f"{ts} {t['side']} {t['size']:.2f} @ ${t['price']:.2f} - {t['outcome']}")

    elif cmd == 'summary' and len(sys.argv) > 2:
        wallet = sys.argv[2]
        print(f"Fetching all trades for {wallet[:16]}...")
        trades = get_wallet_trades_all(wallet, max_trades=2000)
        summary = summarize_trades(trades)

        print(f"\nSummary ({summary['count']} trades):")
        print(f"  Total volume: ${summary['total_volume_usdc']:,.2f}")
        print(f"  Buys: {summary['buy_count']} (${summary['buy_volume']:,.2f})")
        print(f"  Sells: {summary['sell_count']} (${summary['sell_volume']:,.2f})")
        print(f"  Unique markets: {summary['unique_markets']}")
        print(f"  Price range: {summary['price_range']}")

    else:
        print(__doc__)
