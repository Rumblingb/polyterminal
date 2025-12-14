#!/usr/bin/env python3
"""
mm - market making utilities for btc 15m updown markets

usage:
    from mm import post_grid, post_window

    # post grid for a specific window
    results = post_window(window_ts, price_levels=[0.44, 0.46, 0.48], size=108)

    # post grid for next N windows
    results = post_grid(windows_ahead=4)
"""
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

# load from project root
env_path = Path(__file__).parent.parent.parent / '.env'
load_dotenv(env_path)

from .orders import place_orders, get_orders, cancel_all

GAMMA_API = 'https://gamma-api.polymarket.com'

# default strategy
DEFAULT_LEVELS = [0.44, 0.46, 0.48]
DEFAULT_SIZE = 108


def get_window_tokens(window_ts: int) -> Optional[dict]:
    """fetch UP/DOWN token IDs for a window"""
    slug = f'btc-updown-15m-{window_ts}'
    try:
        resp = requests.get(f'{GAMMA_API}/markets?slug={slug}', timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not data:
            return None
        market = data[0] if isinstance(data, list) else data
        tokens = market.get('clobTokenIds', [])
        if isinstance(tokens, str):
            tokens = json.loads(tokens)
        if len(tokens) >= 2:
            return {
                'up': tokens[0],
                'down': tokens[1],
                'question': market.get('question', ''),
                'condition_id': market.get('conditionId', '')
            }
    except:
        pass
    return None


def post_window(window_ts: int, price_levels: list = None, size: int = None) -> dict:
    """
    post limit orders for a single window

    args:
        window_ts: window timestamp
        price_levels: list of prices (default: [0.44, 0.46, 0.48])
        size: shares per level (default: 108)

    returns dict with results
    """
    levels = price_levels or DEFAULT_LEVELS
    order_size = size or DEFAULT_SIZE

    tokens = get_window_tokens(window_ts)
    if not tokens:
        return {'success': False, 'error': 'market not found'}

    orders = []
    for side, token in [('UP', tokens['up']), ('DOWN', tokens['down'])]:
        for price in levels:
            orders.append({
                'token_id': token,
                'price': price,
                'size': order_size,
                'side': 'BUY',
                'label': f'{side}@{price}'
            })

    results = place_orders(orders)

    success = sum(1 for r in results if r.get('success'))
    failed = len(results) - success

    dt = datetime.utcfromtimestamp(window_ts)
    cost = order_size * sum(levels) * 2

    return {
        'window_ts': window_ts,
        'window_time': dt.strftime('%H:%M UTC'),
        'success_count': success,
        'failed_count': failed,
        'cost': cost,
        'orders': results
    }


def post_grid(windows_ahead: int = 4, price_levels: list = None,
              size: int = None, skip_existing: bool = True) -> list:
    """
    post limit orders for multiple upcoming windows

    args:
        windows_ahead: number of windows to post (default: 4)
        price_levels: list of prices
        size: shares per level
        skip_existing: skip windows that already have orders

    returns list of results per window
    """
    now = int(time.time())
    current_window = now - (now % 900)

    # get existing orders to avoid duplicates
    existing_tokens = set()
    if skip_existing:
        for o in get_orders():
            existing_tokens.add(o.get('asset_id'))

    results = []
    for i in range(1, windows_ahead + 1):
        window_ts = current_window + (i * 900)

        tokens = get_window_tokens(window_ts)
        if not tokens:
            results.append({
                'window_ts': window_ts,
                'success': False,
                'error': 'market not found'
            })
            continue

        # skip if already have orders for this window
        if skip_existing and (tokens['up'] in existing_tokens or tokens['down'] in existing_tokens):
            dt = datetime.utcfromtimestamp(window_ts)
            results.append({
                'window_ts': window_ts,
                'window_time': dt.strftime('%H:%M UTC'),
                'skipped': True,
                'reason': 'already has orders'
            })
            continue

        result = post_window(window_ts, price_levels, size)
        results.append(result)

        time.sleep(0.5)

    return results


def get_upcoming_windows(hours: int = 24) -> list:
    """get list of upcoming window timestamps"""
    now = int(time.time())
    current = now - (now % 900)

    windows = []
    for i in range(1, (hours * 4) + 1):
        ts = current + (i * 900)
        dt = datetime.utcfromtimestamp(ts)
        windows.append({
            'ts': ts,
            'time': dt.strftime('%H:%M UTC'),
            'slug': f'btc-updown-15m-{ts}'
        })

    return windows


if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print(__doc__)
        print("\nCommands:")
        print("  python mm.py windows [--hours N]")
        print("  python mm.py post <window_ts>")
        print("  python mm.py grid [--count N]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == 'windows':
        hours = 1
        for i, arg in enumerate(sys.argv):
            if arg == '--hours' and i + 1 < len(sys.argv):
                hours = int(sys.argv[i + 1])

        windows = get_upcoming_windows(hours)
        print(f"Upcoming windows ({len(windows)}):\n")
        for w in windows:
            print(f"  {w['time']} - {w['ts']}")

    elif cmd == 'post' and len(sys.argv) > 2:
        window_ts = int(sys.argv[2])
        print(f"Posting orders for window {window_ts}...")
        result = post_window(window_ts)

        if result.get('success_count', 0) > 0:
            print(f"\n✓ {result['success_count']} orders placed")
            print(f"  Window: {result['window_time']}")
            print(f"  Cost: ~${result['cost']:.0f}")
        else:
            print(f"\n✗ Failed: {result.get('error', 'unknown')}")

    elif cmd == 'grid':
        count = 4
        for i, arg in enumerate(sys.argv):
            if arg == '--count' and i + 1 < len(sys.argv):
                count = int(sys.argv[i + 1])

        print(f"Posting grid for {count} windows...")
        results = post_grid(windows_ahead=count)

        success = sum(1 for r in results if r.get('success_count', 0) > 0)
        print(f"\nDone: {success}/{len(results)} windows")

        for r in results:
            if r.get('skipped'):
                print(f"  {r['window_time']}: skipped ({r['reason']})")
            elif r.get('success_count', 0) > 0:
                print(f"  {r['window_time']}: ✓ {r['success_count']} orders")
            else:
                print(f"  {r.get('window_ts', '?')}: ✗ {r.get('error', 'failed')}")

    else:
        print(__doc__)
