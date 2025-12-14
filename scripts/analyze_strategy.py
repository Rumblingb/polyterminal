#!/usr/bin/env python3
"""analyze the trading strategy against real data"""
import sys
sys.path.insert(0, 'skills/clickhouse')
import os
from dotenv import load_dotenv
load_dotenv()
import json
from ch import query

# pick a recent window with activity
windows_q, _ = query("SELECT DISTINCT window_ts FROM clob_events WHERE window_ts > 0 ORDER BY window_ts DESC LIMIT 10")
window_ts = windows_q[0][0]
print(f"Analyzing window: {window_ts}")

# get token mapping
tokens, _ = query(f"""
SELECT token_id, side FROM token_registry
WHERE coin='btc' AND window_ts={window_ts}
""")
token_map = {t[0]: t[1] for t in tokens}
print(f"Tokens: {len(token_map)} found")

if len(token_map) < 2:
    print("No tokens for this window, trying earlier...")
    window_ts = windows_q[1][0]
    tokens, _ = query(f"""
    SELECT token_id, side FROM token_registry
    WHERE coin='btc' AND window_ts={window_ts}
    """)
    token_map = {t[0]: t[1] for t in tokens}

# get all price_change events for this window, first 60 seconds
events, _ = query(f"""
SELECT ts, asset_id, raw
FROM clob_events
WHERE window_ts = {window_ts}
  AND event_type = 'price_change'
  AND toUnixTimestamp(ts) - {window_ts} BETWEEN 0 AND 60
ORDER BY ts
LIMIT 200
""")

print(f"\nFirst 60 seconds of price changes ({len(events)} events):")
print(f"{'Time':>6}  {'Side':>5}  {'Bid':>6}  {'Ask':>6}  {'AskSz':>6}  {'Comb':>6}")
print("-" * 50)

last_up_ask = 0.50
last_down_ask = 0.50
opportunities = 0

for ts, asset_id, raw in events[:40]:
    data = json.loads(raw)
    elapsed = ts.timestamp() - window_ts

    for pc in data.get('price_changes', []):
        side = token_map.get(pc.get('asset_id'))
        if not side:
            continue

        bid = float(pc.get('best_bid', 0))
        ask = float(pc.get('best_ask', 1))
        ask_size = float(pc.get('best_ask_size', 0))

        if side == 'up':
            last_up_ask = ask
        else:
            last_down_ask = ask

        combined_ask = last_up_ask + last_down_ask
        marker = " ***" if combined_ask < 0.975 else ""
        if combined_ask < 0.975:
            opportunities += 1

        print(f"{elapsed:>5.1f}s  {side:>5}  {bid:.3f}  {ask:.3f}  {ask_size:>5.0f}  {combined_ask:>5.3f}{marker}")

print(f"\nOpportunities with combined < 0.975: {opportunities}")
