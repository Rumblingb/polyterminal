#!/usr/bin/env python3
"""
subgraph - Goldsky subgraph queries for on-chain trade data

source of truth for wallet trades. indexes polymarket smart contracts.

usage:
    from subgraph import get_wallet_trades, get_recent_trades

    # get trades for a wallet
    trades = get_wallet_trades('0x6031b6eed1c97e853c6e0f03ad3ce3529351f96d', limit=100)

    # get all recent trades
    recent = get_recent_trades(limit=50)
"""
import requests
from typing import Optional

SUBGRAPH_URL = "https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/orderbook-subgraph/0.0.1/gn"

def _query(graphql: str) -> dict:
    """execute graphql query against goldsky"""
    resp = requests.post(SUBGRAPH_URL, json={"query": graphql}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if 'errors' in data:
        raise Exception(f"GraphQL error: {data['errors']}")
    return data['data']

def get_wallet_trades(wallet: str, limit: int = 100, skip: int = 0,
                      role: str = 'both', since_ts: Optional[int] = None) -> list:
    """
    fetch trades for a wallet from subgraph

    args:
        wallet: ethereum address (0x...)
        limit: max results (capped at 1000 by subgraph)
        skip: pagination offset
        role: 'maker', 'taker', or 'both'
        since_ts: unix timestamp to filter trades after

    returns list of raw orderFilledEvent dicts with:
        id, timestamp, transactionHash, maker, taker,
        makerAssetId, takerAssetId, makerAmountFilled, takerAmountFilled, fee
    """
    wallet = wallet.lower()

    # build where clause
    if role == 'maker':
        where = f'maker: "{wallet}"'
    elif role == 'taker':
        where = f'taker: "{wallet}"'
    else:
        where = f'or: [{{maker: "{wallet}"}}, {{taker: "{wallet}"}}]'

    if since_ts:
        where += f', timestamp_gt: "{since_ts}"'

    query = f'''
    {{
      orderFilledEvents(
        first: {min(limit, 1000)},
        skip: {skip},
        orderBy: timestamp,
        orderDirection: desc,
        where: {{ {where} }}
      ) {{
        id
        timestamp
        transactionHash
        orderHash
        maker
        taker
        makerAssetId
        takerAssetId
        makerAmountFilled
        takerAmountFilled
        fee
      }}
    }}
    '''

    data = _query(query)
    return data['orderFilledEvents']

def get_wallet_trades_all(wallet: str, role: str = 'both',
                          since_ts: Optional[int] = None) -> list:
    """
    fetch ALL trades for a wallet (paginated)
    warning: can be slow for very active wallets
    """
    all_trades = []
    skip = 0

    while True:
        batch = get_wallet_trades(wallet, limit=1000, skip=skip,
                                  role=role, since_ts=since_ts)
        if not batch:
            break
        all_trades.extend(batch)
        skip += len(batch)

        if len(batch) < 1000:
            break

    return all_trades

def get_recent_trades(limit: int = 100) -> list:
    """fetch most recent trades across all wallets"""
    query = f'''
    {{
      orderFilledEvents(
        first: {min(limit, 1000)},
        orderBy: timestamp,
        orderDirection: desc
      ) {{
        id
        timestamp
        transactionHash
        maker
        taker
        makerAssetId
        takerAssetId
        makerAmountFilled
        takerAmountFilled
        fee
      }}
    }}
    '''

    data = _query(query)
    return data['orderFilledEvents']

def get_trades_by_token(token_id: str, limit: int = 100) -> list:
    """fetch trades for a specific token/outcome"""
    query = f'''
    {{
      orderFilledEvents(
        first: {min(limit, 1000)},
        orderBy: timestamp,
        orderDirection: desc,
        where: {{
          or: [
            {{makerAssetId: "{token_id}"}},
            {{takerAssetId: "{token_id}"}}
          ]
        }}
      ) {{
        id
        timestamp
        transactionHash
        maker
        taker
        makerAssetId
        takerAssetId
        makerAmountFilled
        takerAmountFilled
        fee
      }}
    }}
    '''

    data = _query(query)
    return data['orderFilledEvents']

def decode_trade(event: dict, wallet: str) -> dict:
    """
    decode raw orderFilledEvent into readable trade

    returns dict with:
        tx, timestamp, role (MAKER/TAKER), side (BUY/SELL),
        shares, price, usdc, fee, token_id
    """
    wallet = wallet.lower()
    is_maker = event['maker'].lower() == wallet
    maker_is_usdc = event['makerAssetId'] == "0"
    taker_is_usdc = event['takerAssetId'] == "0"

    if is_maker:
        role = "MAKER"
        if maker_is_usdc:
            side = "BUY"
            usdc = int(event['makerAmountFilled']) / 1e6
            shares = int(event['takerAmountFilled']) / 1e6
            token_id = event['takerAssetId']
        else:
            side = "SELL"
            shares = int(event['makerAmountFilled']) / 1e6
            usdc = int(event['takerAmountFilled']) / 1e6
            token_id = event['makerAssetId']
    else:
        role = "TAKER"
        if taker_is_usdc:
            side = "BUY"
            usdc = int(event['takerAmountFilled']) / 1e6
            shares = int(event['makerAmountFilled']) / 1e6
            token_id = event['makerAssetId']
        else:
            side = "SELL"
            shares = int(event['takerAmountFilled']) / 1e6
            usdc = int(event['makerAmountFilled']) / 1e6
            token_id = event['takerAssetId']

    price = usdc / shares if shares > 0 else 0
    fee = int(event.get('fee', '0')) / 1e6

    return {
        'tx': event['transactionHash'],
        'timestamp': int(event['timestamp']),
        'role': role,
        'side': side,
        'shares': shares,
        'price': price,
        'usdc': usdc,
        'fee': fee,
        'token_id': token_id
    }

def count_wallet_trades(wallet: str, since_ts: Optional[int] = None) -> dict:
    """quick count of maker/taker trades for a wallet"""
    wallet = wallet.lower()

    ts_filter = f', timestamp_gt: "{since_ts}"' if since_ts else ''

    # count maker
    q1 = f'''
    {{
      orderFilledEvents(first: 1000, where: {{maker: "{wallet}"{ts_filter}}}) {{
        id
      }}
    }}
    '''
    maker_count = len(_query(q1)['orderFilledEvents'])

    # count taker
    q2 = f'''
    {{
      orderFilledEvents(first: 1000, where: {{taker: "{wallet}"{ts_filter}}}) {{
        id
      }}
    }}
    '''
    taker_count = len(_query(q2)['orderFilledEvents'])

    return {
        'maker': maker_count,
        'taker': taker_count,
        'total': maker_count + taker_count,
        'note': 'capped at 1000 per role' if maker_count >= 1000 or taker_count >= 1000 else None
    }


if __name__ == '__main__':
    import sys
    import json

    if len(sys.argv) < 2:
        print(__doc__)
        print("\nCommands:")
        print("  python subgraph.py trades <wallet> [--limit N] [--role maker|taker]")
        print("  python subgraph.py count <wallet>")
        print("  python subgraph.py recent [--limit N]")
        print("  python subgraph.py token <token_id> [--limit N]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == 'trades' and len(sys.argv) > 2:
        wallet = sys.argv[2]
        limit = 20
        role = 'both'

        for i, arg in enumerate(sys.argv):
            if arg == '--limit' and i + 1 < len(sys.argv):
                limit = int(sys.argv[i + 1])
            if arg == '--role' and i + 1 < len(sys.argv):
                role = sys.argv[i + 1]

        trades = get_wallet_trades(wallet, limit=limit, role=role)
        print(f"Found {len(trades)} trades for {wallet[:10]}...\n")

        for t in trades[:10]:
            decoded = decode_trade(t, wallet)
            print(f"{decoded['role']} {decoded['side']} {decoded['shares']:.2f} @ ${decoded['price']:.4f} = ${decoded['usdc']:.2f}")
            print(f"  tx: {decoded['tx'][:20]}...")
            print()

    elif cmd == 'count' and len(sys.argv) > 2:
        wallet = sys.argv[2]
        counts = count_wallet_trades(wallet)
        print(f"Trade counts for {wallet[:10]}...")
        print(f"  Maker: {counts['maker']}")
        print(f"  Taker: {counts['taker']}")
        print(f"  Total: {counts['total']}")
        if counts['note']:
            print(f"  Note: {counts['note']}")

    elif cmd == 'recent':
        limit = 20
        for i, arg in enumerate(sys.argv):
            if arg == '--limit' and i + 1 < len(sys.argv):
                limit = int(sys.argv[i + 1])

        trades = get_recent_trades(limit=limit)
        print(f"Recent {len(trades)} trades:\n")
        for t in trades[:10]:
            print(f"maker: {t['maker'][:10]}... taker: {t['taker'][:10]}...")
            print(f"  tx: {t['transactionHash'][:20]}...")
            print()

    elif cmd == 'token' and len(sys.argv) > 2:
        token_id = sys.argv[2]
        limit = 20
        for i, arg in enumerate(sys.argv):
            if arg == '--limit' and i + 1 < len(sys.argv):
                limit = int(sys.argv[i + 1])

        trades = get_trades_by_token(token_id, limit=limit)
        print(f"Found {len(trades)} trades for token {token_id[:20]}...\n")
        for t in trades[:10]:
            print(json.dumps(t, indent=2))

    else:
        print(__doc__)
