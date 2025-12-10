#!/usr/bin/env python3
"""
resolution - check market outcomes

usage:
    python skills/resolution.py [--coin btc] [--windows 20]
"""
import sys
import json
from datetime import datetime
from collections import Counter

from ch import query

def get_resolutions(coin=None, max_windows=20):
    """show resolution outcomes"""
    where = f"WHERE coin = '{coin}'" if coin else "WHERE 1=1"

    rows, _ = query(f'''
    SELECT window_ts, coin, raw
    FROM crypto_prices
    {where}
    ORDER BY window_ts DESC
    LIMIT {max_windows * 4 if not coin else max_windows}
    ''')

    results = []
    for window_ts, c, raw in rows:
        data = json.loads(raw)
        if isinstance(data, list):
            data = data[0] if data else {}

        outcome_prices = data.get('outcomePrices', '')
        if isinstance(outcome_prices, str) and outcome_prices:
            outcome_prices = json.loads(outcome_prices)

        if outcome_prices == ['1', '0']:
            outcome = 'UP'
        elif outcome_prices == ['0', '1']:
            outcome = 'DOWN'
        else:
            outcome = '?'

        results.append((window_ts, c, outcome))

    # group by window
    by_window = {}
    for window_ts, c, outcome in results:
        if window_ts not in by_window:
            by_window[window_ts] = {}
        by_window[window_ts][c] = outcome

    print(f'{"Window":<20} {"BTC":>5} {"ETH":>5} {"SOL":>5} {"XRP":>5}')
    print('-' * 45)

    for window_ts in sorted(by_window.keys(), reverse=True)[:max_windows]:
        dt = datetime.utcfromtimestamp(window_ts).strftime('%m/%d %H:%M UTC')
        w = by_window[window_ts]
        print(f'{dt:<20} {w.get("btc", "-"):>5} {w.get("eth", "-"):>5} '
              f'{w.get("sol", "-"):>5} {w.get("xrp", "-"):>5}')

    # stats
    outcomes = Counter(o for _, _, o in results)
    total = sum(outcomes.values())
    print()
    print(f'Total: {total} resolutions')
    print(f'UP: {outcomes["UP"]} ({outcomes["UP"]/total*100:.0f}%)')
    print(f'DOWN: {outcomes["DOWN"]} ({outcomes["DOWN"]/total*100:.0f}%)')

if __name__ == '__main__':
    coin = None
    max_windows = 20

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == '--coin' and i + 1 < len(args):
            coin = args[i + 1]
            i += 2
        elif args[i] == '--windows' and i + 1 < len(args):
            max_windows = int(args[i + 1])
            i += 2
        else:
            i += 1

    get_resolutions(coin, max_windows)
