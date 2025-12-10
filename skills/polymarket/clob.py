#!/usr/bin/env python3
"""
clob - CLOB API for orderbook and pricing data

live orderbook snapshots, best prices, market listing.

usage:
    from clob import get_book, get_price, get_spread

    # get full orderbook
    book = get_book('61923092...')
    print(book['bids'][:5], book['asks'][:5])

    # get best price
    price = get_price('61923092...', side='buy')

    # get spread
    spread = get_spread('61923092...')
"""
import requests
from typing import Optional

CLOB_API_BASE = "https://clob.polymarket.com"

def get_book(token_id: str) -> Optional[dict]:
    """
    get full orderbook for a token

    returns dict with:
        market (condition_id), asset_id, timestamp, hash,
        bids: [{price, size}, ...], asks: [{price, size}, ...]

    bids/asks sorted by price (best first)
    """
    resp = requests.get(f"{CLOB_API_BASE}/book",
                       params={'token_id': token_id},
                       timeout=30)

    if resp.status_code == 404:
        return None

    resp.raise_for_status()
    return resp.json()

def get_price(token_id: str, side: str = 'buy') -> Optional[float]:
    """
    get best price for a token

    args:
        token_id: clob token id
        side: 'buy' or 'sell'

    returns best price as float, or None if no liquidity
    """
    resp = requests.get(f"{CLOB_API_BASE}/price",
                       params={'token_id': token_id, 'side': side},
                       timeout=30)

    if resp.status_code == 404:
        return None

    resp.raise_for_status()
    data = resp.json()
    return float(data.get('price')) if data.get('price') else None

def get_spread(token_id: str) -> Optional[dict]:
    """
    get bid/ask spread for a token

    returns dict with:
        bid, ask, spread, spread_pct, mid
    """
    book = get_book(token_id)
    if not book:
        return None

    bids = book.get('bids', [])
    asks = book.get('asks', [])

    if not bids or not asks:
        return None

    best_bid = float(bids[0]['price'])
    best_ask = float(asks[0]['price'])
    spread = best_ask - best_bid
    mid = (best_bid + best_ask) / 2

    return {
        'bid': best_bid,
        'ask': best_ask,
        'spread': spread,
        'spread_pct': round(spread / mid * 100, 2) if mid > 0 else None,
        'mid': round(mid, 4),
        'bid_size': float(bids[0]['size']),
        'ask_size': float(asks[0]['size'])
    }

def get_depth(token_id: str, levels: int = 5) -> Optional[dict]:
    """
    get orderbook depth at multiple levels

    returns dict with:
        bids: [(price, size, cumulative), ...],
        asks: [(price, size, cumulative), ...],
        total_bid_depth, total_ask_depth
    """
    book = get_book(token_id)
    if not book:
        return None

    def process_side(orders, n):
        result = []
        cumulative = 0
        for o in orders[:n]:
            size = float(o['size'])
            cumulative += size
            result.append({
                'price': float(o['price']),
                'size': size,
                'cumulative': round(cumulative, 2)
            })
        return result

    bids = process_side(book.get('bids', []), levels)
    asks = process_side(book.get('asks', []), levels)

    return {
        'bids': bids,
        'asks': asks,
        'total_bid_depth': sum(float(b['size']) for b in book.get('bids', [])),
        'total_ask_depth': sum(float(a['size']) for a in book.get('asks', []))
    }

