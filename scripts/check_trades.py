#!/usr/bin/env python3
"""check trade size distribution"""
import sys
sys.path.insert(0, 'skills/clickhouse')
import json
from dotenv import load_dotenv
load_dotenv()
from ch import query

# get recent window
windows, _ = query("SELECT DISTINCT window_ts FROM token_registry WHERE coin='btc' ORDER BY window_ts DESC LIMIT 1")
window_ts = windows[0][0]

# get tokens
tokens, _ = query(f"SELECT token_id, side FROM token_registry WHERE coin='btc' AND window_ts={window_ts}")
token_map = {t[0]: t[1] for t in tokens}

# get SELL trades
events, _ = query(f'''
SELECT raw FROM clob_events
WHERE window_ts = {window_ts}
  AND event_type = 'last_trade_price'
LIMIT 500
''')

sizes = []
prices = []
tokens_seen = []
for (raw,) in events:
    data = json.loads(raw)
    if isinstance(data, list):
        data = data[0] if data else {}
    if data.get('side') == 'SELL':
        sizes.append(float(data.get('size', 0)))
        prices.append(float(data.get('price', 0)))
        asset_id = data.get('asset_id', '')
        side = token_map.get(asset_id, 'unknown')
        tokens_seen.append(side)

print(f'Window: {window_ts}')
print(f'SELL trade count: {len(sizes)}')
print(f'Total volume: {sum(sizes):.0f}')
print(f'Avg size: {sum(sizes)/len(sizes):.1f}' if sizes else 'N/A')
print(f'Max size: {max(sizes):.0f}' if sizes else 'N/A')
print()

# distribution
print('Size distribution:')
buckets = [0, 10, 50, 100, 500, 1000, 5000]
for i in range(len(buckets)-1):
    count = sum(1 for s in sizes if buckets[i] <= s < buckets[i+1])
    vol = sum(s for s in sizes if buckets[i] <= s < buckets[i+1])
    print(f'  {buckets[i]:>5}-{buckets[i+1]:<5}: {count:>3} trades, {vol:>8.0f} volume')
print(f'  {buckets[-1]:>5}+    : {sum(1 for s in sizes if s >= buckets[-1]):>3} trades')

print()
print('Price distribution (full range):')
price_buckets = [0, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 1.0]
for i in range(len(price_buckets)-1):
    count = sum(1 for p in prices if price_buckets[i] <= p < price_buckets[i+1])
    vol = sum(sizes[j] for j, p in enumerate(prices) if price_buckets[i] <= p < price_buckets[i+1])
    if count > 0:
        print(f'  {price_buckets[i]:.2f}-{price_buckets[i+1]:.2f}: {count:>3} trades, {vol:>6.0f} vol')

print()
print('Sample trades:')
for i in range(min(10, len(prices))):
    print(f'  price={prices[i]:.3f}, size={sizes[i]:.0f}')

# analyze by token side
print()
print('BY TOKEN SIDE:')
for side in ['up', 'down']:
    side_prices = [p for p, t in zip(prices, tokens_seen) if t == side]
    side_sizes = [s for s, t in zip(sizes, tokens_seen) if t == side]
    if side_prices:
        print(f'\n{side.upper()}:')
        print(f'  trades: {len(side_prices)}, volume: {sum(side_sizes):.0f}')
        print(f'  price range: {min(side_prices):.3f} - {max(side_prices):.3f}')

        # low price sells (our target)
        low = [p for p in side_prices if p <= 0.50]
        print(f'  sells at <= 0.50: {len(low)} ({len(low)/len(side_prices)*100:.0f}%)')

        # very low price
        very_low = [(p, s) for p, s, t in zip(prices, sizes, tokens_seen) if t == side and p <= 0.48]
        if very_low:
            print(f'  sells at <= 0.48:')
            for p, s in very_low[:5]:
                print(f'    {p:.3f} x {s:.0f}')
