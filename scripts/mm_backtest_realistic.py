#!/usr/bin/env python3
"""
Realistic MM backtest including LOSSES from unmatched fills

When only one side fills:
- You hold a 50/50 bet on BTC
- Win: $1 per share (profit = 1 - cost)
- Lose: $0 per share (loss = -cost)
- Expected value = 0.5 - cost (negative if cost > 0.50)
"""
import sys
sys.path.insert(0, 'skills/clickhouse')
import json
import random
from collections import defaultdict
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()
from ch import query

PRICE_LEVELS = [0.44, 0.46, 0.48]
ORDER_SIZE = 11
QUEUE_SCENARIOS = [0, 0.25, 0.50, 0.75]
SIMULATIONS = 1000  # monte carlo for unmatched outcomes


def analyze_window(window_ts: int):
    tokens, _ = query(f"""
    SELECT token_id, side FROM token_registry
    WHERE coin='btc' AND window_ts={window_ts}
    """)
    token_map = {t[0]: t[1] for t in tokens}
    if len(token_map) < 2:
        return None

    books, _ = query(f"""
    SELECT asset_id, raw FROM clob_events
    WHERE window_ts = {window_ts} AND event_type = 'book'
    LIMIT 50
    """)

    queue_depth = {'up': {}, 'down': {}}
    for asset_id, raw in books[:10]:
        data = json.loads(raw)
        if isinstance(data, list):
            data = data[0] if data else {}
        side = token_map.get(asset_id)
        if not side:
            continue
        bids = data.get('bids', [])
        for level in PRICE_LEVELS:
            depth = sum(float(b['size']) for b in bids if float(b['price']) >= level)
            if level not in queue_depth[side] or depth > 0:
                queue_depth[side][level] = depth

    events, _ = query(f"""
    SELECT asset_id, raw FROM clob_events
    WHERE window_ts = {window_ts} AND event_type = 'last_trade_price'
    """)

    sell_volume = {'up': defaultdict(float), 'down': defaultdict(float)}
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
        for level in PRICE_LEVELS:
            if price <= level + 0.01:
                sell_volume[side][level] += size

    return {
        'window_ts': window_ts,
        'queue_depth': queue_depth,
        'sell_volume': sell_volume
    }


def simulate_fills(data: dict, queue_fraction: float):
    results = {}
    for level in PRICE_LEVELS:
        up_queue = data['queue_depth'].get('up', {}).get(level, 3000)
        down_queue = data['queue_depth'].get('down', {}).get(level, 3000)
        up_vol = data['sell_volume']['up'][level]
        down_vol = data['sell_volume']['down'][level]

        up_ahead = up_queue * queue_fraction
        down_ahead = down_queue * queue_fraction

        up_overflow = max(0, up_vol - up_ahead)
        down_overflow = max(0, down_vol - down_ahead)

        up_fill = min(ORDER_SIZE, up_overflow)
        down_fill = min(ORDER_SIZE, down_overflow)

        results[level] = {
            'up_fill': up_fill,
            'down_fill': down_fill,
            'cost_up': up_fill * level,
            'cost_down': down_fill * level
        }
    return results


def calculate_pnl(results: dict, up_wins: bool):
    """
    Calculate PnL given fills and outcome

    up_wins=True: UP pays $1, DOWN pays $0
    up_wins=False: UP pays $0, DOWN pays $1
    """
    total_pnl = 0

    for level, r in results.items():
        up_fill = r['up_fill']
        down_fill = r['down_fill']

        # matched portion - guaranteed profit
        matched = min(up_fill, down_fill)
        edge = 1 - level * 2
        matched_pnl = matched * edge

        # unmatched UP
        unmatched_up = up_fill - matched
        if unmatched_up > 0:
            if up_wins:
                # UP pays $1, we paid level
                unmatched_pnl_up = unmatched_up * (1 - level)
            else:
                # UP pays $0, we lose cost
                unmatched_pnl_up = -unmatched_up * level
        else:
            unmatched_pnl_up = 0

        # unmatched DOWN
        unmatched_down = down_fill - matched
        if unmatched_down > 0:
            if not up_wins:
                # DOWN pays $1, we paid level
                unmatched_pnl_down = unmatched_down * (1 - level)
            else:
                # DOWN pays $0, we lose cost
                unmatched_pnl_down = -unmatched_down * level
        else:
            unmatched_pnl_down = 0

        total_pnl += matched_pnl + unmatched_pnl_up + unmatched_pnl_down

    return total_pnl


