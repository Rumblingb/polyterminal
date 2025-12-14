#!/usr/bin/env python3
"""
orders - place and manage orders on polymarket CLOB

usage:
    from orders import place_order, get_orders, cancel_order

    # place single order
    order_id = place_order(token_id, price=0.45, size=100, side='BUY')

    # get open orders
    orders = get_orders()

    # cancel
    cancel_order(order_id)
    cancel_all()
"""
import os
import time
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# load from project root
env_path = Path(__file__).parent.parent.parent / '.env'
load_dotenv(env_path)

CLOB_HOST = 'https://clob.polymarket.com'


def get_client():
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds

    client = ClobClient(
        host=CLOB_HOST,
        key=os.environ.get('PRIVATE_KEY'),
        chain_id=137,
        signature_type=1,
        funder=os.environ.get('POLY_ADDRESS')
    )
    creds = ApiCreds(
        api_key=os.environ.get('POLY_API_KEY'),
        api_secret=os.environ.get('POLY_API_SECRET'),
        api_passphrase=os.environ.get('POLY_PASSPHRASE')
    )
    client.set_api_creds(creds)
    return client


def place_order(token_id: str, price: float, size: int, side: str = 'BUY',
                order_type: str = 'GTC') -> Optional[dict]:
    """
    place a single limit order

    args:
        token_id: clob token id
        price: limit price (0.01-0.99)
        size: number of shares
        side: 'BUY' or 'SELL'
        order_type: 'GTC', 'GTD', 'FOK', 'FAK'

    returns dict with order_id and status, or None on failure
    """
    from py_clob_client.clob_types import OrderArgs, OrderType
    from py_clob_client.order_builder.constants import BUY, SELL

    client = get_client()

    order_side = BUY if side.upper() == 'BUY' else SELL
    otype = getattr(OrderType, order_type.upper())

    try:
        order = client.create_order(OrderArgs(
            price=price,
            size=size,
            side=order_side,
            token_id=token_id
        ))
        resp = client.post_order(order, otype)

        if resp.get('success'):
            return {
                'order_id': resp.get('orderID'),
                'status': resp.get('status', 'live'),
                'success': True
            }
        else:
            return {
                'success': False,
                'error': resp.get('errorMsg', 'unknown error')
            }
    except Exception as e:
        return {'success': False, 'error': str(e)}


def place_orders(orders: list) -> list:
    """
    place multiple orders (batch, max 15 per batch)

    args:
        orders: list of dicts with token_id, price, size, side

    returns list of results
    """
    from py_clob_client.clob_types import OrderArgs, OrderType, PostOrdersArgs
    from py_clob_client.order_builder.constants import BUY, SELL

    client = get_client()
    results = []

    for i in range(0, len(orders), 15):
        batch = orders[i:i+15]

        post_args = []
        for o in batch:
            order_side = BUY if o.get('side', 'BUY').upper() == 'BUY' else SELL
            order = client.create_order(OrderArgs(
                price=o['price'],
                size=o['size'],
                side=order_side,
                token_id=o['token_id']
            ))
            post_args.append(PostOrdersArgs(
                order=order,
                orderType=OrderType.GTC
            ))

        try:
            resp = client.post_orders(post_args)
            for j, r in enumerate(resp):
                results.append({
                    'token_id': batch[j]['token_id'],
                    'price': batch[j]['price'],
                    'size': batch[j]['size'],
                    'success': r.get('success', False),
                    'order_id': r.get('orderID'),
                    'error': r.get('errorMsg') if not r.get('success') else None
                })
        except Exception as e:
            for o in batch:
                results.append({
                    'token_id': o['token_id'],
                    'price': o['price'],
                    'success': False,
                    'error': str(e)
                })

        time.sleep(0.3)

    return results


def get_orders(market: Optional[str] = None, asset_id: Optional[str] = None) -> list:
    """
    get open orders

    args:
        market: filter by market condition_id
        asset_id: filter by token_id

    returns list of order dicts
    """
    client = get_client()

    params = {}
    if market:
        params['market'] = market
    if asset_id:
        params['asset_id'] = asset_id

    return client.get_orders(**params) if params else client.get_orders()


def cancel_order(order_id: str) -> bool:
    """cancel a single order by id"""
    client = get_client()
    try:
        resp = client.cancel(order_id)
        return order_id in resp.get('canceled', [])
    except:
        return False


def cancel_orders(order_ids: list) -> dict:
    """cancel multiple orders"""
    client = get_client()
    try:
        return client.cancel_orders(order_ids)
    except Exception as e:
        return {'error': str(e)}


def cancel_all() -> dict:
    """cancel all open orders"""
    client = get_client()
    try:
        return client.cancel_all()
    except Exception as e:
        return {'error': str(e)}


if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print(__doc__)
        print("\nCommands:")
        print("  python orders.py list")
        print("  python orders.py cancel <order_id>")
        print("  python orders.py cancel-all")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == 'list':
        orders = get_orders()
        print(f"Open orders: {len(orders)}\n")
        for o in orders:
            print(f"{o.get('side')} {o.get('outcome')} @ {o.get('price')} | {o.get('original_size')} shares | {o.get('status')}")

    elif cmd == 'cancel' and len(sys.argv) > 2:
        order_id = sys.argv[2]
        if cancel_order(order_id):
            print(f"Cancelled: {order_id}")
        else:
            print(f"Failed to cancel: {order_id}")

    elif cmd == 'cancel-all':
        resp = cancel_all()
        print(f"Cancelled: {len(resp.get('canceled', []))} orders")

    else:
        print(__doc__)
