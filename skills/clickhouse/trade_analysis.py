#!/usr/bin/env python3
"""
trade_analysis - analyze REAL trade data from clickhouse

no simulation. no fake queues. just facts from actual trades.

questions we answer:
1. what edge do SELL trades actually execute at?
2. how balanced are UP vs DOWN fills?
3. what volume trades at each price level?
4. what's the realistic capture rate for passive MM?
"""
import sys
import json
from datetime import datetime
from collections import defaultdict

from ch import query


def analyze_trades(coin='btc', max_windows=0):
    """
    analyze actual SELL trades (passive fills) from real data

    SELL trades = someone selling into bids = passive maker fills
    this is exactly what we care about for market making
    """
    # get windows
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
    print(f'{coin.upper()} TRADE ANALYSIS - Real Data')
    print(f'{"="*70}\n')

    all_stats = []

    for window_ts in windows:
        # get token mapping
        tq, _ = query(f"""
            SELECT token_id, side
            FROM token_registry
            WHERE coin='{coin}' AND window_ts={window_ts}
        """)
        tokens = {tid: side for tid, side in tq}

        if len(tokens) < 2:
            continue

        # get all last_trade_price events (first 9 min)
        rows, _ = query(f'''
            SELECT
                asset_id,
                raw,
                toUnixTimestamp(ts) - {window_ts} as elapsed
            FROM clob_events
            WHERE window_ts = {window_ts}
              AND event_type = 'last_trade_price'
              AND toUnixTimestamp(ts) - {window_ts} BETWEEN 0 AND 540
            ORDER BY ts
        ''')

        if not rows:
            continue

        # analyze trades by side
        stats = {
            'up': {'buy': [], 'sell': []},
            'down': {'buy': [], 'sell': []}
        }

        for asset_id, raw, elapsed in rows:
            side = tokens.get(asset_id)
            if not side:
                continue

            try:
                data = json.loads(raw)
                if isinstance(data, list):
                    data = data[0] if data else {}

                price = float(data.get('price', 0))
                size = float(data.get('size', 0))
                trade_side = data.get('side', '').upper()

                if trade_side in ('BUY', 'SELL') and price > 0 and size > 0:
                    stats[side][trade_side.lower()].append({
                        'price': price,
                        'size': size,
                        'elapsed': elapsed
                    })
            except:
                continue

        # calculate metrics
        up_sells = stats['up']['sell']
        down_sells = stats['down']['sell']
        up_buys = stats['up']['buy']
        down_buys = stats['down']['buy']

        if not up_sells or not down_sells:
            continue

        # SELL volume and avg price (these are passive fills on bids)
        up_sell_vol = sum(t['size'] for t in up_sells)
        down_sell_vol = sum(t['size'] for t in down_sells)
        up_sell_avg = sum(t['price'] * t['size'] for t in up_sells) / up_sell_vol
        down_sell_avg = sum(t['price'] * t['size'] for t in down_sells) / down_sell_vol

        # BUY volume (aggressive takes of asks)
        up_buy_vol = sum(t['size'] for t in up_buys) if up_buys else 0
        down_buy_vol = sum(t['size'] for t in down_buys) if down_buys else 0

        # edge at SELL prices (what passive makers capture)
        realized_edge = 1 - up_sell_avg - down_sell_avg

        # balance ratio
        min_vol = min(up_sell_vol, down_sell_vol)
        max_vol = max(up_sell_vol, down_sell_vol)
        balance = min_vol / max_vol if max_vol > 0 else 0

        all_stats.append({
            'window_ts': window_ts,
            'up_sell_vol': up_sell_vol,
            'down_sell_vol': down_sell_vol,
            'up_sell_avg': up_sell_avg,
            'down_sell_avg': down_sell_avg,
            'up_buy_vol': up_buy_vol,
            'down_buy_vol': down_buy_vol,
            'realized_edge': realized_edge,
            'balance': balance,
            'up_sell_count': len(up_sells),
            'down_sell_count': len(down_sells),
        })

    # print results
    print(f'{"Window":<14} {"UP SELL":>10} {"DN SELL":>10} {"Balance":>8} {"Edge":>8} {"UP avg":>7} {"DN avg":>7}')
    print('-' * 75)

    for s in all_stats:
        ts = datetime.utcfromtimestamp(s['window_ts']).strftime('%m/%d %H:%M')
        print(f'{ts:<14} {s["up_sell_vol"]:>10,.0f} {s["down_sell_vol"]:>10,.0f} '
              f'{s["balance"]*100:>7.0f}% {s["realized_edge"]*100:>+7.2f}% '
              f'{s["up_sell_avg"]:>6.2f}c {s["down_sell_avg"]:>6.2f}c')

    if all_stats:
        print('-' * 75)

        # aggregates
        total_up = sum(s['up_sell_vol'] for s in all_stats)
        total_down = sum(s['down_sell_vol'] for s in all_stats)
        avg_balance = sum(s['balance'] for s in all_stats) / len(all_stats)
        avg_edge = sum(s['realized_edge'] for s in all_stats) / len(all_stats)

        # weighted avg prices
        wtd_up_avg = sum(s['up_sell_avg'] * s['up_sell_vol'] for s in all_stats) / total_up
        wtd_down_avg = sum(s['down_sell_avg'] * s['down_sell_vol'] for s in all_stats) / total_down
        wtd_edge = 1 - wtd_up_avg - wtd_down_avg

        print(f'\nSUMMARY ({len(all_stats)} windows):')
        print(f'  UP SELL volume:   {total_up:>12,.0f} shares')
        print(f'  DOWN SELL volume: {total_down:>12,.0f} shares')
        print(f'  Balance ratio:    {avg_balance*100:>12.1f}%')
        print(f'  Avg edge:         {avg_edge*100:>+12.2f}% (simple avg)')
        print(f'  Wtd edge:         {wtd_edge*100:>+12.2f}% (volume-weighted)')
        print(f'  UP weighted avg:  {wtd_up_avg:>12.3f}')
        print(f'  DOWN weighted avg:{wtd_down_avg:>12.3f}')

        # what this means for MM
        matched_potential = min(total_up, total_down)
        potential_pnl = matched_potential * wtd_edge

        print(f'\nMM POTENTIAL:')
        print(f'  If you captured ALL sells on both sides:')
        print(f'    Matched shares: {matched_potential:>10,.0f}')
        print(f'    PnL at edge:    ${potential_pnl:>10,.2f}')
        print(f'  Reality check:')
        print(f'    You compete with other MMs')
        print(f'    Realistic capture: 5-20% of volume')
        print(f'    Realistic PnL:     ${potential_pnl * 0.1:>10,.2f} (at 10% capture)')

    return all_stats


