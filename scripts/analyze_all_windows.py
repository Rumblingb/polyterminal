#!/usr/bin/env python3
"""
Comprehensive analysis of all window data
- Trade volume by time of day
- Fill rates at different price levels
- Queue depths
- Both-sides fill probability
"""
import sys
sys.path.insert(0, 'skills/clickhouse')
import json
from collections import defaultdict
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()
from ch import query


def analyze_windows():
    # get all windows
    windows, _ = query("""
    SELECT DISTINCT window_ts FROM token_registry
    WHERE coin='btc'
    ORDER BY window_ts DESC
    LIMIT 100
    """)

    print(f"Analyzing {len(windows)} windows...")
    print()

    # aggregate stats
    by_hour = defaultdict(lambda: {'windows': 0, 'up_vol': 0, 'down_vol': 0, 'both_fill': 0})
    price_hits = defaultdict(lambda: {'up': 0, 'down': 0, 'windows': 0})  # price -> hits
    all_results = []

    for (window_ts,) in windows:
        # get tokens
        tokens, _ = query(f"""
        SELECT token_id, side FROM token_registry
        WHERE coin='btc' AND window_ts={window_ts}
        """)
        token_map = {t[0]: t[1] for t in tokens}
        if len(token_map) < 2:
            continue

        # get all SELL trades
        events, _ = query(f"""
        SELECT asset_id, raw FROM clob_events
        WHERE window_ts = {window_ts}
          AND event_type = 'last_trade_price'
        """)

        up_sells = defaultdict(float)  # price -> volume
        down_sells = defaultdict(float)

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

            # bucket by price (round to 0.02)
            price_bucket = round(price * 50) / 50

            if side == 'up':
                up_sells[price_bucket] += size
            else:
                down_sells[price_bucket] += size

        # analyze this window
        dt = datetime.utcfromtimestamp(window_ts)
        hour = dt.hour

        # check fills at each price level
        target_prices = [0.40, 0.42, 0.44, 0.46, 0.48, 0.50]
        up_total = sum(up_sells.values())
        down_total = sum(down_sells.values())

        window_result = {
            'ts': window_ts,
            'hour': hour,
            'up_vol': up_total,
            'down_vol': down_total,
            'up_low': sum(v for p, v in up_sells.items() if p <= 0.48),
            'down_low': sum(v for p, v in down_sells.items() if p <= 0.48),
        }

        # track price level hits
        for p in target_prices:
            # check if any sells at or below this price
            up_at_p = sum(v for pr, v in up_sells.items() if pr <= p)
            down_at_p = sum(v for pr, v in down_sells.items() if pr <= p)
            if up_at_p > 0:
                price_hits[p]['up'] += 1
            if down_at_p > 0:
                price_hits[p]['down'] += 1

        price_hits[0.48]['windows'] += 1

        by_hour[hour]['windows'] += 1
        by_hour[hour]['up_vol'] += up_total
        by_hour[hour]['down_vol'] += down_total
        if window_result['up_low'] > 0 and window_result['down_low'] > 0:
            by_hour[hour]['both_fill'] += 1

        all_results.append(window_result)

    # print results
    print("=" * 70)
    print("SELL VOLUME BY HOUR (UTC)")
    print("=" * 70)
    print(f"{'Hour':>4} | {'Windows':>8} | {'UP Vol':>10} | {'DOWN Vol':>10} | {'Both Fill':>10}")
    print("-" * 70)

    for hour in range(24):
        stats = by_hour[hour]
        if stats['windows'] > 0:
            both_pct = stats['both_fill'] / stats['windows'] * 100
            print(f"{hour:>4} | {stats['windows']:>8} | {stats['up_vol']:>10.0f} | "
                  f"{stats['down_vol']:>10.0f} | {stats['both_fill']:>3}/{stats['windows']:<3} ({both_pct:>3.0f}%)")

    print()
    print("=" * 70)
    print("PRICE LEVEL HIT RATES (sells at or below price)")
    print("=" * 70)
    print(f"{'Price':>6} | {'Edge':>5} | {'UP hits':>10} | {'DOWN hits':>10} | {'Both':>10}")
    print("-" * 70)

    total_windows = price_hits[0.48]['windows']
    for p in sorted(price_hits.keys()):
        if p > 0.50:
            continue
        edge = (1 - p * 2) * 100
        up_hits = price_hits[p]['up']
        down_hits = price_hits[p]['down']
        # both = windows where BOTH up and down had sells at this price
        both = sum(1 for r in all_results
                   if sum(v for pr, v in up_sells.items() if pr <= p) > 0
                   and sum(v for pr, v in down_sells.items() if pr <= p) > 0)

        print(f"{p:>6.2f} | {edge:>4.0f}% | {up_hits:>3}/{total_windows:<3} ({up_hits/total_windows*100:>3.0f}%) | "
              f"{down_hits:>3}/{total_windows:<3} ({down_hits/total_windows*100:>3.0f}%) | ~{both}")

    # summary stats
    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)

    total_up_vol = sum(r['up_vol'] for r in all_results)
    total_down_vol = sum(r['down_vol'] for r in all_results)
    total_up_low = sum(r['up_low'] for r in all_results)
    total_down_low = sum(r['down_low'] for r in all_results)

    print(f"Total windows: {len(all_results)}")
    print(f"Total UP SELL volume: {total_up_vol:,.0f}")
    print(f"Total DOWN SELL volume: {total_down_vol:,.0f}")
    print(f"UP volume at <= 0.48: {total_up_low:,.0f} ({total_up_low/total_up_vol*100:.1f}%)")
    print(f"DOWN volume at <= 0.48: {total_down_low:,.0f} ({total_down_low/total_down_vol*100:.1f}%)")

    both_low = sum(1 for r in all_results if r['up_low'] > 0 and r['down_low'] > 0)
    print(f"Windows with BOTH sides <= 0.48: {both_low}/{len(all_results)} ({both_low/len(all_results)*100:.0f}%)")


if __name__ == '__main__':
    analyze_windows()
