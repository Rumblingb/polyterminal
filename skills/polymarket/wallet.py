#!/usr/bin/env python3
"""
wallet - complete wallet analysis combining all data sources

high-level wallet analysis: trades, positions, P&L, activity.

usage:
    from wallet import analyze_wallet, get_positions, get_pnl

    # full analysis
    analysis = analyze_wallet('0x6031...')

    # get current positions
    positions = get_positions('0x6031...')

    # calculate P&L
    pnl = get_pnl('0x6031...')

cli:
    python wallet.py analyze <address>
    python wallet.py trades <address> [--limit N]
    python wallet.py positions <address>
    python wallet.py pnl <address>
    python wallet.py activity <address> [--hours N]
"""
import sys
import time
from datetime import datetime
from collections import defaultdict
from typing import Optional

# local imports
from subgraph import get_wallet_trades, decode_trade, count_wallet_trades
from gamma import get_token_info, get_market_by_token
from clob import get_price, get_spread


def get_decoded_trades(wallet: str, limit: int = 500,
                       since_ts: Optional[int] = None) -> list:
    """
    get wallet trades with full decoding and market info

    returns list of dicts with:
        tx, timestamp, role, side, shares, price, usdc, fee, token_id,
        market, outcome, slug
    """
    raw_trades = get_wallet_trades(wallet, limit=limit, since_ts=since_ts)

    decoded = []
    token_cache = {}

    for t in raw_trades:
        trade = decode_trade(t, wallet)

        # get market info (cached)
        token_id = trade['token_id']
        if token_id not in token_cache:
            info = get_token_info(token_id)
            token_cache[token_id] = info

        info = token_cache[token_id]
        trade['market'] = info.get('market', 'Unknown')
        trade['outcome'] = info.get('outcome', 'Unknown')
        trade['slug'] = info.get('slug', '')
        trade['condition_id'] = info.get('condition_id', '')

        decoded.append(trade)

    return decoded

def get_positions(wallet: str, limit: int = 1000) -> dict:
    """
    calculate current positions from trade history

    returns dict keyed by token_id with:
        shares, avg_entry, total_cost, market, outcome, current_price, unrealized_pnl
    """
    trades = get_decoded_trades(wallet, limit=limit)

    positions = defaultdict(lambda: {
        'shares': 0,
        'total_cost': 0,
        'trades': 0,
        'market': None,
        'outcome': None,
        'slug': None
    })

    for t in trades:
        token_id = t['token_id']
        pos = positions[token_id]

        if t['side'] == 'BUY':
            pos['shares'] += t['shares']
            pos['total_cost'] += t['usdc']
        else:  # SELL
            pos['shares'] -= t['shares']
            pos['total_cost'] -= t['usdc']

        pos['trades'] += 1
        pos['market'] = t['market']
        pos['outcome'] = t['outcome']
        pos['slug'] = t['slug']

    # filter to non-zero positions and add current prices
    active = {}
    for token_id, pos in positions.items():
        if abs(pos['shares']) > 0.01:  # small threshold for dust
            pos['avg_entry'] = pos['total_cost'] / pos['shares'] if pos['shares'] != 0 else 0

            # get current price
            try:
                current = get_price(token_id, side='sell')
                pos['current_price'] = current
                if current and pos['shares'] > 0:
                    pos['current_value'] = pos['shares'] * current
                    pos['unrealized_pnl'] = pos['current_value'] - pos['total_cost']
            except:
                pos['current_price'] = None
                pos['unrealized_pnl'] = None

            active[token_id] = pos

    return active