def price_distribution(coin='btc', max_windows=5):
    """
    show distribution of SELL prices (passive fills)

    helps understand:
    - what % of volume trades at each price level
    - should you post at best bid or behind?
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

    print(f'\n{"="*60}')
    print(f'{coin.upper()} SELL PRICE DISTRIBUTION')
    print(f'{"="*60}\n')

    # collect all prices
    up_prices = defaultdict(float)
    down_prices = defaultdict(float)

    for window_ts in windows:
        tq, _ = query(f"""
            SELECT token_id, side
            FROM token_registry
            WHERE coin='{coin}' AND window_ts={window_ts}
        """)
        tokens = {tid: side for tid, side in tq}

        if len(tokens) < 2:
            continue

        rows, _ = query(f'''
            SELECT asset_id, raw
            FROM clob_events
            WHERE window_ts = {window_ts}
              AND event_type = 'last_trade_price'
              AND toUnixTimestamp(ts) - {window_ts} BETWEEN 0 AND 540
        ''')

        for asset_id, raw in rows:
            side = tokens.get(asset_id)
            if not side:
                continue

            try:
                data = json.loads(raw)
                if isinstance(data, list):
                    data = data[0] if data else {}

                if data.get('side', '').upper() != 'SELL':
                    continue

                price = float(data.get('price', 0))
                size = float(data.get('size', 0))

                # bucket by cent
                bucket = round(price, 2)
                if side == 'up':
                    up_prices[bucket] += size
                else:
                    down_prices[bucket] += size
            except:
                continue

    # print distributions
    print('UP token SELL distribution:')
    print(f'{"Price":>8} {"Volume":>12} {"% of total":>10}')
    total_up = sum(up_prices.values())
    for price in sorted(up_prices.keys(), reverse=True)[:15]:
        vol = up_prices[price]
        pct = vol / total_up * 100 if total_up else 0
        bar = '#' * int(pct / 2)
        print(f'{price:>8.2f} {vol:>12,.0f} {pct:>9.1f}% {bar}')

    print(f'\nDOWN token SELL distribution:')
    print(f'{"Price":>8} {"Volume":>12} {"% of total":>10}')
    total_down = sum(down_prices.values())
    for price in sorted(down_prices.keys(), reverse=True)[:15]:
        vol = down_prices[price]
        pct = vol / total_down * 100 if total_down else 0
        bar = '#' * int(pct / 2)
        print(f'{price:>8.2f} {vol:>12,.0f} {pct:>9.1f}% {bar}')


def buy_sell_ratio(coin='btc', max_windows=10):
    """
    analyze BUY vs SELL ratio over time

    high buy ratio = directional pressure = adverse selection risk
    """
    windows_q, _ = query('''
        SELECT DISTINCT window_ts
        FROM clob_events
        WHERE window_ts > 0
        ORDER BY window_ts DESC
    ''')
    windows = [w[0] for w in windows_q][:max_windows]

    print(f'\n{"="*60}')
    print(f'{coin.upper()} BUY:SELL RATIO BY WINDOW')
    print(f'{"="*60}\n')

    print(f'{"Window":<14} {"UP B:S":>10} {"DN B:S":>10} {"UP vol":>10} {"DN vol":>10}')
    print('-' * 60)

    for window_ts in sorted(windows):
        tq, _ = query(f"""
            SELECT token_id, side
            FROM token_registry
            WHERE coin='{coin}' AND window_ts={window_ts}
        """)
        tokens = {tid: side for tid, side in tq}

        if len(tokens) < 2:
            continue

        rows, _ = query(f'''
            SELECT asset_id, raw
            FROM clob_events
            WHERE window_ts = {window_ts}
              AND event_type = 'last_trade_price'
              AND toUnixTimestamp(ts) - {window_ts} BETWEEN 0 AND 540
        ''')

        up_buy = up_sell = down_buy = down_sell = 0

        for asset_id, raw in rows:
            side = tokens.get(asset_id)
            if not side:
                continue

            try:
                data = json.loads(raw)
                if isinstance(data, list):
                    data = data[0] if data else {}

                size = float(data.get('size', 0))
                trade_side = data.get('side', '').upper()

                if side == 'up':
                    if trade_side == 'BUY':
                        up_buy += size
                    else:
                        up_sell += size
                else:
                    if trade_side == 'BUY':
                        down_buy += size
                    else:
                        down_sell += size
            except:
                continue

        up_ratio = up_buy / up_sell if up_sell > 0 else 0
        down_ratio = down_buy / down_sell if down_sell > 0 else 0

        ts = datetime.utcfromtimestamp(window_ts).strftime('%m/%d %H:%M')
        print(f'{ts:<14} {up_ratio:>10.1f}:1 {down_ratio:>10.1f}:1 '
              f'{up_buy+up_sell:>10,.0f} {down_buy+down_sell:>10,.0f}')


if __name__ == '__main__':
    coin = 'btc'
    cmd = 'trades'
    max_windows = 0

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == '--coin' and i + 1 < len(args):
            coin = args[i + 1]
            i += 2
        elif args[i] == '--windows' and i + 1 < len(args):
            max_windows = int(args[i + 1])
            i += 2
        elif args[i] in ('trades', 'dist', 'ratio'):
            cmd = args[i]
            i += 1
        else:
            i += 1

    if cmd == 'trades':
        analyze_trades(coin, max_windows)
    elif cmd == 'dist':
        price_distribution(coin, max_windows or 5)
    elif cmd == 'ratio':
        buy_sell_ratio(coin, max_windows or 10)
