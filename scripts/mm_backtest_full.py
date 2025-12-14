#!/usr/bin/env python3
"""
Full MM backtest with sensitivity analysis

Tests:
1. Different queue positions (0%, 25%, 50%, 75% of queue ahead)
2. Volume capture at each price level
3. PnL under different scenarios
"""
import sys
sys.path.insert(0, 'skills/clickhouse')
import json
from collections import defaultdict
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()
from ch import query

PRICE_LEVELS = [0.44, 0.46, 0.48]
ORDER_SIZE = 11  # ~$5
QUEUE_SCENARIOS = [0, 0.25, 0.50, 0.75]  # fraction of queue ahead of us


def analyze_window(window_ts: int):
    """get all sell volume and queue data for a window"""
    tokens, _ = query(f"""
    SELECT token_id, side FROM token_registry
    WHERE coin='btc' AND window_ts={window_ts}
    """)
    token_map = {t[0]: t[1] for t in tokens}
    if len(token_map) < 2:
        return None

    # get book snapshots for queue depth
    books, _ = query(f"""
    SELECT asset_id, raw FROM clob_events
    WHERE window_ts = {window_ts} AND event_type = 'book'
    LIMIT 50
    """)

    # estimate queue at each level (use first book snapshot)
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

    # get all SELL trades
    events, _ = query(f"""
    SELECT asset_id, raw FROM clob_events
    WHERE window_ts = {window_ts} AND event_type = 'last_trade_price'
    """)

    # volume at each price level
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

        # bucket by which levels this trade would hit
        for level in PRICE_LEVELS:
            if price <= level + 0.01:
                sell_volume[side][level] += size

    return {
        'window_ts': window_ts,
        'queue_depth': queue_depth,
        'sell_volume': sell_volume
    }


def simulate_fills(data: dict, queue_fraction: float):
    """simulate fills given queue position"""
    results = {}

    for level in PRICE_LEVELS:
        up_queue = data['queue_depth'].get('up', {}).get(level, 5000)
        down_queue = data['queue_depth'].get('down', {}).get(level, 5000)

        up_vol = data['sell_volume']['up'][level]
        down_vol = data['sell_volume']['down'][level]

        # queue ahead of us
        up_ahead = up_queue * queue_fraction
        down_ahead = down_queue * queue_fraction

        # overflow after queue clears
        up_overflow = max(0, up_vol - up_ahead)
        down_overflow = max(0, down_vol - down_ahead)

        # we get filled from overflow (capped at order size)
        up_fill = min(ORDER_SIZE, up_overflow)
        down_fill = min(ORDER_SIZE, down_overflow)

        matched = min(up_fill, down_fill)
        edge = 1 - level * 2
        pnl = matched * edge

        results[level] = {
            'up_vol': up_vol,
            'down_vol': down_vol,
            'up_queue': up_queue,
            'down_queue': down_queue,
            'up_fill': up_fill,
            'down_fill': down_fill,
            'matched': matched,
            'edge': edge,
            'pnl': pnl
        }

    return results