def run_analysis():
    windows, _ = query("""
    SELECT DISTINCT window_ts FROM token_registry
    WHERE coin='btc'
    ORDER BY window_ts DESC
    LIMIT 100
    """)

    print(f"Realistic MM Backtest (including losses)")
    print(f"Levels: {PRICE_LEVELS}")
    print(f"Order size: {ORDER_SIZE} shares")
    print(f"Monte Carlo simulations: {SIMULATIONS}")
    print()

    all_data = []
    for (window_ts,) in windows:
        data = analyze_window(window_ts)
        if data:
            all_data.append(data)

    print(f"Windows: {len(all_data)}")
    print()

    # analyze each queue scenario
    print("=" * 80)
    print("PNL ANALYSIS (with unmatched losses)")
    print("=" * 80)
    print()

    for queue_frac in QUEUE_SCENARIOS:
        print(f"--- Queue {queue_frac*100:.0f}% ahead ---")

        # collect all window results
        all_results = []
        for data in all_data:
            result = simulate_fills(data, queue_frac)
            all_results.append(result)

        # monte carlo for unmatched outcomes
        pnl_samples = []
        for _ in range(SIMULATIONS):
            total_pnl = 0
            for result in all_results:
                up_wins = random.random() < 0.5  # 50/50 outcome
                total_pnl += calculate_pnl(result, up_wins)
            pnl_samples.append(total_pnl)

        pnl_samples.sort()

        avg_pnl = sum(pnl_samples) / len(pnl_samples)
        worst_5 = pnl_samples[int(len(pnl_samples) * 0.05)]
        best_5 = pnl_samples[int(len(pnl_samples) * 0.95)]

        # also calculate deterministic matched-only
        matched_only = 0
        total_unmatched_up = 0
        total_unmatched_down = 0

        for result in all_results:
            for level, r in result.items():
                matched = min(r['up_fill'], r['down_fill'])
                edge = 1 - level * 2
                matched_only += matched * edge
                total_unmatched_up += r['up_fill'] - matched
                total_unmatched_down += r['down_fill'] - matched

        hours = len(all_data) * 0.25

        print(f"  Matched-only PnL:  ${matched_only:>8.2f} (${matched_only/hours*24:.2f}/day)")
        print(f"  Unmatched UP:      {total_unmatched_up:>8.0f} shares")
        print(f"  Unmatched DOWN:    {total_unmatched_down:>8.0f} shares")
        print(f"  Expected PnL:      ${avg_pnl:>8.2f} (${avg_pnl/hours*24:.2f}/day)")
        print(f"  5th percentile:    ${worst_5:>8.2f} (${worst_5/hours*24:.2f}/day)")
        print(f"  95th percentile:   ${best_5:>8.2f} (${best_5/hours*24:.2f}/day)")
        print()

    # detailed breakdown
    print("=" * 80)
    print("BREAKDOWN BY LEVEL (0% queue ahead)")
    print("=" * 80)

    for level in PRICE_LEVELS:
        total_up = 0
        total_down = 0
        total_matched = 0

        for data in all_data:
            result = simulate_fills(data, 0)
            r = result[level]
            total_up += r['up_fill']
            total_down += r['down_fill']
            total_matched += min(r['up_fill'], r['down_fill'])

        unmatched_up = total_up - total_matched
        unmatched_down = total_down - total_matched

        matched_pnl = total_matched * (1 - level * 2)
        # expected value of unmatched = (0.5 - level) per share
        unmatched_ev = (unmatched_up + unmatched_down) * (0.5 - level)

        print(f"\nLevel {level} (edge={(1-level*2)*100:.0f}%):")
        print(f"  Total UP fills:    {total_up:>6.0f} shares")
        print(f"  Total DOWN fills:  {total_down:>6.0f} shares")
        print(f"  Matched:           {total_matched:>6.0f} shares -> ${matched_pnl:.2f}")
        print(f"  Unmatched UP:      {unmatched_up:>6.0f} shares")
        print(f"  Unmatched DOWN:    {unmatched_down:>6.0f} shares")
        print(f"  Unmatched EV:      ${unmatched_ev:.2f} (per share EV: ${0.5-level:.3f})")


if __name__ == '__main__':
    run_analysis()