def get_pnl(wallet: str, limit: int = 1000) -> dict:
    """
    calculate realized P&L from trade history

    groups by market and calculates profit/loss on closed positions

    returns dict with:
        total_pnl, total_volume, win_rate, by_market, by_outcome
    """
    trades = get_decoded_trades(wallet, limit=limit)

    # group trades by market (condition_id)
    by_market = defaultdict(list)
    for t in trades:
        by_market[t['condition_id']].append(t)

    results = {
        'total_pnl': 0,
        'total_volume': 0,
        'realized_pnl': 0,
        'num_markets': 0,
        'wins': 0,
        'losses': 0,
        'by_market': {}
    }

    for cid, market_trades in by_market.items():
        if not cid:
            continue

        market_name = market_trades[0]['market']
        buy_cost = 0
        sell_proceeds = 0
        net_shares = 0

        for t in market_trades:
            results['total_volume'] += t['usdc']

            if t['side'] == 'BUY':
                buy_cost += t['usdc']
                net_shares += t['shares']
            else:
                sell_proceeds += t['usdc']
                net_shares -= t['shares']

        # if position is closed (net shares ~0), calculate realized P&L
        if abs(net_shares) < 0.1:
            pnl = sell_proceeds - buy_cost
            results['realized_pnl'] += pnl
            results['num_markets'] += 1

            if pnl > 0:
                results['wins'] += 1
            elif pnl < 0:
                results['losses'] += 1

            results['by_market'][cid] = {
                'market': market_name[:50],
                'pnl': round(pnl, 2),
                'volume': round(buy_cost + sell_proceeds, 2)
            }

    results['total_pnl'] = round(results['realized_pnl'], 2)
    results['total_volume'] = round(results['total_volume'], 2)
    results['win_rate'] = (results['wins'] / results['num_markets'] * 100
                          if results['num_markets'] > 0 else 0)

    return results

def get_activity(wallet: str, hours: int = 24) -> dict:
    """
    get recent activity summary

    returns dict with:
        trades_count, volume, buys, sells, unique_markets,
        most_active_market, hourly_breakdown
    """
    since_ts = int(time.time()) - (hours * 3600)
    trades = get_decoded_trades(wallet, limit=1000, since_ts=since_ts)

    if not trades:
        return {'trades_count': 0, 'period_hours': hours}

    buys = [t for t in trades if t['side'] == 'BUY']
    sells = [t for t in trades if t['side'] == 'SELL']
    markets = set(t['slug'] for t in trades)

    # hourly breakdown
    hourly = defaultdict(int)
    for t in trades:
        hour = datetime.fromtimestamp(t['timestamp']).strftime('%Y-%m-%d %H:00')
        hourly[hour] += 1

    # most active market
    market_counts = defaultdict(int)
    for t in trades:
        market_counts[t['slug']] += 1
    most_active = max(market_counts.items(), key=lambda x: x[1]) if market_counts else (None, 0)

    return {
        'period_hours': hours,
        'trades_count': len(trades),
        'buy_count': len(buys),
        'sell_count': len(sells),
        'total_volume': round(sum(t['usdc'] for t in trades), 2),
        'buy_volume': round(sum(t['usdc'] for t in buys), 2),
        'sell_volume': round(sum(t['usdc'] for t in sells), 2),
        'unique_markets': len(markets),
        'most_active_market': most_active[0],
        'most_active_count': most_active[1],
        'hourly_breakdown': dict(sorted(hourly.items()))
    }

