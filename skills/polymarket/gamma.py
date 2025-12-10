#!/usr/bin/env python3
"""
gamma - Gamma API for market metadata

use this to map token_ids to markets, get market details, find active markets.

usage:
    from gamma import get_market_by_token, get_market_by_slug, find_markets

    # map token to market
    market = get_market_by_token('6192309209...')
    print(market['question'], market['outcome'])

    # find active 15m updown markets
    markets = find_markets(slug_contains='updown-15m', active=True)
"""
import json
import requests
from typing import Optional

GAMMA_API_BASE = "https://gamma-api.polymarket.com"

def get_market_by_token(token_id: str) -> Optional[dict]:
    """
    get market info for a token_id (clob token)

    returns dict with:
        question, slug, outcomes, outcome (specific to token),
        conditionId, clobTokenIds, outcomePrices, volume, liquidity, etc
    """
    resp = requests.get(f"{GAMMA_API_BASE}/markets",
                       params={'clob_token_ids': token_id},
                       timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if not data:
        return None

    market = data[0]

    # determine which outcome this token represents
    outcomes = json.loads(market.get('outcomes', '[]'))
    token_ids = json.loads(market.get('clobTokenIds', '[]'))

    idx = token_ids.index(token_id) if token_id in token_ids else -1
    outcome = outcomes[idx] if 0 <= idx < len(outcomes) else None

    market['outcome'] = outcome
    market['outcome_index'] = idx

    return market

def get_market_by_slug(slug: str) -> Optional[dict]:
    """get market by exact slug"""
    resp = requests.get(f"{GAMMA_API_BASE}/markets",
                       params={'slug': slug},
                       timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data[0] if data else None

def get_market_by_condition(condition_id: str) -> Optional[dict]:
    """get market by condition_id"""
    resp = requests.get(f"{GAMMA_API_BASE}/markets",
                       params={'condition_id': condition_id},
                       timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data[0] if data else None

def find_markets(slug_contains: Optional[str] = None,
                 active: bool = True,
                 closed: Optional[bool] = None,
                 limit: int = 50) -> list:
    """
    find markets matching criteria

    args:
        slug_contains: filter by slug substring
        active: filter by active status
        closed: filter by closed status
        limit: max results
    """
    params = {'limit': limit, 'active': str(active).lower()}

    if slug_contains:
        params['slug_contains'] = slug_contains
    if closed is not None:
        params['closed'] = str(closed).lower()

    resp = requests.get(f"{GAMMA_API_BASE}/markets", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()

def find_events(slug_contains: Optional[str] = None,
                active: bool = True,
                limit: int = 50) -> list:
    """
    find events (groups of markets) matching criteria

    events contain multiple related markets (e.g. all outcomes for a question)
    """
    params = {'limit': limit, 'active': str(active).lower()}

    if slug_contains:
        params['slug_contains'] = slug_contains

    resp = requests.get(f"{GAMMA_API_BASE}/events", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()

def get_15m_updown_markets(coin: Optional[str] = None, active: bool = True) -> list:
    """
    get 15-minute up/down markets

    args:
        coin: filter by coin (btc, eth, sol, xrp)
        active: only active markets
    """
    # these have dynamic slugs like btc-updown-15m-{timestamp}
    # search by pattern in recent trades instead

    resp = requests.get(f"{DATA_API_BASE}/trades", params={'limit': 50}, timeout=30)
    resp.raise_for_status()
    trades = resp.json()

    # find unique 15m market slugs
    slugs = set()
    for t in trades:
        slug = t.get('slug', '')
        if 'updown-15m' in slug:
            if coin is None or slug.startswith(f'{coin}-'):
                slugs.add(slug)

    # fetch market details
    markets = []
    for slug in slugs:
        market = get_market_by_slug(slug)
        if market and (not active or market.get('active')):
            markets.append(market)

    return markets

# shortcut for data api
DATA_API_BASE = "https://data-api.polymarket.com"

def get_token_info(token_id: str) -> dict:
    """
    get full token info including market context

    returns:
        token_id, market (question), slug, outcome, current_price,
        condition_id, counterpart_token_id
    """
    market = get_market_by_token(token_id)
    if not market:
        return {'token_id': token_id, 'error': 'not found'}

    outcomes = json.loads(market.get('outcomes', '[]'))
    token_ids = json.loads(market.get('clobTokenIds', '[]'))
    prices = json.loads(market.get('outcomePrices', '[]'))

    idx = market.get('outcome_index', -1)
    other_idx = 1 - idx if idx in [0, 1] else -1

    return {
        'token_id': token_id,
        'market': market.get('question'),
        'slug': market.get('slug'),
        'outcome': market.get('outcome'),
        'outcome_index': idx,
        'current_price': float(prices[idx]) if 0 <= idx < len(prices) else None,
        'condition_id': market.get('conditionId'),
        'counterpart_token_id': token_ids[other_idx] if 0 <= other_idx < len(token_ids) else None,
        'volume': market.get('volume'),
        'liquidity': market.get('liquidity')
    }

def batch_token_info(token_ids: list) -> dict:
    """get info for multiple tokens, returns dict keyed by token_id"""
    result = {}
    for tid in token_ids:
        result[tid] = get_token_info(tid)
    return result


if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print(__doc__)
        print("\nCommands:")
        print("  python gamma.py token <token_id>")
        print("  python gamma.py slug <slug>")
        print("  python gamma.py find [--slug-contains X] [--limit N]")
        print("  python gamma.py 15m [--coin btc]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == 'token' and len(sys.argv) > 2:
        token_id = sys.argv[2]
        info = get_token_info(token_id)

        print(f"Token: {token_id[:30]}...\n")
        if 'error' in info:
            print(f"Error: {info['error']}")
        else:
            print(f"Market: {info['market']}")
            print(f"Outcome: {info['outcome']}")
            print(f"Current price: ${info['current_price']}")
            print(f"Slug: {info['slug']}")
            print(f"Volume: {info['volume']}")
            print(f"Counterpart: {info['counterpart_token_id'][:30]}..." if info['counterpart_token_id'] else "")

    elif cmd == 'slug' and len(sys.argv) > 2:
        slug = sys.argv[2]
        market = get_market_by_slug(slug)

        if market:
            print(f"Market: {market.get('question')}")
            print(f"Slug: {market.get('slug')}")
            print(f"Outcomes: {market.get('outcomes')}")
            print(f"Prices: {market.get('outcomePrices')}")
            print(f"Volume: {market.get('volume')}")
            print(f"Active: {market.get('active')}")
            print(f"Closed: {market.get('closed')}")
        else:
            print("Market not found")

    elif cmd == 'find':
        slug_contains = None
        limit = 20

        for i, arg in enumerate(sys.argv):
            if arg == '--slug-contains' and i + 1 < len(sys.argv):
                slug_contains = sys.argv[i + 1]
            if arg == '--limit' and i + 1 < len(sys.argv):
                limit = int(sys.argv[i + 1])

        markets = find_markets(slug_contains=slug_contains, limit=limit)
        print(f"Found {len(markets)} markets:\n")

        for m in markets[:20]:
            print(f"{m.get('slug')}")
            print(f"  {m.get('question')[:60]}")
            print(f"  outcomes: {m.get('outcomes')}")
            print()

    elif cmd == '15m':
        coin = None
        for i, arg in enumerate(sys.argv):
            if arg == '--coin' and i + 1 < len(sys.argv):
                coin = sys.argv[i + 1]

        markets = get_15m_updown_markets(coin=coin)
        print(f"Found {len(markets)} 15m updown markets:\n")

        for m in markets:
            print(f"{m.get('slug')}")
            print(f"  {m.get('question')}")
            print(f"  prices: {m.get('outcomePrices')}")
            print()

    else:
        print(__doc__)
