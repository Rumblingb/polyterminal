#!/usr/bin/env python3
"""
Market maker backtest v2 - FIXED version

Key insight: when DOWN is cheap (0.14), UP is expensive (0.85)
You can't have both cheap at the same time (arbitrage would be instant)

Strategy must be:
1. Post bids when combined_bid < 0.98 (edge exists)
2. Only fill when trade sweeps our queue
3. Track ACTUAL combined at time of each fill pair

Two approaches:
A) Post at fixed price (e.g., 48c both sides) - wait for both to fill
B) Post at best_bid dynamically - fills at varying prices
"""
import sys
sys.path.insert(0, 'skills/clickhouse')
import os
import json
from collections import defaultdict
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()
from ch import query

# params
ORDER_SIZE = 100
MAX_CAPITAL = 250
FIXED_BID = 0.48  # post at 48c both sides (strategy A)


def simulate_fixed_price(window_ts: int, verbose: bool = False):
    """
    Strategy A: Post at fixed 48c on both sides
    Only fill if trade price <= 48c
    """
    tokens, _ = query(f"""
    SELECT token_id, side FROM token_registry
    WHERE coin='btc' AND window_ts={window_ts}
    """)
    token_map = {t[0]: t[1] for t in tokens}
    if len(token_map) < 2:
        return None

    events, _ = query(f"""
    SELECT
        toUnixTimestamp(ts) as ts,
        event_type,
        asset_id,
        raw
    FROM clob_events
    WHERE window_ts = {window_ts}
      AND event_type = 'last_trade_price'
    ORDER BY ts
    """)

    fills = {'up': [], 'down': []}

    for ts, event_type, asset_id, raw in events:
        side = token_map.get(asset_id)
        if not side:
            continue

        data = json.loads(raw)
        if isinstance(data, list):
            data = data[0] if data else {}

        trade_side = data.get('side', '')
        if trade_side != 'SELL':
            continue

        price = float(data.get('price', 0))
        size = float(data.get('size', 0))
        elapsed = ts - window_ts

        # only fill if trade is at or below our 48c bid
        if price <= FIXED_BID + 0.01:
            current_cost = sum(f['cost'] for f in fills[side])
            if current_cost < MAX_CAPITAL:
                fill_size = min(size, ORDER_SIZE, (MAX_CAPITAL - current_cost) / FIXED_BID)
                fills[side].append({
                    'elapsed': elapsed,
                    'price': FIXED_BID,
                    'size': fill_size,
                    'cost': fill_size * FIXED_BID
                })
                if verbose:
                    print(f"  {elapsed:>4.0f}s FILL {side} {fill_size:.0f} @ {FIXED_BID:.2f}")

    # calculate result
    up_qty = sum(f['size'] for f in fills['up'])
    down_qty = sum(f['size'] for f in fills['down'])

    if up_qty > 0 and down_qty > 0:
        # with fixed price, combined = 0.48 + 0.48 = 0.96
        combined = FIXED_BID * 2
        edge = 1 - combined
        matched = min(up_qty, down_qty)
        pnl = matched * edge

        return {
            'window_ts': window_ts,
            'up_qty': up_qty,
            'down_qty': down_qty,
            'combined': combined,
            'edge': edge,
            'matched': matched,
            'pnl': pnl
        }

    return None


