#!/usr/bin/env python3
"""
backtest_take - backtest liquidity-taking strategies

uses price_change events for real-time prices (book snapshots are stale).

strategies:
1. take_edge    - take both sides when edge > threshold
2. take_timing  - take at specific elapsed time when edge exists
3. edge         - analyze edge over time
"""
import sys
import json
from datetime import datetime
from collections import defaultdict

from ch import query


def get_prices_over_time(coin, window_ts, tokens):
    """
    extract best bid/ask over time from price_change events

    returns: list of (elapsed, up_bid, up_ask, down_bid, down_ask)
    """
    rows, _ = query(f'''
        SELECT raw, toUnixTimestamp(ts) - {window_ts} as elapsed
        FROM clob_events
        WHERE window_ts = {window_ts}
          AND event_type = 'price_change'
          AND toUnixTimestamp(ts) - {window_ts} BETWEEN 0 AND 540
        ORDER BY ts
    ''')

    # track current best prices
    current = {
        'up': {'bid': 0, 'ask': 1},
        'down': {'bid': 0, 'ask': 1}
    }

    results = []

    for raw, elapsed in rows:
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                data = data[0]

            for pc in data.get('price_changes', []):
                asset_id = pc.get('asset_id')
                side = tokens.get(asset_id)
                if side:
                    if 'best_bid' in pc:
                        current[side]['bid'] = float(pc['best_bid'])
                    if 'best_ask' in pc:
                        current[side]['ask'] = float(pc['best_ask'])

            # only record if we have real prices
            if (current['up']['bid'] > 0.05 and current['up']['ask'] < 0.95 and
                current['down']['bid'] > 0.05 and current['down']['ask'] < 0.95):
                results.append((
                    elapsed,
                    current['up']['bid'],
                    current['up']['ask'],
                    current['down']['bid'],
                    current['down']['ask']
                ))
        except:
            continue

    return results


