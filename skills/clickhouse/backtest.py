#!/usr/bin/env python3
"""
backtest - simulate market making with queue position

usage:
    python skills/backtest.py [--coin btc] [--capital 50] [--windows 0]

options:
    --coin      coin to backtest (btc, eth, sol, xrp) [default: btc]
    --capital   capital per side in USD [default: 50]
    --windows   limit to N most recent windows, 0 = all [default: 0]
"""
import sys
import json
from collections import defaultdict
from datetime import datetime

from ch import query

def run_backtest(coin='btc', capital_per_side=50, max_windows=0):
    """
    queue-based backtest using real book depth
    posts at best_bid, back of queue, fills when large SELLs sweep through
    """
    # get windows
    windows_q, _ = query('SELECT DISTINCT window_ts FROM clob_events WHERE window_ts > 0 ORDER BY window_ts DESC')
    windows = [w[0] for w in windows_q]
    if max_windows > 0:
        windows = windows[:max_windows]
    windows = sorted(windows)

    results = []

    for window_ts in windows:
        # get tokens
        tq, _ = query(f"SELECT token_id, side FROM token_registry WHERE coin='{coin}' AND window_ts={window_ts}")
        tokens = {tid: side for tid, side in tq}

        if len(tokens) < 2:
            continue

        # get book + trade events
        rows, _ = query(f'''
        SELECT event_type, asset_id, raw
        FROM clob_events
        WHERE window_ts = {window_ts}
          AND event_type IN ('book', 'last_trade_price')
          AND toUnixTimestamp(ts) - {window_ts} BETWEEN 0 AND 540
        ORDER BY ts
        ''')

        # state
        depth_ahead = {'up': 0, 'down': 0}
        best_bid = {'up': 0, 'down': 0}
        my_capital = {'up': capital_per_side, 'down': capital_per_side}
        my_fills = {'up': [], 'down': []}

        for event_type, asset_id, raw in rows:
            data = json.loads(raw)
            if isinstance(data, list):
                data = data[0] if data else {}

            side = tokens.get(asset_id)
            if not side:
                continue

            if event_type == 'book':
                bids = data.get('bids', [])
                if bids:
                    best = max(bids, key=lambda x: float(x['price']))
                    depth_ahead[side] = float(best['size'])
                    best_bid[side] = float(best['price'])

            elif event_type == 'last_trade_price':
                if data.get('side') != 'SELL':
                    continue

                trade_price = float(data.get('price', 0))
                size = float(data.get('size', 0))
                our_bid = best_bid[side]

                if my_capital[side] < 5 or our_bid <= 0:
                    continue

                # trade must reach our bid level
                if trade_price > our_bid + 0.02:
                    continue

                # does trade sweep through to us?
                ahead = depth_ahead[side]
                if size > ahead:
                    available = size - ahead
                    max_shares = my_capital[side] / our_bid
                    fill = min(available, max_shares)

                    if fill > 0:
                        cost = fill * our_bid  # fill at OUR bid, not trade price
                        my_capital[side] -= cost
                        my_fills[side].append((our_bid, fill))
                        depth_ahead[side] = 0
                else:
                    depth_ahead[side] = max(0, ahead - size)

        # calculate pnl
        up_shares = sum(s for _, s in my_fills['up'])
        down_shares = sum(s for _, s in my_fills['down'])

        if up_shares > 0 and down_shares > 0:
            up_cost = sum(p*s for p,s in my_fills['up'])
            down_cost = sum(p*s for p,s in my_fills['down'])
            matched = min(up_shares, down_shares)
            edge = 1 - (up_cost/up_shares + down_cost/down_shares)
            pnl = matched * edge
            capital_used = up_cost + down_cost

            results.append({
                'window_ts': window_ts,
                'up_shares': up_shares,
                'down_shares': down_shares,
                'matched': matched,
                'edge': edge,
                'pnl': pnl,
                'capital': capital_used
            })

    # output
    print(f'{coin.upper()} Backtest: ${capital_per_side}/side, back of queue')
    print()
    print(f'{"Window":<8} {"UP":>6} {"DOWN":>6} {"Match":>6} {"Edge":>7} {"PnL":>9} {"Capital":>8}')
    print('-' * 60)

    for r in results:
        ts = datetime.utcfromtimestamp(r['window_ts']).strftime('%H:%M')
        print(f'{ts:<8} {r["up_shares"]:>6.0f} {r["down_shares"]:>6.0f} {r["matched"]:>6.0f} '
              f'{r["edge"]*100:>+6.1f}% ${r["pnl"]:>8.2f} ${r["capital"]:>7.0f}')

    if results:
        total_pnl = sum(r['pnl'] for r in results)
        total_cap = sum(r['capital'] for r in results)
        total_match = sum(r['matched'] for r in results)
        avg_edge = sum(r['edge'] for r in results) / len(results)
        wins = sum(1 for r in results if r['pnl'] > 0)
        hours = len(results) * 0.25  # 15 min per window

        print('-' * 60)
        print(f'Windows: {len(results)} | Matched: {total_match:.0f} | Avg Edge: {avg_edge*100:+.1f}%')
        print(f'PnL: ${total_pnl:.2f} | Capital: ${total_cap:.0f} | ROI: {total_pnl/total_cap*100:.1f}%')
        print(f'Win rate: {wins}/{len(results)} ({wins/len(results)*100:.0f}%)')
        print()
        print(f'Per hour:  ${total_pnl / hours:.2f}')
        print(f'Per day:   ${total_pnl / hours * 24:.2f}')

    return results

def sensitivity(coin='btc'):
    """run backtest across different capital levels"""
    print(f'{coin.upper()} Sensitivity Analysis')
    print()
    print(f'{"Capital":>10} {"PnL":>10} {"ROI":>8} {"Daily":>10}')
    print('-' * 45)

    for cap in [25, 50, 100, 200]:
        results = run_backtest(coin, cap, max_windows=0)
        if not results:
            continue

        total_pnl = sum(r['pnl'] for r in results)
        total_cap = sum(r['capital'] for r in results)
        hours = len(results) * 0.25
        daily = total_pnl / hours * 24
        roi = total_pnl / total_cap * 100

        print(f'${cap:>9}/s ${total_pnl:>9.2f} {roi:>7.1f}% ${daily:>9.0f}')

if __name__ == '__main__':
    coin = 'btc'
    capital = 50
    max_windows = 0

    # parse args
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == '--coin' and i + 1 < len(args):
            coin = args[i + 1]
            i += 2
        elif args[i] == '--capital' and i + 1 < len(args):
            capital = int(args[i + 1])
            i += 2
        elif args[i] == '--windows' and i + 1 < len(args):
            max_windows = int(args[i + 1])
            i += 2
        elif args[i] == '--sensitivity':
            sensitivity(coin)
            sys.exit(0)
        else:
            i += 1

    run_backtest(coin, capital, max_windows)
