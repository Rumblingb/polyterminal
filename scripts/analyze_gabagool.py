#!/usr/bin/env python3
"""deep analysis of gabagool's limit order arb strategy"""
import requests
import json
from datetime import datetime
from collections import defaultdict
from pathlib import Path

SUBGRAPH = 'https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/orderbook-subgraph/0.0.1/gn'
WALLET = '0x6031b6eed1c97e853c6e0f03ad3ce3529351f96d'

def fetch_trades(role, wallet):
    all_events = []
    skip = 0
    batch = 100  # smaller batches

    while True:
        query = """
        {
          orderFilledEvents(
            where: { %s: "%s" }
            first: %d
            skip: %d
            orderBy: timestamp
            orderDirection: asc
          ) {
            id
            transactionHash
            timestamp
            maker
            taker
            makerAssetId
            takerAssetId
            makerAmountFilled
            takerAmountFilled
            fee
          }
        }
        """ % (role, wallet, batch, skip)

        # retry logic
        for attempt in range(5):
            try:
                resp = requests.post(SUBGRAPH, json={'query': query}, timeout=120)
                data = resp.json()
                events = data.get('data', {}).get('orderFilledEvents', [])
                break
            except Exception as e:
                if attempt < 4:
                    print(f'    retry {attempt+1} ({e.__class__.__name__})...')
                    import time
                    time.sleep(3)
                else:
                    print(f'    giving up after 5 attempts')
                    events = []

        if not events:
            break

        all_events.extend(events)
        print(f'  {role}: {len(all_events):,} events')

        if len(events) < batch:
            break
        skip += batch
        import time
        time.sleep(0.5)  # rate limit

    return all_events

def decode_trade(e, wallet):
    maker_asset = e['makerAssetId']
    taker_asset = e['takerAssetId']
    maker_amount = int(e['makerAmountFilled']) / 1e6
    taker_amount = int(e['takerAmountFilled']) / 1e6
    fee = int(e['fee']) / 1e6 if e.get('fee') else 0

    is_maker = e['maker'].lower() == wallet.lower()

    if is_maker:
        if maker_asset == '0':
            side, usdc, shares, token = 'BUY', maker_amount, taker_amount, taker_asset
        else:
            side, shares, usdc, token = 'SELL', maker_amount, taker_amount, maker_asset
    else:
        if taker_asset == '0':
            side, usdc, shares, token = 'BUY', taker_amount, maker_amount, maker_asset
        else:
            side, shares, usdc, token = 'SELL', taker_amount, maker_amount, taker_asset

    price = usdc / shares if shares > 0 else 0

    return {
        'ts': int(e['timestamp']),
        'side': side,
        'token': token,
        'shares': shares,
        'usdc': usdc,
        'price': price,
        'fee': fee,
        'is_maker': is_maker,
        'hash': e['transactionHash']
    }