def simulate_dynamic(window_ts: int, min_edge: float = 0.02, verbose: bool = False):
    """
    Strategy B: Post at best_bid when combined_bid < 1-min_edge
    Track actual fill prices
    """
    tokens, _ = query(f"""
    SELECT token_id, side FROM token_registry
    WHERE coin='btc' AND window_ts={window_ts}
    """)
    token_map = {t[0]: t[1] for t in tokens}
    if len(token_map) < 2:
        return None

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

    book = {'up': {'bid': 0.50}, 'down': {'bid': 0.50}}
    fills = {'up': [], 'down': []}

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

        elif event_type == 'last_trade_price':
            trade_side = data.get('side', '')
            if trade_side != 'SELL':
                continue

            price = float(data.get('price', 0))
            size = float(data.get('size', 0))

            # check if combined_bid has edge
            combined_bid = book['up']['bid'] + book['down']['bid']
            our_bid = book[side]['bid']

            if combined_bid >= (1 - min_edge):
                continue  # no edge, don't fill

            if price > our_bid + 0.02:
                continue  # trade above our bid

            current_cost = sum(f['cost'] for f in fills[side])
            if current_cost >= MAX_CAPITAL:
                continue

            fill_size = min(size, ORDER_SIZE, (MAX_CAPITAL - current_cost) / our_bid)
            fills[side].append({
                'elapsed': elapsed,
                'price': our_bid,
                'size': fill_size,
                'cost': fill_size * our_bid,
                'combined_bid': combined_bid
            })

            if verbose:
                print(f"  {elapsed:>4.0f}s FILL {side} {fill_size:.0f} @ {our_bid:.2f} (comb={combined_bid:.3f})")

    # calculate - use weighted average of fill prices
    up_qty = sum(f['size'] for f in fills['up'])
    down_qty = sum(f['size'] for f in fills['down'])
    up_cost = sum(f['cost'] for f in fills['up'])
    down_cost = sum(f['cost'] for f in fills['down'])

    if up_qty > 0 and down_qty > 0:
        up_avg = up_cost / up_qty
        down_avg = down_cost / down_qty
        combined = up_avg + down_avg
        edge = 1 - combined
        matched = min(up_qty, down_qty)
        pnl = matched * edge

        return {
            'window_ts': window_ts,
            'up_qty': up_qty,
            'down_qty': down_qty,
            'up_avg': up_avg,
            'down_avg': down_avg,
            'combined': combined,
            'edge': edge,
            'matched': matched,
            'pnl': pnl
        }

    return None


def run_backtest(strategy='fixed', max_windows=20):
    windows, _ = query("""
    SELECT DISTINCT window_ts FROM token_registry
    WHERE coin='btc'
    ORDER BY window_ts DESC
    """)

    if max_windows > 0:
        windows = windows[:max_windows]

    print(f"Backtest: {strategy.upper()} strategy")
    print(f"Windows: {len(windows)}")
    print()

    results = []
    for (window_ts,) in windows:
        if strategy == 'fixed':
            result = simulate_fixed_price(window_ts)
        else:
            result = simulate_dynamic(window_ts)

        if result:
            results.append(result)
            dt = datetime.utcfromtimestamp(window_ts)
            print(f"{dt.strftime('%m/%d %H:%M')} | "
                  f"up={result['up_qty']:>4.0f} dn={result['down_qty']:>4.0f} | "
                  f"combined={result['combined']:.3f} edge={result['edge']*100:>+4.1f}% | "
                  f"matched={result['matched']:>4.0f} pnl=${result['pnl']:>+6.2f}")

    if results:
        print()
        print("="*60)
        total_pnl = sum(r['pnl'] for r in results)
        total_matched = sum(r['matched'] for r in results)
        avg_edge = sum(r['edge'] for r in results) / len(results)
        wins = sum(1 for r in results if r['pnl'] > 0)
        hours = len(windows) * 0.25

        print(f"Windows with fills: {len(results)}/{len(windows)}")
        print(f"Total matched: {total_matched:.0f}")
        print(f"Avg edge: {avg_edge*100:.1f}%")
        print(f"PnL: ${total_pnl:.2f}")
        print(f"Win rate: {wins}/{len(results)} ({wins/len(results)*100:.0f}%)")
        print(f"Per day: ${total_pnl/hours*24:.2f}")

    return results


if __name__ == '__main__':
    print("="*60)
    print("FIXED PRICE STRATEGY (48c both sides)")
    print("="*60)
    run_backtest('fixed', 20)

    print()
    print("="*60)
    print("DYNAMIC STRATEGY (post at best_bid when edge > 2%)")
    print("="*60)
    run_backtest('dynamic', 20)
