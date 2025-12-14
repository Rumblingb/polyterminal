#!/usr/bin/env python3
"""
Backtest assuming we're FIRST in queue (posted at market open)
No queue simulation - if price hits our level, we fill
"""
import sys
sys.path.insert(0, 'skills/clickhouse')
import json
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()
from ch import query

PRICE_LEVELS = [0.44, 0.46, 0.48]
ORDER_SIZE = 11  # ~$5 at 0.45


def simulate_window(window_ts: int):
    tokens, _ = query(f"""
    SELECT token_id, side FROM token_registry
    WHERE coin='btc' AND window_ts={window_ts}
    """)
    token_map = {t[0]: t[1] for t in tokens}
    if len(token_map) < 2:
        return None

    events, _ = query(f"""
    SELECT asset_id, raw FROM clob_events
    WHERE window_ts = {window_ts}
      AND event_type = 'last_trade_price'
    """)

    # track fills at each level
    fills = {
        'up': {p: 0 for p in PRICE_LEVELS},
        'down': {p: 0 for p in PRICE_LEVELS}
    }

    for asset_id, raw in events:
        data = json.loads(raw)
        if isinstance(data, list):
            data = data[0] if data else {}

        if data.get('side') != 'SELL':
            continue

        side = token_map.get(data.get('asset_id', asset_id))
        if not side:
            continue

        price = float(data.get('price', 0))
        size = float(data.get('size', 0))

        # check each level - if trade at or below our bid, we fill
        for level in PRICE_LEVELS:
            if price <= level + 0.01:  # small tolerance
                remaining = ORDER_SIZE - fills[side][level]
                if remaining > 0:
                    fills[side][level] += min(size, remaining)

    # calculate results per level
    results = []
    for level in PRICE_LEVELS:
        up_qty = fills['up'][level]
        down_qty = fills['down'][level]

        if up_qty > 0 or down_qty > 0:
            matched = min(up_qty, down_qty)
            edge = 1 - level * 2
            pnl = matched * edge
            results.append({
                'level': level,
                'up': up_qty,
                'down': down_qty,
                'matched': matched,
                'edge': edge,
                'pnl': pnl
            })

    return {
        'window_ts': window_ts,
        'fills': fills,
        'results': results,
        'total_pnl': sum(r['pnl'] for r in results)
    }


def run_backtest():
    windows, _ = query("""
    SELECT DISTINCT window_ts FROM token_registry
    WHERE coin='btc'
    ORDER BY window_ts DESC
    LIMIT 100
    """)

    print(f"Backtest: FIRST IN QUEUE simulation")
    print(f"Levels: {PRICE_LEVELS}")
    print(f"Order size: {ORDER_SIZE} shares (~${ORDER_SIZE * 0.46:.0f})")
    print(f"Windows: {len(windows)}")
    print()

    # aggregate by level
    level_stats = {p: {'fills': 0, 'matched': 0, 'pnl': 0, 'windows': 0} for p in PRICE_LEVELS}
    total_pnl = 0
    both_filled = 0

    for (window_ts,) in windows:
        result = simulate_window(window_ts)
        if not result:
            continue

        dt = datetime.utcfromtimestamp(window_ts)

        window_has_both = False
        for r in result['results']:
            level_stats[r['level']]['windows'] += 1
            if r['up'] > 0:
                level_stats[r['level']]['fills'] += 1
            if r['down'] > 0:
                level_stats[r['level']]['fills'] += 1
            level_stats[r['level']]['matched'] += r['matched']
            level_stats[r['level']]['pnl'] += r['pnl']

            if r['matched'] > 0:
                window_has_both = True

        if window_has_both:
            both_filled += 1

        total_pnl += result['total_pnl']

        if result['total_pnl'] > 0:
            fills_str = ' | '.join([f"{r['level']}: {r['matched']:.0f}@{r['edge']*100:.0f}%=${r['pnl']:.2f}"
                                    for r in result['results'] if r['matched'] > 0])
            print(f"{dt.strftime('%m/%d %H:%M')} | {fills_str} | total=${result['total_pnl']:.2f}")

    print()
    print("=" * 70)
    print("BY PRICE LEVEL")
    print("=" * 70)
    print(f"{'Level':>6} | {'Edge':>5} | {'Matched':>10} | {'PnL':>10} | {'Windows':>8}")
    print("-" * 70)

    for level in PRICE_LEVELS:
        s = level_stats[level]
        edge = (1 - level * 2) * 100
        print(f"{level:>6.2f} | {edge:>4.0f}% | {s['matched']:>10.0f} | ${s['pnl']:>9.2f} | {s['windows']:>8}")

    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Windows analyzed: {len(windows)}")
    print(f"Windows with both sides filled: {both_filled} ({both_filled/len(windows)*100:.0f}%)")
    print(f"Total PnL: ${total_pnl:.2f}")
    print(f"Per window: ${total_pnl/len(windows):.2f}")

    hours = len(windows) * 0.25
    print(f"Per day: ${total_pnl/hours*24:.2f}")


if __name__ == '__main__':
    run_backtest()