def main():
    print('='*60)
    print('GABAGOOL DEEP ANALYSIS')
    print('='*60)

    # fetch all trades
    print('\nFetching trades...')
    maker_events = fetch_trades('maker', WALLET)
    taker_events = fetch_trades('taker', WALLET)

    # dedupe
    all_events = maker_events + taker_events
    seen = set()
    unique = []
    for e in all_events:
        if e['id'] not in seen:
            seen.add(e['id'])
            unique.append(e)

    print(f'\nTotal unique events: {len(unique):,}')

    # decode
    trades = [decode_trade(e, WALLET) for e in unique]
    trades.sort(key=lambda x: x['ts'])

    # save
    Path('data').mkdir(exist_ok=True)
    with open('data/gabagool_trades.json', 'w') as f:
        json.dump(trades, f)
    print(f'Saved {len(trades):,} trades')

    # === ANALYSIS ===
    print('\n' + '='*60)
    print('TRADE BREAKDOWN')
    print('='*60)

    buys = [t for t in trades if t['side'] == 'BUY']
    sells = [t for t in trades if t['side'] == 'SELL']
    maker = [t for t in trades if t['is_maker']]
    taker = [t for t in trades if not t['is_maker']]

    print(f'\nTotal: {len(trades):,} trades')
    print(f'  Buys:  {len(buys):,} (${sum(t["usdc"] for t in buys):,.0f})')
    print(f'  Sells: {len(sells):,} (${sum(t["usdc"] for t in sells):,.0f})')

    print(f'\nBy role:')
    print(f'  Maker: {len(maker):,} trades')
    print(f'  Taker: {len(taker):,} trades')

    # maker breakdown
    maker_buys = [t for t in maker if t['side'] == 'BUY']
    maker_sells = [t for t in maker if t['side'] == 'SELL']
    print(f'\nMaker orders:')
    print(f'  Buys:  {len(maker_buys):,} (${sum(t["usdc"] for t in maker_buys):,.0f})')
    print(f'  Sells: {len(maker_sells):,} (${sum(t["usdc"] for t in maker_sells):,.0f})')

    # taker breakdown
    taker_buys = [t for t in taker if t['side'] == 'BUY']
    taker_sells = [t for t in taker if t['side'] == 'SELL']
    print(f'\nTaker orders:')
    print(f'  Buys:  {len(taker_buys):,} (${sum(t["usdc"] for t in taker_buys):,.0f})')
    print(f'  Sells: {len(taker_sells):,} (${sum(t["usdc"] for t in taker_sells):,.0f})')

    # === PRICE DISTRIBUTION ===
    print('\n' + '='*60)
    print('ENTRY PRICE DISTRIBUTION (Buys only)')
    print('='*60)

    ranges = [(0, 0.3), (0.3, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0)]
    for lo, hi in ranges:
        in_range = [t for t in buys if lo <= t['price'] < hi]
        if in_range:
            vol = sum(t['usdc'] for t in in_range)
            print(f'  {lo:.0%}-{hi:.0%}: {len(in_range):,} trades (${vol:,.0f})')

    # === POSITION ANALYSIS ===
    print('\n' + '='*60)
    print('POSITION BUILDING (by token)')
    print('='*60)

    # group by token
    by_token = defaultdict(list)
    for t in trades:
        by_token[t['token']].append(t)

    # analyze each position
    positions = []
    for token, token_trades in by_token.items():
        cost = sum(t['usdc'] for t in token_trades if t['side'] == 'BUY')
        revenue = sum(t['usdc'] for t in token_trades if t['side'] == 'SELL')
        shares_bought = sum(t['shares'] for t in token_trades if t['side'] == 'BUY')
        shares_sold = sum(t['shares'] for t in token_trades if t['side'] == 'SELL')
        net_shares = shares_bought - shares_sold

        avg_buy = cost / shares_bought if shares_bought > 0 else 0
        avg_sell = revenue / shares_sold if shares_sold > 0 else 0

        positions.append({
            'token': token[:20],
            'trades': len(token_trades),
            'cost': cost,
            'revenue': revenue,
            'shares_bought': shares_bought,
            'shares_sold': shares_sold,
            'net_shares': net_shares,
            'avg_buy': avg_buy,
            'avg_sell': avg_sell,
            'pnl': revenue - cost if net_shares <= 0 else 0
        })

    # sort by volume
    positions.sort(key=lambda x: x['cost'], reverse=True)

    print(f'\nTop 10 positions by volume:')
    print(f'{"Token":<22} {"Trades":>7} {"Cost":>10} {"Revenue":>10} {"Net":>8} {"AvgBuy":>7} {"AvgSell":>7}')
    print('-'*80)
    for p in positions[:10]:
        print(f'{p["token"]:<22} {p["trades"]:>7} ${p["cost"]:>8,.0f} ${p["revenue"]:>8,.0f} {p["net_shares"]:>8.0f} {p["avg_buy"]:>7.3f} {p["avg_sell"]:>7.3f}')

    # === PAIRED POSITION ANALYSIS ===
    print('\n' + '='*60)
    print('LOOKING FOR UP/DOWN PAIRS')
    print('='*60)

    # group trades by timestamp (within 5 seconds)
    time_groups = defaultdict(list)
    for t in trades:
        bucket = t['ts'] // 5  # 5 second buckets
        time_groups[bucket].append(t)

    # find pairs where both UP and DOWN were bought
    pair_count = 0
    total_combined = 0
    combined_values = []

    for bucket, bucket_trades in time_groups.items():
        tokens_bought = set(t['token'] for t in bucket_trades if t['side'] == 'BUY')
        if len(tokens_bought) >= 2:
            # check if these look like UP/DOWN pairs (different tokens bought together)
            prices = [t['price'] for t in bucket_trades if t['side'] == 'BUY']
            if len(prices) >= 2:
                # likely pair trade
                pair_count += 1
                combined = sum(prices[:2])  # first two prices
                combined_values.append(combined)
                total_combined += combined

    if pair_count > 0:
        avg_combined = total_combined / pair_count
        print(f'\nPotential pair trades: {pair_count:,}')
        print(f'Average combined price: {avg_combined:.4f}')

        # distribution
        print(f'\nCombined price distribution:')
        for lo, hi in [(0.90, 0.95), (0.95, 0.98), (0.98, 1.00), (1.00, 1.02), (1.02, 1.10)]:
            count = len([c for c in combined_values if lo <= c < hi])
            if count > 0:
                pct = count / len(combined_values) * 100
                print(f'  {lo:.2f}-{hi:.2f}: {count:,} ({pct:.1f}%)')

    # === TIME ANALYSIS ===
    print('\n' + '='*60)
    print('TRADING PATTERNS')
    print('='*60)

    # trades per hour
    by_hour = defaultdict(int)
    for t in trades:
        hour = datetime.fromtimestamp(t['ts']).hour
        by_hour[hour] += 1

    print('\nTrades by hour (UTC):')
    for h in sorted(by_hour.keys()):
        bar = '#' * (by_hour[h] // 100)
        print(f'  {h:02d}:00 - {by_hour[h]:>5,} {bar}')

    # recent activity
    print('\nRecent trades (last 10):')
    for t in trades[-10:]:
        dt = datetime.fromtimestamp(t['ts'])
        role = 'M' if t['is_maker'] else 'T'
        print(f'  {dt} | {t["side"]:4} | ${t["usdc"]:>8.2f} | {t["price"]:.3f} | {role}')

    # === FEES ===
    print('\n' + '='*60)
    print('FEE ANALYSIS')
    print('='*60)

    total_fees = sum(t['fee'] for t in trades)
    maker_fees = sum(t['fee'] for t in maker)
    taker_fees = sum(t['fee'] for t in taker)

    print(f'\nTotal fees paid: ${total_fees:,.2f}')
    print(f'  Maker fees: ${maker_fees:,.2f}')
    print(f'  Taker fees: ${taker_fees:,.2f}')

    total_volume = sum(t['usdc'] for t in trades)
    if total_volume > 0:
        print(f'\nFee rate: {total_fees/total_volume*100:.3f}%')
    else:
        print('\nNo trades found - check API connection')

if __name__ == '__main__':
    main()
