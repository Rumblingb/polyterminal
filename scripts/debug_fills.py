#!/usr/bin/env python3
"""debug fill simulation"""
import sys
sys.path.insert(0, 'skills/clickhouse')
import os
import json
from dotenv import load_dotenv
load_dotenv()
from ch import query

window_ts = 1765644300

tokens, _ = query(f"""
SELECT token_id, side FROM token_registry
WHERE coin='btc' AND window_ts={window_ts}
""")
token_map = {t[0]: t[1] for t in tokens}

events, _ = query(f"""
SELECT
    toUnixTimestamp(ts) as ts,
    event_type,
    asset_id,
    raw
FROM clob_events
WHERE window_ts = {window_ts}
  AND event_type IN ('book', 'last_trade_price')
ORDER BY ts
""")

print(f"Window {window_ts} - debugging fills")
print()

book = {
    'up': {'bid': 0, 'bid_size': 0},
    'down': {'bid': 0, 'bid_size': 0}
}

fills = []

for ts, event_type, asset_id, raw in events:
    side = token_map.get(asset_id)
    if not side:
        continue

    data = json.loads(raw)
    if isinstance(data, list):
        data = data[0] if data else {}

    elapsed = ts - window_ts

    if event_type == 'book':
        bids = data.get('bids', [])
        if bids:
            best = max(bids, key=lambda x: float(x['price']))
            book[side]['bid'] = float(best['price'])
            book[side]['bid_size'] = float(best['size'])

    elif event_type == 'last_trade_price':
        trade_side = data.get('side', '')
        if trade_side != 'SELL':
            continue

        price = float(data.get('price', 0))
        size = float(data.get('size', 0))
        our_bid = book[side]['bid']

        fills.append({
            'elapsed': elapsed,
            'side': side,
            'trade_price': price,
            'trade_size': size,
            'our_bid': our_bid,
            'queue': book[side]['bid_size']
        })

print(f"Total SELL trades: {len(fills)}")

good_fills = [f for f in fills if f['our_bid'] > 0 and f['trade_price'] <= f['our_bid'] + 0.02]
print(f"Trades at or near our bid: {len(good_fills)}")
print()

for f in good_fills[:20]:
    print(f"  {f['elapsed']:>4.0f}s {f['side']:>4} | trade={f['trade_price']:.3f} x {f['trade_size']:.0f} | "
          f"our_bid={f['our_bid']:.3f} queue={f['queue']:.0f}")

print()
up_fills = [f for f in good_fills if f['side'] == 'up']
down_fills = [f for f in good_fills if f['side'] == 'down']

print(f"UP: {len(up_fills)} fills")
if up_fills:
    up_prices = [f['our_bid'] for f in up_fills]
    print(f"  Avg bid: {sum(up_prices)/len(up_prices):.3f}")
    print(f"  Min bid: {min(up_prices):.3f}")
    print(f"  Max bid: {max(up_prices):.3f}")

print(f"DOWN: {len(down_fills)} fills")
if down_fills:
    down_prices = [f['our_bid'] for f in down_fills]
    print(f"  Avg bid: {sum(down_prices)/len(down_prices):.3f}")
    print(f"  Min bid: {min(down_prices):.3f}")
    print(f"  Max bid: {max(down_prices):.3f}")

if up_fills and down_fills:
    combined = sum(up_prices)/len(up_prices) + sum(down_prices)/len(down_prices)
    print(f"\nCombined avg bid: {combined:.3f}")
    print(f"This means if ALL fills executed, edge = {(1-combined)*100:.1f}%")
    print()
    print("BUT - the backtest is getting fills at varying prices")
    print("When one side dumps (price drops), we get cheap fills")
    print("This creates the illusion of high edge")
