#!/usr/bin/env python3
"""
analyze - market analysis utilities

usage:
    python skills/analyze.py windows [--coin btc]
    python skills/analyze.py edge [--coin btc]
    python skills/analyze.py fills <window_ts> [--coin btc]
    python skills/analyze.py depth <window_ts> [--coin btc]
"""
import sys
import json
from collections import defaultdict
from datetime import datetime

from ch import query

def get_windows(coin=None):
    """list all collected windows with stats"""
    sql = '''
    SELECT
        window_ts,
        count(*) as events,
        countIf(event_type='last_trade_price') as trades,
        countIf(event_type='book') as books
    FROM clob_events
    WHERE window_ts > 0
    GROUP BY window_ts
    ORDER BY window_ts DESC
    '''
    rows, _ = query(sql)

    print(f'{"Window":<20} {"Events":>10} {"Trades":>8} {"Books":>8}')
    print('-' * 50)
    for window_ts, events, trades, books in rows:
        dt = datetime.utcfromtimestamp(window_ts).strftime('%m/%d %H:%M UTC')
        print(f'{dt:<20} {events:>10,} {trades:>8,} {books:>8,}')
    print(f'\nTotal: {len(rows)} windows')

def get_edge(coin='btc'):
    """calculate realized edge from fills at bid per window"""
    # get all windows
    windows_q, _ = query('SELECT DISTINCT window_ts FROM clob_events WHERE window_ts > 0')
    windows = [w[0] for w in windows_q]

    results = []
    for window_ts in windows:
        # get tokens
        tq, _ = query(f"SELECT token_id, side FROM token_registry WHERE coin='{coin}' AND window_ts={window_ts}")
        tokens = {tid: side for tid, side in tq}
        if len(tokens) < 2:
            continue

        # get price_change + trades
        rows, _ = query(f'''
        SELECT event_type, asset_id, raw
        FROM clob_events
        WHERE window_ts = {window_ts}
          AND event_type IN ('price_change', 'last_trade_price')
          AND toUnixTimestamp(ts) - {window_ts} BETWEEN 0 AND 540
        ORDER BY ts
        ''')

        best_bid = {'up': 0, 'down': 0}
        at_bid = {'up': [], 'down': []}

        for event_type, asset_id, raw in rows:
            data = json.loads(raw)
            if isinstance(data, list):
                data = data[0] if data else {}

            if event_type == 'price_change':
                for pc in data.get('price_changes', []):
                    side = tokens.get(pc.get('asset_id', ''))
                    if side:
                        bb = float(pc.get('best_bid', 0))
                        if bb > 0:
                            best_bid[side] = bb

            elif event_type == 'last_trade_price':
                side = tokens.get(asset_id)
                if not side or data.get('side') != 'SELL':
                    continue

                price = float(data.get('price', 0))
                size = float(data.get('size', 0))
                bb = best_bid[side]

                if bb > 0 and abs(price - bb) <= 0.02:
                    at_bid[side].append((price, size))

        if at_bid['up'] and at_bid['down']:
            up_vol = sum(s for _, s in at_bid['up'])
            down_vol = sum(s for _, s in at_bid['down'])
            up_avg = sum(p*s for p,s in at_bid['up']) / up_vol
            down_avg = sum(p*s for p,s in at_bid['down']) / down_vol
            edge = 1 - (up_avg + down_avg)

            ts_str = datetime.utcfromtimestamp(window_ts).strftime('%m/%d %H:%M')
            results.append((ts_str, up_avg, down_avg, edge, up_vol, down_vol))

    print(f'{coin.upper()} Edge from fills at bid (±2c)')
    print()
    print(f'{"Window":<12} {"UP":>6} {"DOWN":>6} {"Comb":>6} {"Edge":>7} {"UP vol":>8} {"DN vol":>8}')
    print('-' * 65)

    for ts, up, down, edge, uv, dv in results:
        print(f'{ts:<12} {up:>6.2f} {down:>6.2f} {up+down:>6.2f} {edge*100:>+6.1f}% {uv:>8.0f} {dv:>8.0f}')

    if results:
        avg_edge = sum(e for _,_,_,e,_,_ in results) / len(results)
        pos = sum(1 for _,_,_,e,_,_ in results if e > 0)
        print('-' * 65)
        print(f'Avg edge: {avg_edge*100:+.1f}% | Positive: {pos}/{len(results)} windows')

