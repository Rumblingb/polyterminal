#!/usr/bin/env python3
"""
markets - market discovery, search, and details

find markets by volume, category, search terms. get market details and events.

usage:
    from markets import search_markets, get_trending, get_market_details

    # top markets by volume
    trending = get_trending(limit=20)

    # search markets
    results = search_markets('bitcoin', limit=10)

    # get specific market
    market = get_market_details(slug='fed-rate-hike-in-2025')

cli:
    python markets.py trending [--limit N]
    python markets.py search <query> [--limit N]
    python markets.py categories
    python markets.py category <name> [--limit N]
    python markets.py details <slug>
    python markets.py event <slug>
    python markets.py active [--limit N]
    python markets.py 15m [--coin btc]
"""
import requests
import json
from typing import Optional, List
from datetime import datetime

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
DATA_API = "https://data-api.polymarket.com"


# =============================================================================
# MARKET DISCOVERY
# =============================================================================

def public_search(query: str, limit_per_type: int = 10, events_status: str = None,
                  search_profiles: bool = False) -> dict:
    """
    official polymarket search API

    args:
        query: search text
        limit_per_type: max results per type (events, tags, profiles)
        events_status: filter by status
        search_profiles: include profile results

    returns dict with: events, tags, profiles, pagination
    """
    params = {'q': query, 'limit_per_type': limit_per_type}
    if events_status:
        params['events_status'] = events_status
    if search_profiles:
        params['search_profiles'] = 'true'

    resp = requests.get(f"{GAMMA_API}/public-search", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()

def get_trending(limit: int = 20, timeframe: str = '24hr') -> list:
    """
    get top markets by volume

    args:
        limit: max results
        timeframe: '24hr', '1wk', '1mo', '1yr'
    """
    order_field = f'volume{timeframe}' if timeframe != '24hr' else 'volume24hr'

    resp = requests.get(f"{GAMMA_API}/markets", params={
        'active': 'true',
        'closed': 'false',
        'limit': limit,
        'order': order_field,
        'ascending': 'false'
    }, timeout=30)
    resp.raise_for_status()
    return resp.json()

def get_active_markets(limit: int = 50, offset: int = 0) -> list:
    """get all active markets"""
    resp = requests.get(f"{GAMMA_API}/markets", params={
        'active': 'true',
        'closed': 'false',
        'limit': limit,
        'offset': offset
    }, timeout=30)
    resp.raise_for_status()
    return resp.json()

def list_markets(limit: int = 100, offset: int = 0, order: str = 'volume24hr',
                 ascending: bool = False, active: bool = None, closed: bool = None,
                 slug: list = None, condition_ids: list = None, clob_token_ids: list = None,
                 tag_id: int = None, volume_min: float = None, liquidity_min: float = None) -> list:
    """
    list markets with full query options

    args:
        limit: max results
        offset: pagination offset
        order: sort field (volume24hr, volume, liquidity, etc)
        ascending: sort direction
        active: filter by active status
        closed: filter by closed status
        slug: filter by slugs
        condition_ids: filter by condition IDs
        clob_token_ids: filter by token IDs
        tag_id: filter by category/tag
        volume_min: minimum volume filter
        liquidity_min: minimum liquidity filter

    returns list of markets
    """
    params = {'limit': limit, 'offset': offset, 'order': order, 'ascending': str(ascending).lower()}

    if active is not None:
        params['active'] = str(active).lower()
    if closed is not None:
        params['closed'] = str(closed).lower()
    if slug:
        params['slug'] = slug
    if condition_ids:
        params['condition_ids'] = condition_ids
    if clob_token_ids:
        params['clob_token_ids'] = clob_token_ids
    if tag_id:
        params['tag_id'] = tag_id
    if volume_min:
        params['volume_num_min'] = volume_min
    if liquidity_min:
        params['liquidity_num_min'] = liquidity_min

    resp = requests.get(f"{GAMMA_API}/markets", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()

def search_markets(query: str, limit: int = 20, active_only: bool = True) -> list:
    """
    search markets by text query

    searches in question/title text
    """
    # gamma doesn't have great text search, fetch more and filter client-side
    params = {'limit': 500}
    if active_only:
        params['active'] = 'true'
        params['closed'] = 'false'

    resp = requests.get(f"{GAMMA_API}/markets", params=params, timeout=30)
    resp.raise_for_status()

    # filter by query
    results = resp.json()
    query_lower = query.lower()
    matches = [m for m in results if query_lower in m.get('question', '').lower()
               or query_lower in m.get('slug', '').lower()
               or query_lower in m.get('description', '').lower()]

    # sort by volume
    matches.sort(key=lambda m: float(m.get('volume', 0) or 0), reverse=True)

    return matches[:limit]

def get_markets_by_category(category: str, limit: int = 20) -> list:
    """get markets in a category"""
    resp = requests.get(f"{GAMMA_API}/markets", params={
        'active': 'true',
        'closed': 'false',
        'limit': limit,
        'tag': category
    }, timeout=30)
    resp.raise_for_status()
    return resp.json()

def get_categories() -> list:
    """get all market categories/tags"""
    resp = requests.get(f"{GAMMA_API}/tags", timeout=30)
    resp.raise_for_status()
    return resp.json()


# =============================================================================
# MARKET DETAILS
# =============================================================================

def get_market_details(slug: str = None, condition_id: str = None) -> Optional[dict]:
    """
    get full market details

    returns dict with all market info including:
        question, outcomes, prices, volume, liquidity, tokens, etc
    """
    params = {}
    if slug:
        params['slug'] = slug
    elif condition_id:
        params['condition_id'] = condition_id
    else:
        return None

    resp = requests.get(f"{GAMMA_API}/markets", params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data[0] if data else None

def get_event(slug: str) -> Optional[dict]:
    """
    get event (group of related markets)

    events contain multiple markets for same question
    """
    resp = requests.get(f"{GAMMA_API}/events", params={'slug': slug}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data[0] if data else None

def get_events(active: bool = True, limit: int = 20) -> list:
    """get list of events"""
    resp = requests.get(f"{GAMMA_API}/events", params={
        'active': str(active).lower(),
        'limit': limit
    }, timeout=30)
    resp.raise_for_status()
    return resp.json()


# =============================================================================
# CLOB MARKETS
# =============================================================================

def get_clob_markets(limit: int = 100, cursor: str = None,
                     active: bool = True) -> dict:
    """
    get markets from CLOB with trading info

    returns dict with:
        data: [markets], next_cursor, count
    """
    params = {'limit': min(limit, 1000)}
    if cursor:
        params['next_cursor'] = cursor

    resp = requests.get(f"{CLOB_API}/markets", params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if active:
        data['data'] = [m for m in data.get('data', [])
                       if m.get('active') and not m.get('closed')]

    return data

def get_clob_market(condition_id: str) -> Optional[dict]:
    """get single CLOB market by condition_id"""
    resp = requests.get(f"{CLOB_API}/markets/{condition_id}", timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()

def get_price_history(condition_id: str, interval: str = '1h',
                      fidelity: int = 1) -> list:
    """
    get price history for a market

    args:
        condition_id: market condition
        interval: '1m', '5m', '1h', '1d'
        fidelity: data resolution
    """
    resp = requests.get(f"{CLOB_API}/prices-history", params={
        'market': condition_id,
        'interval': interval,
        'fidelity': fidelity
    }, timeout=30)
    resp.raise_for_status()
    return resp.json().get('history', [])


# =============================================================================
# 15M UPDOWN MARKETS (SPECIAL HANDLING)
# =============================================================================

def get_15m_markets(coin: str = None, active_only: bool = True) -> list:
    """
    get 15-minute up/down markets

    these have dynamic slugs like {coin}-updown-15m-{timestamp}
    """
    # get from recent trades to find active 15m markets
    resp = requests.get(f"{DATA_API}/trades", params={'limit': 100}, timeout=30)
    resp.raise_for_status()
    trades = resp.json()

    # find unique 15m slugs
    slugs = set()
    for t in trades:
        slug = t.get('slug', '')
        if 'updown-15m' in slug:
            if coin is None or slug.startswith(f'{coin}-'):
                slugs.add(slug)

    # get market details
    markets = []
    for slug in slugs:
        market = get_market_details(slug=slug)
        if market:
            if not active_only or (market.get('active') and not market.get('closed')):
                markets.append(market)

    return sorted(markets, key=lambda m: m.get('slug', ''))

def get_current_15m_window() -> dict:
    """
    get current 15m window markets for all coins

    returns dict keyed by coin with market info
    """
    markets = get_15m_markets()
    result = {}

    for m in markets:
        slug = m.get('slug', '')
        for coin in ['btc', 'eth', 'sol', 'xrp']:
            if slug.startswith(f'{coin}-'):
                if coin not in result:
                    result[coin] = m
                else:
                    # keep the most recent (higher timestamp in slug)
                    try:
                        curr_ts = int(result[coin]['slug'].split('-')[-1])
                        new_ts = int(slug.split('-')[-1])
                        if new_ts > curr_ts:
                            result[coin] = m
                    except:
                        pass
                break

    return result


# =============================================================================
# MARKET ANALYTICS
# =============================================================================

def get_market_summary(slug: str) -> dict:
    """
    get summary analytics for a market

    combines gamma and clob data
    """
    market = get_market_details(slug=slug)
    if not market:
        return {'error': 'market not found'}

    condition_id = market.get('conditionId')
    outcomes = json.loads(market.get('outcomes', '[]'))
    prices = json.loads(market.get('outcomePrices', '[]'))
    tokens = json.loads(market.get('clobTokenIds', '[]'))

    # get orderbook data for each outcome
    from clob import get_spread

    outcome_data = []
    for i, outcome in enumerate(outcomes):
        data = {
            'outcome': outcome,
            'price': float(prices[i]) if i < len(prices) else None,
            'token_id': tokens[i] if i < len(tokens) else None
        }

        if data['token_id']:
            spread = get_spread(data['token_id'])
            if spread:
                data['bid'] = spread['bid']
                data['ask'] = spread['ask']
                data['spread_pct'] = spread['spread_pct']
                data['liquidity'] = spread['bid_size'] + spread['ask_size']

        outcome_data.append(data)

    return {
        'question': market.get('question'),
        'slug': slug,
        'condition_id': condition_id,
        'volume': market.get('volume'),
        'volume_24h': market.get('volume24hr'),
        'liquidity': market.get('liquidity'),
        'end_date': market.get('endDate'),
        'active': market.get('active'),
        'closed': market.get('closed'),
        'outcomes': outcome_data
    }

def compare_markets(slugs: list) -> list:
    """compare multiple markets side by side"""
    return [get_market_summary(slug) for slug in slugs]


# =============================================================================
# CLI
# =============================================================================

if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == 'trending':
        limit = 20
        for i, arg in enumerate(sys.argv):
            if arg == '--limit' and i + 1 < len(sys.argv):
                limit = int(sys.argv[i + 1])

        markets = get_trending(limit=limit)
        print(f"Top {len(markets)} markets by 24h volume:\n")

        for m in markets:
            vol = float(m.get('volume24hr', 0))
            liq = float(m.get('liquidity', 0))
            print(f"{m.get('question', 'N/A')[:60]}")
            print(f"  slug: {m.get('slug')}")
            print(f"  24h vol: ${vol:,.0f} | liquidity: ${liq:,.0f}")
            print(f"  prices: {m.get('outcomePrices')}")
            print()

    elif cmd == 'search' and len(sys.argv) > 2:
        query = sys.argv[2]
        limit = 10
        for i, arg in enumerate(sys.argv):
            if arg == '--limit' and i + 1 < len(sys.argv):
                limit = int(sys.argv[i + 1])

        markets = search_markets(query, limit=limit)
        print(f"Search results for '{query}' ({len(markets)}):\n")

        for m in markets:
            print(f"{m.get('question', 'N/A')[:60]}")
            print(f"  slug: {m.get('slug')}")
            print(f"  prices: {m.get('outcomePrices')}")
            print()

    elif cmd == 'categories':
        cats = get_categories()
        print(f"Categories ({len(cats)}):\n")
        for c in cats[:30]:
            if isinstance(c, dict):
                print(f"  {c.get('label', c.get('slug', str(c)))}")
            else:
                print(f"  {c}")

    elif cmd == 'category' and len(sys.argv) > 2:
        category = sys.argv[2]
        limit = 10
        for i, arg in enumerate(sys.argv):
            if arg == '--limit' and i + 1 < len(sys.argv):
                limit = int(sys.argv[i + 1])

        markets = get_markets_by_category(category, limit=limit)
        print(f"Markets in '{category}' ({len(markets)}):\n")

        for m in markets:
            print(f"{m.get('question', 'N/A')[:60]}")
            print(f"  slug: {m.get('slug')}")
            print()

    elif cmd == 'details' and len(sys.argv) > 2:
        slug = sys.argv[2]
        summary = get_market_summary(slug)

        if 'error' in summary:
            print(f"Error: {summary['error']}")
        else:
            print(f"Market: {summary['question']}\n")
            print(f"Slug: {summary['slug']}")
            print(f"Condition: {summary['condition_id']}")
            print(f"Volume: ${float(summary['volume'] or 0):,.0f}")
            print(f"24h Volume: ${float(summary['volume_24h'] or 0):,.0f}")
            print(f"Liquidity: ${float(summary['liquidity'] or 0):,.0f}")
            print(f"End date: {summary['end_date']}")
            print(f"Active: {summary['active']}, Closed: {summary['closed']}")
            print(f"\nOutcomes:")
            for o in summary['outcomes']:
                print(f"  {o['outcome']}: ${o['price']:.4f}" if o['price'] else f"  {o['outcome']}: N/A")
                if o.get('bid'):
                    print(f"    bid: ${o['bid']:.2f}, ask: ${o['ask']:.2f}, spread: {o['spread_pct']}%")

    elif cmd == 'event' and len(sys.argv) > 2:
        slug = sys.argv[2]
        event = get_event(slug)

        if event:
            print(f"Event: {event.get('title')}\n")
            print(f"Slug: {event.get('slug')}")
            print(f"Volume: ${float(event.get('volume', 0)):,.0f}")
            print(f"Markets ({len(event.get('markets', []))}):")
            for m in event.get('markets', []):
                print(f"  - {m.get('question', 'N/A')[:50]}")
                print(f"    prices: {m.get('outcomePrices')}")
        else:
            print("Event not found")

    elif cmd == 'active':
        limit = 20
        for i, arg in enumerate(sys.argv):
            if arg == '--limit' and i + 1 < len(sys.argv):
                limit = int(sys.argv[i + 1])

        markets = get_active_markets(limit=limit)
        print(f"Active markets ({len(markets)}):\n")

        for m in markets:
            print(f"{m.get('question', 'N/A')[:60]}")
            print(f"  slug: {m.get('slug')}")
            print()

    elif cmd == '15m':
        coin = None
        for i, arg in enumerate(sys.argv):
            if arg == '--coin' and i + 1 < len(sys.argv):
                coin = sys.argv[i + 1]

        if coin:
            markets = get_15m_markets(coin=coin)
        else:
            markets = get_current_15m_window()
            if isinstance(markets, dict):
                print("Current 15m windows:\n")
                for c, m in markets.items():
                    print(f"{c.upper()}: {m.get('slug')}")
                    print(f"  prices: {m.get('outcomePrices')}")
                    print()
                sys.exit(0)

        print(f"15m markets ({len(markets)}):\n")
        for m in markets:
            print(f"{m.get('slug')}")
            print(f"  {m.get('question')}")
            print(f"  prices: {m.get('outcomePrices')}")
            print()

    else:
        print(__doc__)
