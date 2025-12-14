#!/usr/bin/env python3
"""
Market maker backtest - simulates posting bids and getting filled

Algorithm:
1. Post at best_bid on both UP and DOWN
2. Track queue position (we're at back of queue)
3. When SELL trades come through, they deplete queue
4. If SELL size > queue ahead of us, we get filled
5. Calculate PnL based on matched fills
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

# strategy params
ORDER_SIZE = 100  # shares per order
MAX_CAPITAL_PER_SIDE = 250  # $
MIN_EDGE = 0.02  # 2% minimum combined_bid edge to post


def simulate_window(window_ts: int, verbose: bool = False):
    """simulate market making for one window"""

    # get tokens
    tokens, _ = query(f"""
    SELECT token_id, side FROM token_registry
    WHERE coin='btc' AND window_ts={window_ts}
    """)
    token_map = {t[0]: t[1] for t in tokens}

    if len(token_map) < 2:
        return None

    # get all events in chronological order
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

    # state
    book = {
        'up': {'bid': 0, 'bid_size': 0, 'ask': 1, 'ask_size': 0},
        'down': {'bid': 0, 'bid_size': 0, 'ask': 1, 'ask_size': 0}
    }

    # our position
    position = {
        'up': {'qty': 0, 'cost': 0, 'queue_ahead': 0, 'our_bid': 0},
        'down': {'qty': 0, 'cost': 0, 'queue_ahead': 0, 'our_bid': 0}
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
            # update book state
            bids = data.get('bids', [])
            asks = data.get('asks', [])

            if bids:
                best = max(bids, key=lambda x: float(x['price']))
                book[side]['bid'] = float(best['price'])
                book[side]['bid_size'] = float(best['size'])
            if asks:
                best = min(asks, key=lambda x: float(x['price']))
                book[side]['ask'] = float(best['price'])
                book[side]['ask_size'] = float(best['size'])

            # check if we should post/update order
            combined_bid = book['up']['bid'] + book['down']['bid']
            edge = 1 - combined_bid

            if edge >= MIN_EDGE:
                current_cost = position[side]['cost']
                if current_cost < MAX_CAPITAL_PER_SIDE:
                    # post order at best_bid, back of queue
                    position[side]['queue_ahead'] = book[side]['bid_size']
                    position[side]['our_bid'] = book[side]['bid']

        elif event_type == 'last_trade_price':
            # check if this is a SELL (fills our bid)
            trade_side = data.get('side', '')
            if trade_side != 'SELL':
                continue

            price = float(data.get('price', 0))
            size = float(data.get('size', 0))
            our_bid = position[side]['our_bid']

            # skip if we don't have an order or trade is above our bid
            if our_bid <= 0 or price > our_bid + 0.02:
                continue

            # deplete queue
            queue = position[side]['queue_ahead']
            if size > queue:
                # we get filled!
                fill_size = min(size - queue, ORDER_SIZE)
                fill_cost = fill_size * our_bid

                if position[side]['cost'] + fill_cost <= MAX_CAPITAL_PER_SIDE:
                    position[side]['qty'] += fill_size
                    position[side]['cost'] += fill_cost
                    position[side]['queue_ahead'] = 0  # reset to back of queue

                    fills.append({
                        'elapsed': elapsed,
                        'side': side,
                        'price': our_bid,
                        'size': fill_size,
                        'cost': fill_cost
                    })

                    if verbose:
                        print(f"  {elapsed:>4.0f}s FILL {side:>4} {fill_size:.0f} @ {our_bid:.3f}")
            else:
                position[side]['queue_ahead'] = queue - size

    # calculate results
    up_qty = position['up']['qty']
    down_qty = position['down']['qty']
    up_cost = position['up']['cost']
    down_cost = position['down']['cost']

    if up_qty > 0 and down_qty > 0:
        matched = min(up_qty, down_qty)
        up_avg = up_cost / up_qty
        down_avg = down_cost / down_qty
        combined = up_avg + down_avg
        edge = 1 - combined
        pnl = matched * edge

        return {
            'window_ts': window_ts,
            'up_qty': up_qty,
            'down_qty': down_qty,
            'up_cost': up_cost,
            'down_cost': down_cost,
            'up_avg': up_avg,
            'down_avg': down_avg,
            'combined': combined,
            'matched': matched,
            'edge': edge,
            'pnl': pnl,
            'fills': len(fills)
        }

    return None


def run_backtest(max_windows: int = 0):
    """run backtest across all windows"""

    windows, _ = query("""
    SELECT DISTINCT window_ts FROM token_registry
    WHERE coin='btc'
    ORDER BY window_ts DESC
    """)

    if max_windows > 0:
        windows = windows[:max_windows]

    print(f"Backtesting {len(windows)} windows...")
    print(f"Params: ORDER_SIZE={ORDER_SIZE}, MAX_CAPITAL=${MAX_CAPITAL_PER_SIDE}/side, MIN_EDGE={MIN_EDGE*100}%")
    print()

    results = []
    for (window_ts,) in windows:
        result = simulate_window(window_ts, verbose=False)
        if result:
            results.append(result)
            dt = datetime.utcfromtimestamp(window_ts)
            print(f"{dt.strftime('%m/%d %H:%M')} | "
                  f"up={result['up_qty']:>4.0f} dn={result['down_qty']:>4.0f} | "
                  f"combined={result['combined']:.3f} | "
                  f"matched={result['matched']:>4.0f} | "
                  f"pnl=${result['pnl']:>+6.2f}")

    if results:
        print()
        print("="*60)
        print("SUMMARY")
        print("="*60)

        total_pnl = sum(r['pnl'] for r in results)
        total_matched = sum(r['matched'] for r in results)
        total_capital = sum(r['up_cost'] + r['down_cost'] for r in results)
        avg_edge = sum(r['edge'] for r in results) / len(results)
        wins = sum(1 for r in results if r['pnl'] > 0)

        print(f"Windows with fills: {len(results)} / {len(windows)}")
        print(f"Total matched: {total_matched:.0f} shares")
        print(f"Average edge: {avg_edge*100:.1f}%")
        print(f"Total PnL: ${total_pnl:.2f}")
        print(f"Total capital used: ${total_capital:.0f}")
        print(f"ROI: {total_pnl/total_capital*100:.1f}%")
        print(f"Win rate: {wins}/{len(results)} ({wins/len(results)*100:.0f}%)")

        hours = len(windows) * 0.25
        print()
        print(f"Per hour: ${total_pnl/hours:.2f}")
        print(f"Per day: ${total_pnl/hours*24:.2f}")

    return results


if __name__ == '__main__':
    import sys
    max_windows = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    run_backtest(max_windows)