def get_fills(window_ts, coin='btc'):
    """show SELL trades for a specific window"""
    tq, _ = query(f"SELECT token_id, side FROM token_registry WHERE coin='{coin}' AND window_ts={window_ts}")
    tokens = {tid: side for tid, side in tq}

    rows, _ = query(f'''
    SELECT asset_id, raw, toUnixTimestamp(ts) - {window_ts} as elapsed
    FROM clob_events
    WHERE window_ts = {window_ts}
      AND event_type = 'last_trade_price'
      AND toUnixTimestamp(ts) - {window_ts} BETWEEN 0 AND 540
    ORDER BY ts
    ''')

    sells = {'up': [], 'down': []}
    for asset_id, raw, elapsed in rows:
        side = tokens.get(asset_id)
        if not side:
            continue
        data = json.loads(raw)
        if data.get('side') != 'SELL':
            continue
        price = float(data.get('price', 0))
        size = float(data.get('size', 0))
        sells[side].append((elapsed, price, size))

    dt = datetime.utcfromtimestamp(window_ts).strftime('%m/%d %H:%M UTC')
    print(f'{coin.upper()} SELL trades - {dt}')
    print()

    for side in ['up', 'down']:
        print(f'{side.upper()}: {len(sells[side])} trades')
        total = sum(s for _, _, s in sells[side])
        avg_price = sum(p*s for _, p, s in sells[side]) / total if total > 0 else 0
        print(f'  total: {total:.0f} shares, avg price: {avg_price:.3f}')
        print(f'  first 10:')
        for elapsed, price, size in sells[side][:10]:
            print(f'    {elapsed:>3.0f}s  {price:.2f}  {size:>6.0f}')
        print()

def get_depth(window_ts, coin='btc'):
    """analyze book depth for a window"""
    tq, _ = query(f"SELECT token_id, side FROM token_registry WHERE coin='{coin}' AND window_ts={window_ts}")
    tokens = {tid: side for tid, side in tq}

    rows, _ = query(f'''
    SELECT asset_id, raw
    FROM clob_events
    WHERE window_ts = {window_ts}
      AND event_type = 'book'
      AND toUnixTimestamp(ts) - {window_ts} BETWEEN 0 AND 540
    ''')

    depths = {'up': [], 'down': []}
    for asset_id, raw in rows:
        side = tokens.get(asset_id)
        if not side:
            continue
        data = json.loads(raw)
        if isinstance(data, list):
            data = data[0] if data else {}
        bids = data.get('bids', [])
        if bids:
            best = max(bids, key=lambda x: float(x['price']))
            depths[side].append(float(best['size']))

    dt = datetime.utcfromtimestamp(window_ts).strftime('%m/%d %H:%M UTC')
    print(f'{coin.upper()} Book Depth at Best Bid - {dt}')
    print()

    for side in ['up', 'down']:
        d = depths[side]
        if d:
            print(f'{side.upper()}:')
            print(f'  samples: {len(d)}')
            print(f'  avg: {sum(d)/len(d):.0f}')
            print(f'  min: {min(d):.0f}')
            print(f'  max: {max(d):.0f}')
            print()

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    coin = 'btc'

    # parse --coin flag
    for i, arg in enumerate(sys.argv):
        if arg == '--coin' and i + 1 < len(sys.argv):
            coin = sys.argv[i + 1]

    if cmd == 'windows':
        get_windows(coin)
    elif cmd == 'edge':
        get_edge(coin)
    elif cmd == 'fills' and len(sys.argv) > 2:
        get_fills(int(sys.argv[2]), coin)
    elif cmd == 'depth' and len(sys.argv) > 2:
        get_depth(int(sys.argv[2]), coin)
    else:
        print(__doc__)