def get_markets(limit: int = 100, cursor: Optional[str] = None) -> dict:
    """
    list all markets from CLOB

    returns dict with:
        data: [market, ...], next_cursor, limit, count
    """
    params = {'limit': min(limit, 1000)}
    if cursor:
        params['cursor'] = cursor

    resp = requests.get(f"{CLOB_API_BASE}/markets", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()

def get_market(condition_id: str) -> Optional[dict]:
    """get single market by condition_id"""
    resp = requests.get(f"{CLOB_API_BASE}/markets/{condition_id}", timeout=30)

    if resp.status_code == 404:
        return None

    resp.raise_for_status()
    return resp.json()

def get_midpoint(token_id: str) -> Optional[float]:
    """get midpoint price for a token"""
    spread = get_spread(token_id)
    return spread['mid'] if spread else None

def get_combined_spread(up_token: str, down_token: str) -> Optional[dict]:
    """
    get combined spread for up/down token pair

    useful for 15m updown markets. calculates book edge.

    returns dict with:
        up_bid, down_bid, combined, edge, edge_pct
    """
    up_spread = get_spread(up_token)
    down_spread = get_spread(down_token)

    if not up_spread or not down_spread:
        return None

    up_bid = up_spread['bid']
    down_bid = down_spread['bid']
    combined = up_bid + down_bid
    edge = 1 - combined

    return {
        'up_bid': up_bid,
        'down_bid': down_bid,
        'combined': round(combined, 4),
        'edge': round(edge, 4),
        'edge_pct': round(edge * 100, 2),
        'up_depth': up_spread['bid_size'],
        'down_depth': down_spread['bid_size']
    }

def estimate_fill(token_id: str, side: str, size: float) -> Optional[dict]:
    """
    estimate fill price for an order

    args:
        token_id: clob token
        side: 'buy' or 'sell'
        size: shares to trade

    returns dict with:
        avg_price, total_cost, slippage_pct, levels_consumed
    """
    book = get_book(token_id)
    if not book:
        return None

    orders = book['asks'] if side == 'buy' else book['bids']
    if not orders:
        return None

    remaining = size
    total_cost = 0
    levels = 0
    best_price = float(orders[0]['price'])

    for o in orders:
        price = float(o['price'])
        available = float(o['size'])

        fill = min(remaining, available)
        total_cost += fill * price
        remaining -= fill
        levels += 1

        if remaining <= 0:
            break

    if remaining > 0:
        return {'error': 'insufficient liquidity', 'unfilled': remaining}

    avg_price = total_cost / size
    slippage = abs(avg_price - best_price) / best_price * 100

    return {
        'avg_price': round(avg_price, 4),
        'total_cost': round(total_cost, 2),
        'slippage_pct': round(slippage, 2),
        'levels_consumed': levels,
        'best_price': best_price
    }


if __name__ == '__main__':
    import sys
    import json

    if len(sys.argv) < 2:
        print(__doc__)
        print("\nCommands:")
        print("  python clob.py book <token_id>")
        print("  python clob.py price <token_id> [--side buy|sell]")
        print("  python clob.py spread <token_id>")
        print("  python clob.py depth <token_id> [--levels N]")
        print("  python clob.py estimate <token_id> <side> <size>")
        print("  python clob.py markets [--limit N]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == 'book' and len(sys.argv) > 2:
        token_id = sys.argv[2]
        book = get_book(token_id)

        if not book:
            print("No orderbook found")
        else:
            print(f"Orderbook for {token_id[:30]}...\n")
            print("BIDS:")
            for b in book['bids'][:10]:
                print(f"  {b['price']:>6} x {float(b['size']):>10.2f}")
            print("\nASKS:")
            for a in book['asks'][:10]:
                print(f"  {a['price']:>6} x {float(a['size']):>10.2f}")

    elif cmd == 'price' and len(sys.argv) > 2:
        token_id = sys.argv[2]
        side = 'buy'
        for i, arg in enumerate(sys.argv):
            if arg == '--side' and i + 1 < len(sys.argv):
                side = sys.argv[i + 1]

        price = get_price(token_id, side=side)
        if price:
            print(f"Best {side} price: ${price}")
        else:
            print("No price available")

    elif cmd == 'spread' and len(sys.argv) > 2:
        token_id = sys.argv[2]
        spread = get_spread(token_id)

        if spread:
            print(f"Spread for {token_id[:30]}...\n")
            print(f"Bid: ${spread['bid']} ({spread['bid_size']:.0f} shares)")
            print(f"Ask: ${spread['ask']} ({spread['ask_size']:.0f} shares)")
            print(f"Spread: ${spread['spread']:.4f} ({spread['spread_pct']}%)")
            print(f"Mid: ${spread['mid']}")
        else:
            print("No spread data")

    elif cmd == 'depth' and len(sys.argv) > 2:
        token_id = sys.argv[2]
        levels = 5
        for i, arg in enumerate(sys.argv):
            if arg == '--levels' and i + 1 < len(sys.argv):
                levels = int(sys.argv[i + 1])

        depth = get_depth(token_id, levels=levels)

        if depth:
            print(f"Depth for {token_id[:30]}...\n")
            print("BIDS:")
            for b in depth['bids']:
                print(f"  ${b['price']:.2f} x {b['size']:>8.0f} (cum: {b['cumulative']:>8.0f})")
            print(f"  Total: {depth['total_bid_depth']:.0f}")
            print("\nASKS:")
            for a in depth['asks']:
                print(f"  ${a['price']:.2f} x {a['size']:>8.0f} (cum: {a['cumulative']:>8.0f})")
            print(f"  Total: {depth['total_ask_depth']:.0f}")
        else:
            print("No depth data")

    elif cmd == 'estimate' and len(sys.argv) > 4:
        token_id = sys.argv[2]
        side = sys.argv[3]
        size = float(sys.argv[4])

        est = estimate_fill(token_id, side, size)

        if est and 'error' not in est:
            print(f"Fill estimate for {side} {size:.0f} shares:\n")
            print(f"Best price: ${est['best_price']}")
            print(f"Avg price: ${est['avg_price']}")
            print(f"Total cost: ${est['total_cost']}")
            print(f"Slippage: {est['slippage_pct']}%")
            print(f"Levels consumed: {est['levels_consumed']}")
        elif est:
            print(f"Error: {est['error']}")
            if 'unfilled' in est:
                print(f"Unfilled: {est['unfilled']:.0f} shares")
        else:
            print("No estimate available")

    elif cmd == 'markets':
        limit = 20
        for i, arg in enumerate(sys.argv):
            if arg == '--limit' and i + 1 < len(sys.argv):
                limit = int(sys.argv[i + 1])

        result = get_markets(limit=limit)
        print(f"Markets (showing {len(result['data'])} of {result['count']}):\n")

        for m in result['data'][:20]:
            print(f"{m.get('question', 'N/A')[:60]}")
            print(f"  condition: {m.get('condition_id', 'N/A')[:30]}...")
            print(f"  active: {m.get('active')}, closed: {m.get('closed')}")
            print()

    else:
        print(__doc__)
