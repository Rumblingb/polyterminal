#!/usr/bin/env python3
"""
backtest_passive - realistic passive MM backtest

we can't simulate queue position, but we CAN:
1. use REAL sell volume from actual trades
2. assume different capture rates (5%, 10%, 20%)
3. calculate PnL based on actual fill prices

this gives realistic bounds on what's achievable.
"""
import sys
import json
from datetime import datetime
from collections import defaultdict

from ch import query


def backtest_passive(coin='btc', max_windows=0, capture_rate=0.10):
    """
    simulate passive MM with assumed capture rate of SELL flow

    capture_rate: fraction of SELL volume we capture (0.05 = 5%)
    """
    windows_q, _ = query('''
        SELECT DISTINCT window_ts
        FROM clob_events
        WHERE window_ts > 0
        ORDER BY window_ts DESC
    ''')
    windows = [w[0] for w in windows_q]
    if max_windows > 0:
        windows = windows[:max_windows]
    windows = sorted(windows)

    print(f'\n{"="*70}')
    print(f'{coin.upper()} PASSIVE MM BACKTEST')
    print(f'Capture rate: {capture_rate*100:.0f}% of SELL flow')
    print(f'{"="*70}\n')

    all_results = []

    for window_ts in windows:
        # get tokens
        tq, _ = query(f"""
            SELECT token_id, side
            FROM token_registry
            WHERE coin='{coin}' AND window_ts={window_ts}
        """)
        tokens = {tid: side for tid, side in tq}

        if len(tokens) < 2:
            continue

        # get all SELL trades (our potential fills)
        rows, _ = query(f'''
            SELECT asset_id, raw, toUnixTimestamp(ts) - {window_ts} as elapsed
            FROM clob_events
            WHERE window_ts = {window_ts}
              AND event_type = 'last_trade_price'
              AND toUnixTimestamp(ts) - {window_ts} BETWEEN 0 AND 540
            ORDER BY ts
        ''')

        if not rows:
            continue

        up_fills = []
        down_fills = []

        for asset_id, raw, elapsed in rows:
            side = tokens.get(asset_id)
            if not side:
                continue

            try:
                data = json.loads(raw)
                if isinstance(data, list):
                    data = data[0]

                if data.get('side', '').upper() != 'SELL':
                    continue

                price = float(data.get('price', 0))
                size = float(data.get('size', 0))

                if price > 0 and size > 0:
                    # apply capture rate
                    captured = size * capture_rate

                    if side == 'up':
                        up_fills.append({'price': price, 'size': captured})
                    else:
                        down_fills.append({'price': price, 'size': captured})
            except:
                continue

        if not up_fills or not down_fills:
            continue

        # calculate results
        up_vol = sum(f['size'] for f in up_fills)
        down_vol = sum(f['size'] for f in down_fills)
        up_cost = sum(f['price'] * f['size'] for f in up_fills)
        down_cost = sum(f['price'] * f['size'] for f in down_fills)

        up_avg = up_cost / up_vol if up_vol > 0 else 0
        down_avg = down_cost / down_vol if down_vol > 0 else 0

        matched = min(up_vol, down_vol)
        unmatched = abs(up_vol - down_vol)
        edge = 1 - up_avg - down_avg
        matched_pnl = matched * edge

        # unmatched fills: assume 50/50 win/loss at resolution
        # actually we lose the spread on unmatched, so estimate -2% on unmatched
        unmatched_pnl = -unmatched * 0.02

        total_pnl = matched_pnl + unmatched_pnl

        all_results.append({
            'window_ts': window_ts,
            'up_vol': up_vol,
            'down_vol': down_vol,
            'matched': matched,
            'unmatched': unmatched,
            'edge': edge,
            'matched_pnl': matched_pnl,
            'unmatched_pnl': unmatched_pnl,
            'total_pnl': total_pnl
        })

    # print results
    print(f'{"Window":<14} {"UP":>8} {"DOWN":>8} {"Match":>8} {"Edge":>7} {"M.PnL":>8} {"U.PnL":>8} {"Total":>10}')
    print('-' * 80)

    for r in all_results:
        ts = datetime.utcfromtimestamp(r['window_ts']).strftime('%m/%d %H:%M')
        print(f'{ts:<14} {r["up_vol"]:>8,.0f} {r["down_vol"]:>8,.0f} {r["matched"]:>8,.0f} '
              f'{r["edge"]*100:>+6.1f}% ${r["matched_pnl"]:>7.2f} ${r["unmatched_pnl"]:>7.2f} ${r["total_pnl"]:>9.2f}')

    if all_results:
        print('-' * 80)

        total_matched_pnl = sum(r['matched_pnl'] for r in all_results)
        total_unmatched_pnl = sum(r['unmatched_pnl'] for r in all_results)
        total_pnl = sum(r['total_pnl'] for r in all_results)
        total_matched = sum(r['matched'] for r in all_results)
        total_unmatched = sum(r['unmatched'] for r in all_results)
        avg_edge = sum(r['edge'] for r in all_results) / len(all_results)
        wins = sum(1 for r in all_results if r['total_pnl'] > 0)

        print(f'\nSUMMARY ({len(all_results)} windows):')
        print(f'  Win rate:        {wins}/{len(all_results)} ({100*wins/len(all_results):.0f}%)')
        print(f'  Avg edge:        {avg_edge*100:+.2f}%')
        print(f'  Matched vol:     {total_matched:>12,.0f} shares')
        print(f'  Unmatched vol:   {total_unmatched:>12,.0f} shares')
        print(f'  Matched PnL:     ${total_matched_pnl:>11,.2f}')
        print(f'  Unmatched PnL:   ${total_unmatched_pnl:>11,.2f}')
        print(f'  TOTAL PnL:       ${total_pnl:>11,.2f}')
        print(f'  PnL per window:  ${total_pnl/len(all_results):>11,.2f}')

        hours = len(all_results) * 0.25
        print(f'\n  Hourly rate:     ${total_pnl/hours:.2f}/hr')
        print(f'  Daily rate:      ${total_pnl/hours*24:.2f}/day')

    return all_results