def edge_over_time(coin='btc', max_windows=20):
    """
    analyze how edge (taking asks) changes over window lifetime
    """
    windows_q, _ = query('''
        SELECT DISTINCT window_ts
        FROM clob_events
        WHERE window_ts > 0
        ORDER BY window_ts DESC
    ''')
    windows = [w[0] for w in windows_q][:max_windows]

    print(f'\n{"="*60}')
    print(f'{coin.upper()} EDGE BY MINUTE (taking asks)')
    print(f'{"="*60}\n')

    # collect edge by minute bucket
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

        prices = get_prices_over_time(coin, window_ts, tokens)

        for elapsed, up_bid, up_ask, down_bid, down_ask in prices:
            edge = 1 - up_ask - down_ask  # edge from taking asks
            minute = int(elapsed // 60)
            edge_by_minute[minute].append(edge)

    print(f'{"Minute":>8} {"Avg Edge":>10} {"Min":>8} {"Max":>8} {"Samples":>10}')
    print('-' * 50)

    for minute in sorted(edge_by_minute.keys()):
        edges = edge_by_minute[minute]
        if edges:
            avg = sum(edges) / len(edges)
            print(f'{minute:>8} {avg*100:>+9.2f}% {min(edges)*100:>+7.2f}% '
                  f'{max(edges)*100:>+7.2f}% {len(edges):>10}')


def backtest_take_edge(coin='btc', max_windows=0, edge_threshold=0.02, size_usd=100):
    """
    strategy: when combined ask < (1 - edge_threshold), take both sides
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
    print(f'{coin.upper()} TAKE-EDGE BACKTEST')
    print(f'Threshold: {edge_threshold*100:.1f}% edge | Size: ${size_usd}/side')
    print(f'{"="*70}\n')

    all_results = []

    for window_ts in windows:
        tq, _ = query(f"""
            SELECT token_id, side
            FROM token_registry
            WHERE coin='{coin}' AND window_ts={window_ts}
        """)
        tokens = {tid: side for tid, side in tq}

        if len(tokens) < 2:
            continue

        prices = get_prices_over_time(coin, window_ts, tokens)
        if not prices:
            continue

        trades = []
        last_trade_time = -60  # cooldown

        for elapsed, up_bid, up_ask, down_bid, down_ask in prices:
            if elapsed - last_trade_time < 60:  # 60s cooldown
                continue

            edge = 1 - up_ask - down_ask

            if edge >= edge_threshold:
                # simulate taking asks on both sides
                up_shares = size_usd / up_ask
                down_shares = size_usd / down_ask
                matched = min(up_shares, down_shares)
                pnl = matched * edge

                trades.append({
                    'elapsed': elapsed,
                    'up_ask': up_ask,
                    'down_ask': down_ask,
                    'edge': edge,
                    'matched': matched,
                    'pnl': pnl
                })
                last_trade_time = elapsed

        if trades:
            total_pnl = sum(t['pnl'] for t in trades)
            total_matched = sum(t['matched'] for t in trades)
            avg_edge = sum(t['edge'] for t in trades) / len(trades)

            all_results.append({
                'window_ts': window_ts,
                'trades': len(trades),
                'matched': total_matched,
                'avg_edge': avg_edge,
                'pnl': total_pnl
            })

    print(f'{"Window":<14} {"Trades":>8} {"Matched":>10} {"Avg Edge":>10} {"PnL":>12}')
    print('-' * 60)

    for r in all_results:
        ts = datetime.utcfromtimestamp(r['window_ts']).strftime('%m/%d %H:%M')
        print(f'{ts:<14} {r["trades"]:>8} {r["matched"]:>10,.0f} '
              f'{r["avg_edge"]*100:>+9.2f}% ${r["pnl"]:>11.2f}')

    if all_results:
        print('-' * 60)

        total_pnl = sum(r['pnl'] for r in all_results)
        total_trades = sum(r['trades'] for r in all_results)
        total_matched = sum(r['matched'] for r in all_results)
        avg_edge = sum(r['avg_edge'] for r in all_results) / len(all_results)
        wins = sum(1 for r in all_results if r['pnl'] > 0)

        print(f'\nSUMMARY:')
        print(f'  Windows with trades: {len(all_results)} ({100*wins/len(all_results):.0f}% win)')
        print(f'  Total trades:        {total_trades}')
        print(f'  Total matched:       {total_matched:,.0f} shares')
        print(f'  Avg edge per trade:  {avg_edge*100:+.2f}%')
        print(f'  Total PnL:           ${total_pnl:,.2f}')

    return all_results


def backtest_timing(coin='btc', max_windows=0, entry_minute=5, size_usd=100):
    """
    strategy: take liquidity at specific time if edge exists
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
    print(f'{coin.upper()} TIMING BACKTEST - Entry at minute {entry_minute}')
    print(f'Size: ${size_usd}/side')
    print(f'{"="*70}\n')

    all_results = []
    entry_start = entry_minute * 60
    entry_end = entry_start + 60

    for window_ts in windows:
        tq, _ = query(f"""
            SELECT token_id, side
            FROM token_registry
            WHERE coin='{coin}' AND window_ts={window_ts}
        """)
        tokens = {tid: side for tid, side in tq}

        if len(tokens) < 2:
            continue

        prices = get_prices_over_time(coin, window_ts, tokens)

        # find prices in entry window
        entry_prices = [p for p in prices if entry_start <= p[0] < entry_end]
        if not entry_prices:
            continue

        # use middle of entry window
        mid_idx = len(entry_prices) // 2
        elapsed, up_bid, up_ask, down_bid, down_ask = entry_prices[mid_idx]

        edge = 1 - up_ask - down_ask

        if edge > 0:
            up_shares = size_usd / up_ask
            down_shares = size_usd / down_ask
            matched = min(up_shares, down_shares)
            pnl = matched * edge

            all_results.append({
                'window_ts': window_ts,
                'elapsed': elapsed,
                'up_ask': up_ask,
                'down_ask': down_ask,
                'edge': edge,
                'matched': matched,
                'pnl': pnl
            })

    print(f'{"Window":<14} {"Time":>6} {"UP ask":>8} {"DN ask":>8} {"Edge":>8} {"PnL":>10}')
    print('-' * 65)

    for r in all_results:
        ts = datetime.utcfromtimestamp(r['window_ts']).strftime('%m/%d %H:%M')
        mins = int(r['elapsed'] // 60)
        secs = int(r['elapsed'] % 60)
        print(f'{ts:<14} {mins}:{secs:02d} {r["up_ask"]:>8.2f} {r["down_ask"]:>8.2f} '
              f'{r["edge"]*100:>+7.2f}% ${r["pnl"]:>9.2f}')

    if all_results:
        print('-' * 65)

        total_pnl = sum(r['pnl'] for r in all_results)
        total_matched = sum(r['matched'] for r in all_results)
        avg_edge = sum(r['edge'] for r in all_results) / len(all_results)
        wins = sum(1 for r in all_results if r['pnl'] > 0)

        print(f'\nSUMMARY:')
        print(f'  Windows:    {len(all_results)}')
        print(f'  Win rate:   {wins}/{len(all_results)} ({100*wins/len(all_results):.0f}%)')
        print(f'  Avg edge:   {avg_edge*100:+.2f}%')
        print(f'  Matched:    {total_matched:,.0f} shares')
        print(f'  Total PnL:  ${total_pnl:,.2f}')
        print(f'  PnL/window: ${total_pnl/len(all_results):.2f}')

    return all_results


def compare_timing(coin='btc', max_windows=50, size_usd=100):
    """
    compare entry at different minutes to find optimal timing
    """
    print(f'\n{"="*60}')
    print(f'{coin.upper()} TIMING COMPARISON')
    print(f'{"="*60}\n')

    results_by_minute = {}

    for minute in range(1, 9):
        results = backtest_timing(coin, max_windows, minute, size_usd)
        if results:
            total_pnl = sum(r['pnl'] for r in results)
            avg_edge = sum(r['edge'] for r in results) / len(results)
            win_rate = sum(1 for r in results if r['pnl'] > 0) / len(results)
            results_by_minute[minute] = {
                'pnl': total_pnl,
                'avg_edge': avg_edge,
                'win_rate': win_rate,
                'count': len(results)
            }

    print(f'\n{"="*60}')
    print(f'COMPARISON SUMMARY')
    print(f'{"="*60}\n')

    print(f'{"Minute":>8} {"PnL":>12} {"Avg Edge":>10} {"Win Rate":>10} {"Windows":>10}')
    print('-' * 55)

    for minute in sorted(results_by_minute.keys()):
        r = results_by_minute[minute]
        print(f'{minute:>8} ${r["pnl"]:>11.2f} {r["avg_edge"]*100:>+9.2f}% '
              f'{r["win_rate"]*100:>9.0f}% {r["count"]:>10}')


if __name__ == '__main__':
    coin = 'btc'
    cmd = 'edge'
    max_windows = 0
    threshold = 0.02
    size = 100
    minute = 5

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == '--coin' and i + 1 < len(args):
            coin = args[i + 1]
            i += 2
        elif args[i] == '--windows' and i + 1 < len(args):
            max_windows = int(args[i + 1])
            i += 2
        elif args[i] == '--threshold' and i + 1 < len(args):
            threshold = float(args[i + 1])
            i += 2
        elif args[i] == '--size' and i + 1 < len(args):
            size = float(args[i + 1])
            i += 2
        elif args[i] == '--minute' and i + 1 < len(args):
            minute = int(args[i + 1])
            i += 2
        elif args[i] in ('edge', 'take', 'timing', 'compare'):
            cmd = args[i]
            i += 1
        else:
            i += 1

    if cmd == 'edge':
        edge_over_time(coin, max_windows or 20)
    elif cmd == 'take':
        backtest_take_edge(coin, max_windows, threshold, size)
    elif cmd == 'timing':
        backtest_timing(coin, max_windows, minute, size)
    elif cmd == 'compare':
        compare_timing(coin, max_windows or 50, size)