def run_analysis():
    windows, _ = query("""
    SELECT DISTINCT window_ts FROM token_registry
    WHERE coin='btc'
    ORDER BY window_ts DESC
    LIMIT 100
    """)

    print(f"MM Backtest - Full Analysis")
    print(f"Levels: {PRICE_LEVELS}")
    print(f"Order size: {ORDER_SIZE} shares")
    print(f"Queue scenarios: {[f'{q*100:.0f}%' for q in QUEUE_SCENARIOS]}")
    print(f"Windows: {len(windows)}")
    print()

    # collect all window data
    all_data = []
    for (window_ts,) in windows:
        data = analyze_window(window_ts)
        if data:
            all_data.append(data)

    print(f"Valid windows: {len(all_data)}")
    print()

    # 1. VOLUME ANALYSIS
    print("=" * 70)
    print("1. SELL VOLUME AT EACH PRICE LEVEL")
    print("=" * 70)

    for level in PRICE_LEVELS:
        up_vols = [d['sell_volume']['up'][level] for d in all_data]
        down_vols = [d['sell_volume']['down'][level] for d in all_data]

        up_windows = sum(1 for v in up_vols if v > 0)
        down_windows = sum(1 for v in down_vols if v > 0)
        both_windows = sum(1 for u, d in zip(up_vols, down_vols) if u > 0 and d > 0)

        print(f"\nLevel {level} (edge={((1-level*2)*100):.0f}%):")
        print(f"  UP:   {up_windows}/{len(all_data)} windows ({up_windows/len(all_data)*100:.0f}%), "
              f"avg vol={sum(up_vols)/len(up_vols):.0f}, total={sum(up_vols):.0f}")
        print(f"  DOWN: {down_windows}/{len(all_data)} windows ({down_windows/len(all_data)*100:.0f}%), "
              f"avg vol={sum(down_vols)/len(down_vols):.0f}, total={sum(down_vols):.0f}")
        print(f"  BOTH: {both_windows}/{len(all_data)} windows ({both_windows/len(all_data)*100:.0f}%)")

    # 2. QUEUE DEPTH ANALYSIS
    print()
    print("=" * 70)
    print("2. QUEUE DEPTH AT EACH LEVEL")
    print("=" * 70)

    for level in PRICE_LEVELS:
        up_queues = [d['queue_depth'].get('up', {}).get(level, 0) for d in all_data]
        down_queues = [d['queue_depth'].get('down', {}).get(level, 0) for d in all_data]

        up_queues = [q for q in up_queues if q > 0]
        down_queues = [q for q in down_queues if q > 0]

        if up_queues and down_queues:
            print(f"\nLevel {level}:")
            print(f"  UP:   avg={sum(up_queues)/len(up_queues):.0f}, "
                  f"min={min(up_queues):.0f}, max={max(up_queues):.0f}")
            print(f"  DOWN: avg={sum(down_queues)/len(down_queues):.0f}, "
                  f"min={min(down_queues):.0f}, max={max(down_queues):.0f}")

    # 3. SENSITIVITY ANALYSIS
    print()
    print("=" * 70)
    print("3. PNL BY QUEUE POSITION")
    print("=" * 70)
    print()
    print(f"{'Queue Ahead':>12} | ", end='')
    for level in PRICE_LEVELS:
        print(f"  {level} ({(1-level*2)*100:.0f}%)  |", end='')
    print(f"{'TOTAL':>10}")
    print("-" * 70)

    for queue_frac in QUEUE_SCENARIOS:
        total_pnl = 0
        level_pnls = {}

        for level in PRICE_LEVELS:
            level_pnl = 0
            for data in all_data:
                result = simulate_fills(data, queue_frac)
                level_pnl += result[level]['pnl']
            level_pnls[level] = level_pnl
            total_pnl += level_pnl

        print(f"{queue_frac*100:>10.0f}%  | ", end='')
        for level in PRICE_LEVELS:
            print(f"  ${level_pnls[level]:>7.2f}  |", end='')
        print(f" ${total_pnl:>8.2f}")

    # 4. DETAILED RESULTS FOR BEST SCENARIO
    print()
    print("=" * 70)
    print("4. WINDOW-BY-WINDOW (0% queue ahead - best case)")
    print("=" * 70)

    best_results = []
    for data in all_data:
        result = simulate_fills(data, 0)
        total_pnl = sum(r['pnl'] for r in result.values())
        if total_pnl > 0:
            best_results.append({
                'ts': data['window_ts'],
                'result': result,
                'total_pnl': total_pnl
            })

    for br in best_results[:20]:
        dt = datetime.utcfromtimestamp(br['ts'])
        details = ' | '.join([f"{l}:{br['result'][l]['matched']:.0f}"
                              for l in PRICE_LEVELS if br['result'][l]['matched'] > 0])
        print(f"{dt.strftime('%m/%d %H:%M')} | {details} | ${br['total_pnl']:.2f}")

    # 5. SUMMARY
    print()
    print("=" * 70)
    print("5. SUMMARY")
    print("=" * 70)

    hours = len(all_data) * 0.25

    for queue_frac in QUEUE_SCENARIOS:
        total_pnl = 0
        windows_with_pnl = 0

        for data in all_data:
            result = simulate_fills(data, queue_frac)
            window_pnl = sum(r['pnl'] for r in result.values())
            total_pnl += window_pnl
            if window_pnl > 0:
                windows_with_pnl += 1

        daily = total_pnl / hours * 24
        print(f"Queue {queue_frac*100:>3.0f}% ahead: ${total_pnl:>7.2f} total, "
              f"{windows_with_pnl}/{len(all_data)} profitable, "
              f"${daily:.2f}/day")


if __name__ == '__main__':
    run_analysis()
