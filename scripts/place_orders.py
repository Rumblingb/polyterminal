#!/usr/bin/env python3
"""
Place limit orders for upcoming BTC 15m windows

Usage:
  python place_orders.py           # dry run (no real orders)
  python place_orders.py --live    # real orders
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime
from dotenv import load_dotenv

import aiohttp

load_dotenv()

GAMMA_API = 'https://gamma-api.polymarket.com'
CLOB_HOST = 'https://clob.polymarket.com'

# strategy
PRICE_LEVELS = [0.44, 0.46, 0.48]
ORDER_SIZE = 108  # shares per level

# how many windows ahead to post
WINDOWS_AHEAD = 4

LIVE_MODE = '--live' in sys.argv


async def fetch_market(session, ts):
    slug = f'btc-updown-15m-{ts}'
    try:
        async with session.get(f'{GAMMA_API}/markets?slug={slug}') as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            if not data:
                return None
            market = data[0] if isinstance(data, list) else data
            tokens = market.get('clobTokenIds', [])
            if isinstance(tokens, str):
                tokens = json.loads(tokens)
            if len(tokens) >= 2:
                return {
                    'ts': ts,
                    'up_token': tokens[0],
                    'down_token': tokens[1],
                    'question': market.get('question', '')
                }
    except Exception as e:
        print(f'  error fetching {slug}: {e}')
    return None


def create_clob_client():
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds

    client = ClobClient(
        host=CLOB_HOST,
        key=os.getenv('PRIVATE_KEY'),
        chain_id=137,
        signature_type=2,  # browser wallet proxy
        funder=os.getenv('POLY_ADDRESS')
    )
    creds = ApiCreds(
        api_key=os.getenv('POLY_API_KEY'),
        api_secret=os.getenv('POLY_API_SECRET'),
        api_passphrase=os.getenv('POLY_PASSPHRASE')
    )
    client.set_api_creds(creds)
    return client


def place_orders_batch(client, orders_to_place):
    """place multiple orders in batch (max 15 per batch)"""
    from py_clob_client.clob_types import OrderArgs, OrderType, PostOrdersArgs
    from py_clob_client.order_builder.constants import BUY

    results = {'success': 0, 'failed': 0}

    # batch in groups of 15
    for i in range(0, len(orders_to_place), 15):
        batch = orders_to_place[i:i+15]

        post_args = []
        for o in batch:
            order = client.create_order(OrderArgs(
                price=o['price'],
                size=o['size'],
                side=BUY,
                token_id=o['token_id']
            ))
            post_args.append(PostOrdersArgs(
                order=order,
                orderType=OrderType.GTC
            ))

        try:
            resp = client.post_orders(post_args)

            # resp is list of results
            for j, r in enumerate(resp):
                if r.get('success'):
                    order_id = r.get('orderID', '')[:8]
                    print(f'    ✓ {batch[j]["side"]} @ {batch[j]["price"]}: {batch[j]["size"]} (id: {order_id}...)')
                    results['success'] += 1
                else:
                    print(f'    ✗ {batch[j]["side"]} @ {batch[j]["price"]}: {r.get("errorMsg", "unknown error")}')
                    results['failed'] += 1

        except Exception as e:
            print(f'    ✗ batch error: {e}')
            results['failed'] += len(batch)

        time.sleep(0.5)  # rate limit between batches

    return results


async def main():
    print('=' * 60)
    print(f'BTC 15m Order Placer | {"LIVE" if LIVE_MODE else "DRY RUN"}')
    print(f'Levels: {PRICE_LEVELS}')
    print(f'Size: {ORDER_SIZE} shares/level')
    print(f'Windows: {WINDOWS_AHEAD}')
    print('=' * 60)
    print()

    # calc windows
    now = int(time.time())
    current_window = now - (now % 900)

    windows = []
    for i in range(1, WINDOWS_AHEAD + 1):
        windows.append(current_window + (i * 900))

    # fetch markets
    print('Fetching markets...')
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_market(session, ts) for ts in windows]
        markets = await asyncio.gather(*tasks)

    valid_markets = [m for m in markets if m]
    print(f'Found {len(valid_markets)}/{len(windows)} markets\n')

    if not valid_markets:
        print('No markets found!')
        return

    # build order list
    orders_to_place = []
    for m in valid_markets:
        for side, token in [('UP', m['up_token']), ('DOWN', m['down_token'])]:
            for price in PRICE_LEVELS:
                orders_to_place.append({
                    'token_id': token,
                    'price': price,
                    'size': ORDER_SIZE,
                    'side': side,
                    'window_ts': m['ts']
                })

    # show summary
    total_cost = 0
    for m in valid_markets:
        dt = datetime.utcfromtimestamp(m['ts'])
        cost = ORDER_SIZE * sum(PRICE_LEVELS) * 2  # both sides
        total_cost += cost
        print(f'{dt.strftime("%H:%M")} UTC: ~${cost:.0f}')

    print(f'\nTotal: ~${total_cost:.0f} across {len(valid_markets)} windows')
    print(f'Orders: {len(orders_to_place)} total\n')

    if not LIVE_MODE:
        print('DRY RUN - no orders placed')
        print('Run with --live to place real orders')
        print()

        # show simulated orders
        for m in valid_markets:
            dt = datetime.utcfromtimestamp(m['ts'])
            print(f'{dt.strftime("%H:%M")} UTC:')
            for side, token in [('UP', m['up_token']), ('DOWN', m['down_token'])]:
                for price in PRICE_LEVELS:
                    print(f'  [DRY] BUY {side} @ {price}: {ORDER_SIZE} shares')
            print()
        return

    # live mode
    print('Connecting to CLOB...')
    try:
        client = create_clob_client()
        print('Connected!\n')
    except Exception as e:
        print(f'Failed to connect: {e}')
        return

    # place by window for clarity
    for m in valid_markets:
        dt = datetime.utcfromtimestamp(m['ts'])
        print(f'{dt.strftime("%H:%M")} UTC:')

        window_orders = [o for o in orders_to_place if o['window_ts'] == m['ts']]
        results = place_orders_batch(client, window_orders)
        print(f'  → {results["success"]} placed, {results["failed"]} failed\n')

    print('=' * 60)
    print('Done!')
    print('=' * 60)


if __name__ == '__main__':
    asyncio.run(main())