def analyze_wallet(wallet: str, limit: int = 500) -> dict:
    """
    full wallet analysis combining all metrics

    returns dict with:
        address, trade_counts, recent_activity, positions, pnl_summary
    """
    # basic counts
    counts = count_wallet_trades(wallet)

    # recent activity
    activity = get_activity(wallet, hours=24)

    # current positions
    positions = get_positions(wallet, limit=limit)

    # P&L
    pnl = get_pnl(wallet, limit=limit)

    return {
        'address': wallet,
        'trade_counts': counts,
        'activity_24h': activity,
        'open_positions': len(positions),
        'positions': positions,
        'pnl': pnl
    }


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    wallet = sys.argv[2]

    if cmd == 'analyze':
        print(f"Analyzing wallet {wallet[:16]}...\n")
        analysis = analyze_wallet(wallet, limit=300)

        print("=== Trade Counts ===")
        counts = analysis['trade_counts']
        print(f"  Maker: {counts['maker']}")
        print(f"  Taker: {counts['taker']}")
        print(f"  Total: {counts['total']}")
        if counts.get('note'):
            print(f"  Note: {counts['note']}")

        print("\n=== 24h Activity ===")
        act = analysis['activity_24h']
        print(f"  Trades: {act['trades_count']}")
        print(f"  Volume: ${act['total_volume']:,.2f}")
        print(f"  Buys: {act['buy_count']} (${act['buy_volume']:,.2f})")
        print(f"  Sells: {act['sell_count']} (${act['sell_volume']:,.2f})")
        print(f"  Unique markets: {act['unique_markets']}")
        if act.get('most_active_market'):
            print(f"  Most active: {act['most_active_market']} ({act['most_active_count']} trades)")

        print(f"\n=== Positions ({analysis['open_positions']}) ===")
        for tid, pos in list(analysis['positions'].items())[:5]:
            print(f"  {pos['outcome']} - {pos['market'][:40]}")
            print(f"    Shares: {pos['shares']:.2f}, Avg entry: ${pos['avg_entry']:.4f}")
            if pos.get('current_price'):
                print(f"    Current: ${pos['current_price']:.4f}, P&L: ${pos.get('unrealized_pnl', 0):.2f}")

        print("\n=== P&L Summary ===")
        pnl = analysis['pnl']
        print(f"  Realized P&L: ${pnl['total_pnl']:,.2f}")
        print(f"  Total volume: ${pnl['total_volume']:,.2f}")
        print(f"  Markets traded: {pnl['num_markets']}")
        print(f"  Win rate: {pnl['win_rate']:.1f}% ({pnl['wins']}W / {pnl['losses']}L)")

    elif cmd == 'trades':
        limit = 20
        for i, arg in enumerate(sys.argv):
            if arg == '--limit' and i + 1 < len(sys.argv):
                limit = int(sys.argv[i + 1])

        trades = get_decoded_trades(wallet, limit=limit)
        print(f"Recent {len(trades)} trades for {wallet[:16]}...\n")

        for t in trades:
            ts = datetime.fromtimestamp(t['timestamp']).strftime('%m/%d %H:%M')
            print(f"{ts} {t['role']} {t['side']} {t['shares']:.2f} @ ${t['price']:.4f}")
            print(f"  {t['outcome']} - {t['market'][:50]}")
            print()

    elif cmd == 'positions':
        positions = get_positions(wallet)
        print(f"Open positions for {wallet[:16]}...\n")

        if not positions:
            print("No open positions")
        else:
            total_value = 0
            total_pnl = 0

            for tid, pos in positions.items():
                print(f"{pos['outcome']} - {pos['market'][:50]}")
                print(f"  Shares: {pos['shares']:.2f}")
                print(f"  Avg entry: ${pos['avg_entry']:.4f}")
                if pos.get('current_price'):
                    print(f"  Current: ${pos['current_price']:.4f}")
                    print(f"  Value: ${pos.get('current_value', 0):.2f}")
                    print(f"  Unrealized P&L: ${pos.get('unrealized_pnl', 0):.2f}")
                    total_value += pos.get('current_value', 0)
                    total_pnl += pos.get('unrealized_pnl', 0)
                print()

            print(f"Total value: ${total_value:.2f}")
            print(f"Total unrealized P&L: ${total_pnl:.2f}")

    elif cmd == 'pnl':
        pnl = get_pnl(wallet)
        print(f"P&L for {wallet[:16]}...\n")

        print(f"Realized P&L: ${pnl['total_pnl']:,.2f}")
        print(f"Total volume: ${pnl['total_volume']:,.2f}")
        print(f"Markets: {pnl['num_markets']}")
        print(f"Win rate: {pnl['win_rate']:.1f}%")
        print(f"Wins: {pnl['wins']}, Losses: {pnl['losses']}")

        if pnl['by_market']:
            print("\nBy market (closed positions):")
            sorted_markets = sorted(pnl['by_market'].items(),
                                   key=lambda x: x[1]['pnl'], reverse=True)
            for cid, m in sorted_markets[:10]:
                emoji = "+" if m['pnl'] >= 0 else ""
                print(f"  {emoji}${m['pnl']:,.2f} - {m['market']}")

    elif cmd == 'activity':
        hours = 24
        for i, arg in enumerate(sys.argv):
            if arg == '--hours' and i + 1 < len(sys.argv):
                hours = int(sys.argv[i + 1])

        activity = get_activity(wallet, hours=hours)
        print(f"Activity for {wallet[:16]}... (last {hours}h)\n")

        print(f"Trades: {activity['trades_count']}")
        print(f"Volume: ${activity['total_volume']:,.2f}")
        print(f"Buys: {activity['buy_count']} (${activity['buy_volume']:,.2f})")
        print(f"Sells: {activity['sell_count']} (${activity['sell_volume']:,.2f})")
        print(f"Unique markets: {activity['unique_markets']}")

        if activity.get('hourly_breakdown'):
            print("\nHourly breakdown:")
            for hour, count in activity['hourly_breakdown'].items():
                print(f"  {hour}: {count} trades")

    else:
        print(__doc__)