def compare_capture_rates(coin='btc', max_windows=0):
    """
    compare results at different capture rates
    """
    print(f'\n{"="*70}')
    print(f'{coin.upper()} CAPTURE RATE COMPARISON')
    print(f'{"="*70}\n')

    rates = [0.05, 0.10, 0.15, 0.20, 0.30, 0.50]
    results = {}

    for rate in rates:
        r = backtest_passive(coin, max_windows, rate)
        if r:
            total_pnl = sum(x['total_pnl'] for x in r)
            total_matched = sum(x['matched'] for x in r)
            wins = sum(1 for x in r if x['total_pnl'] > 0)
            results[rate] = {
                'pnl': total_pnl,
                'matched': total_matched,
                'win_rate': wins / len(r),
                'windows': len(r)
            }

    print(f'\n{"="*70}')
    print(f'CAPTURE RATE COMPARISON SUMMARY')
    print(f'{"="*70}\n')

    print(f'{"Rate":>8} {"PnL":>12} {"Matched":>12} {"Win Rate":>10} {"$/hour":>10}')
    print('-' * 55)

    for rate in rates:
        if rate in results:
            r = results[rate]
            hours = r['windows'] * 0.25
            print(f'{rate*100:>7.0f}% ${r["pnl"]:>11,.2f} {r["matched"]:>12,.0f} '
                  f'{r["win_rate"]*100:>9.0f}% ${r["pnl"]/hours:>9.2f}')


def edge_vs_time_detailed(coin='btc', max_windows=20):
    """
    show edge distribution at each minute with percentiles
    """
    windows_q, _ = query('''
        SELECT DISTINCT window_ts
        FROM clob_events
        WHERE window_ts > 0
        ORDER BY window_ts DESC
    ''')
    windows = [w[0] for w in windows_q][:max_windows]

    print(f'\n{"="*70}')
    print(f'{coin.upper()} EDGE BY MINUTE (from actual SELL trades)')
    print(f'{"="*70}\n')

    edge_by_minute = defaultdict(list)

    for window_ts in windows:
        tq, _ = query(f"""
            SELECT token_id, side
            FROM token_registry
            WHERE coin='{coin}' AND window_ts={window_ts}
        """)
        tokens = {tid: side for tid, side in tq}

        if len(tokens) < 2:
            continue

        # get sell trades grouped by minute
        rows, _ = query(f'''
            SELECT asset_id, raw, toUnixTimestamp(ts) - {window_ts} as elapsed
            FROM clob_events
            WHERE window_ts = {window_ts}
              AND event_type = 'last_trade_price'
              AND toUnixTimestamp(ts) - {window_ts} BETWEEN 0 AND 540
            ORDER BY ts
        ''')

        sells_by_minute = defaultdict(lambda: {'up': [], 'down': []})

        for asset_id, raw, elapsed in rows:
            side = tokens.get(asset_id)
            if not side:
                continue

            try:
                data = json.loads(raw)
                if isinstance(data, list):
                    data = data[0]

                if data.get('side', '').upper() != 'SELL':
                    continue

                price = float(data.get('price', 0))
                size = float(data.get('size', 0))
                minute = int(elapsed // 60)

                if price > 0 and size > 0:
                    sells_by_minute[minute][side].append({'price': price, 'size': size})
            except:
                continue

        # calculate edge for each minute
        for minute, sides in sells_by_minute.items():
            if sides['up'] and sides['down']:
                up_vol = sum(s['size'] for s in sides['up'])
                down_vol = sum(s['size'] for s in sides['down'])
                up_avg = sum(s['price'] * s['size'] for s in sides['up']) / up_vol
                down_avg = sum(s['price'] * s['size'] for s in sides['down']) / down_vol
                edge = 1 - up_avg - down_avg
                edge_by_minute[minute].append(edge)

    print(f'{"Minute":>8} {"Avg":>8} {"p25":>8} {"p50":>8} {"p75":>8} {"Samples":>10}')
    print('-' * 55)

    for minute in sorted(edge_by_minute.keys()):
        edges = sorted(edge_by_minute[minute])
        if len(edges) >= 4:
            n = len(edges)
            p25 = edges[n // 4]
            p50 = edges[n // 2]
            p75 = edges[3 * n // 4]
            avg = sum(edges) / n
            print(f'{minute:>8} {avg*100:>+7.2f}% {p25*100:>+7.2f}% {p50*100:>+7.2f}% '
                  f'{p75*100:>+7.2f}% {n:>10}')


if __name__ == '__main__':
    coin = 'btc'
    cmd = 'passive'
    max_windows = 0
    capture = 0.10

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == '--coin' and i + 1 < len(args):
            coin = args[i + 1]
            i += 2
        elif args[i] == '--windows' and i + 1 < len(args):
            max_windows = int(args[i + 1])
            i += 2
        elif args[i] == '--capture' and i + 1 < len(args):
            capture = float(args[i + 1])
            i += 2
        elif args[i] in ('passive', 'compare', 'edge'):
            cmd = args[i]
            i += 1
        else:
            i += 1

    if cmd == 'passive':
        backtest_passive(coin, max_windows, capture)
    elif cmd == 'compare':
        compare_capture_rates(coin, max_windows)
    elif cmd == 'edge':
        edge_vs_time_detailed(coin, max_windows or 20)
