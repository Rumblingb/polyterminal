#!/usr/bin/env python3
"""
backtest_v2 - proper orderbook backtest with queue simulation

uses full L2 orderbook data to simulate realistic fills.
tracks queue position at each price level using FIFO matching.

usage:
    python backtest_v2.py [--coin btc] [--capital 100] [--windows 0] [--strategy mm]

strategies:
    mm      - market making at best bid (default)
    mm-1    - market making 1 tick behind best bid
    take    - take liquidity when edge > threshold

queue model:
    - your order joins back of queue when posted
    - queue drains as SELLs sweep through
    - you fill when queue ahead = 0 and more volume comes
"""
import sys
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from collections import defaultdict

from ch import query


@dataclass
class Order:
    side: str  # 'up' or 'down'
    price: float
    size: float
    queue_ahead: float  # shares ahead of us
    posted_at: float  # elapsed seconds when posted
    filled: float = 0
    fill_prices: list = field(default_factory=list)


@dataclass
class Fill:
    side: str
    price: float
    size: float
    elapsed: float


class OrderbookSimulator:
    """
    simulates orderbook and queue position for backtesting
    """
    def __init__(self):
        # full L2 book: {side: {price: size}}
        self.bids = {'up': {}, 'down': {}}
        self.asks = {'up': {}, 'down': {}}

        # best prices
        self.best_bid = {'up': 0, 'down': 0}
        self.best_ask = {'up': 1, 'down': 1}

        # our orders
        self.orders: list[Order] = []

        # fills
        self.fills: list[Fill] = []

        # track when we last updated book (for queue joining)
        self.last_book_update = {'up': 0, 'down': 0}

    def update_book(self, side: str, bids: list, asks: list, elapsed: float):
        """update full orderbook from snapshot"""
        # parse bids
        self.bids[side] = {}
        for b in bids:
            price = float(b['price'])
            size = float(b['size'])
            self.bids[side][price] = size

        # parse asks
        self.asks[side] = {}
        for a in asks:
            price = float(a['price'])
            size = float(a['size'])
            self.asks[side][price] = size

        # update best prices
        if self.bids[side]:
            self.best_bid[side] = max(self.bids[side].keys())
        if self.asks[side]:
            self.best_ask[side] = min(self.asks[side].keys())

        self.last_book_update[side] = elapsed

        # update queue positions for our orders
        for order in self.orders:
            if order.side == side and order.filled < order.size:
                book_size = self.bids[side].get(order.price, 0)
                # if price level still exists and has less depth, update queue
                if book_size > 0:
                    # we stay at back of whatever queue exists
                    # but can't have more ahead than total book size
                    order.queue_ahead = min(order.queue_ahead, book_size)

    def post_order(self, side: str, price: float, size: float, elapsed: float):
        """post a new order, joining back of queue"""
        queue_ahead = self.bids[side].get(price, 0)
        order = Order(
            side=side,
            price=price,
            size=size,
            queue_ahead=queue_ahead,
            posted_at=elapsed
        )
        self.orders.append(order)
        return order

    def process_trade(self, side: str, trade_price: float, trade_size: float,
                      trade_side: str, elapsed: float):
        """
        process incoming trade, simulate fills for our orders

        trade_side: 'BUY' or 'SELL' - the aggressor side
        for us to fill on bids, we need SELL trades (someone selling into bids)
        """
        if trade_side != 'SELL':
            return

        remaining = trade_size

        # process our orders at this price level or better
        for order in self.orders:
            if order.side != side:
                continue
            if order.filled >= order.size:
                continue

            # trade must reach our price level
            # (trade at our price or lower means it swept through)
            if trade_price > order.price + 0.01:
                continue

            # drain queue ahead first
            if order.queue_ahead > 0:
                drained = min(remaining, order.queue_ahead)
                order.queue_ahead -= drained
                remaining -= drained

            # then fill us
            if remaining > 0 and order.queue_ahead <= 0:
                can_fill = min(remaining, order.size - order.filled)
                if can_fill > 0:
                    order.filled += can_fill
                    order.fill_prices.append((order.price, can_fill))
                    remaining -= can_fill

                    self.fills.append(Fill(
                        side=side,
                        price=order.price,
                        size=can_fill,
                        elapsed=elapsed
                    ))

            if remaining <= 0:
                break

    def get_spread(self, side: str) -> float:
        """get bid-ask spread"""
        return self.best_ask[side] - self.best_bid[side]

    def get_edge(self) -> float:
        """get current book edge (1 - up_bid - down_bid)"""
        return 1 - self.best_bid['up'] - self.best_bid['down']


def run_backtest(coin='btc', capital_per_side=100, max_windows=0,
                 strategy='mm', tick_offset=0):
    """
    run backtest with proper queue simulation

    args:
        coin: btc, eth, sol, xrp
        capital_per_side: USD to deploy per side
        max_windows: limit windows (0 = all)
        strategy: 'mm' (market make at best bid), 'mm-1' (1 tick back)
        tick_offset: ticks behind best bid (0 = at best bid)
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

    all_results = []

    for window_ts in windows:
        # get tokens for this window
        tq, _ = query(f"""
            SELECT token_id, side
            FROM token_registry
            WHERE coin='{coin}' AND window_ts={window_ts}
        """)
        tokens = {tid: side for tid, side in tq}

        if len(tokens) < 2:
            continue

        # get all events: book snapshots + trades
        # only first 9 minutes (540s) to avoid resolution period
        rows, _ = query(f'''
            SELECT
                event_type,
                asset_id,
                raw,
                toUnixTimestamp(ts) - {window_ts} as elapsed
            FROM clob_events
            WHERE window_ts = {window_ts}
              AND event_type IN ('book', 'last_trade_price')
              AND toUnixTimestamp(ts) - {window_ts} BETWEEN 0 AND 540
            ORDER BY ts
        ''')

        if not rows:
            continue

        # init simulator
        sim = OrderbookSimulator()

        # track if we've posted orders
        posted = {'up': False, 'down': False}
        capital_remaining = {'up': capital_per_side, 'down': capital_per_side}

        # process events in order
        for event_type, asset_id, raw, elapsed in rows:
            side = tokens.get(asset_id)
            if not side:
                continue

            try:
                data = json.loads(raw)
                if isinstance(data, list):
                    data = data[0] if data else {}
            except:
                continue

            if event_type == 'book':
                bids = data.get('bids', [])
                asks = data.get('asks', [])
                sim.update_book(side, bids, asks, elapsed)

                # post orders after first book update (if we have edge)
                if not posted[side] and sim.best_bid[side] > 0:
                    edge = sim.get_edge()

                    # only post if there's positive edge
                    if edge > 0.01:
                        # calculate posting price
                        post_price = sim.best_bid[side]
                        if tick_offset > 0:
                            post_price = round(post_price - (0.01 * tick_offset), 2)

                        if post_price > 0 and capital_remaining[side] > 0:
                            size = capital_remaining[side] / post_price
                            sim.post_order(side, post_price, size, elapsed)
                            posted[side] = True

            elif event_type == 'last_trade_price':
                trade_price = float(data.get('price', 0))
                trade_size = float(data.get('size', 0))
                trade_side = data.get('side', '')

                sim.process_trade(side, trade_price, trade_size, trade_side, elapsed)

        # calculate results for this window
        up_fills = [f for f in sim.fills if f.side == 'up']
        down_fills = [f for f in sim.fills if f.side == 'down']

        up_shares = sum(f.size for f in up_fills)
        down_shares = sum(f.size for f in down_fills)

        if up_shares > 0 and down_shares > 0:
            up_cost = sum(f.price * f.size for f in up_fills)
            down_cost = sum(f.price * f.size for f in down_fills)

            up_avg = up_cost / up_shares
            down_avg = down_cost / down_shares

            matched = min(up_shares, down_shares)
            edge = 1 - (up_avg + down_avg)
            pnl = matched * edge

            all_results.append({
                'window_ts': window_ts,
                'up_shares': up_shares,
                'down_shares': down_shares,
                'up_avg': up_avg,
                'down_avg': down_avg,
                'matched': matched,
                'edge': edge,
                'pnl': pnl,
                'capital': up_cost + down_cost,
                'up_orders': len([o for o in sim.orders if o.side == 'up']),
                'down_orders': len([o for o in sim.orders if o.side == 'down']),
            })

    # print results
    print(f'\n{"="*70}')
    print(f'{coin.upper()} BACKTEST - Queue Simulation')
    print(f'Strategy: {strategy} | Capital: ${capital_per_side}/side | Tick offset: {tick_offset}')
    print(f'{"="*70}\n')

    print(f'{"Window":<14} {"UP":>8} {"DOWN":>8} {"Match":>8} {"Edge":>8} {"PnL":>10}')
    print('-' * 70)

    for r in all_results:
        ts = datetime.utcfromtimestamp(r['window_ts']).strftime('%m/%d %H:%M')
        print(f'{ts:<14} {r["up_shares"]:>8.0f} {r["down_shares"]:>8.0f} '
              f'{r["matched"]:>8.0f} {r["edge"]*100:>+7.2f}% ${r["pnl"]:>9.2f}')

    if all_results:
        print('-' * 70)

        total_pnl = sum(r['pnl'] for r in all_results)
        total_matched = sum(r['matched'] for r in all_results)
        total_capital = sum(r['capital'] for r in all_results)
        avg_edge = sum(r['edge'] for r in all_results) / len(all_results)
        wins = sum(1 for r in all_results if r['pnl'] > 0)

        print(f'\nSUMMARY:')
        print(f'  Windows:     {len(all_results)}')
        print(f'  Win rate:    {wins}/{len(all_results)} ({100*wins/len(all_results):.0f}%)')
        print(f'  Matched:     {total_matched:,.0f} shares')
        print(f'  Avg edge:    {avg_edge*100:+.2f}%')
        print(f'  Total PnL:   ${total_pnl:,.2f}')
        print(f'  Capital:     ${total_capital:,.0f}')
        print(f'  ROI:         {100*total_pnl/total_capital:.2f}%')

        hours = len(all_results) * 0.25
        print(f'\nPROJECTED:')
        print(f'  Per hour:    ${total_pnl/hours:.2f}')
        print(f'  Per day:     ${total_pnl/hours*24:.2f}')
    else:
        print('No results - check if data exists for this coin')

    return all_results


def analyze_queue_depth(coin='btc', window_ts=None):
    """analyze actual queue depths from book data"""
    if window_ts is None:
        # get most recent window
        rows, _ = query('SELECT max(window_ts) FROM clob_events WHERE window_ts > 0')
        window_ts = rows[0][0]

    tq, _ = query(f"""
        SELECT token_id, side
        FROM token_registry
        WHERE coin='{coin}' AND window_ts={window_ts}
    """)
    tokens = {tid: side for tid, side in tq}

    rows, _ = query(f'''
        SELECT asset_id, raw, toUnixTimestamp(ts) - {window_ts} as elapsed
        FROM clob_events
        WHERE window_ts = {window_ts}
          AND event_type = 'book'
          AND toUnixTimestamp(ts) - {window_ts} BETWEEN 0 AND 540
        ORDER BY ts
    ''')

    depths = {'up': [], 'down': []}
    spreads = {'up': [], 'down': []}

    for asset_id, raw, elapsed in rows:
        side = tokens.get(asset_id)
        if not side:
            continue

        try:
            data = json.loads(raw)
            if isinstance(data, list):
                data = data[0] if data else {}

            bids = data.get('bids', [])
            asks = data.get('asks', [])

            if bids and asks:
                # full depth at top 3 levels
                sorted_bids = sorted(bids, key=lambda x: -float(x['price']))[:3]
                total_depth = sum(float(b['size']) for b in sorted_bids)
                depths[side].append(total_depth)

                best_bid = float(sorted_bids[0]['price'])
                best_ask = min(float(a['price']) for a in asks)
                spreads[side].append(best_ask - best_bid)
        except:
            continue

    dt = datetime.utcfromtimestamp(window_ts).strftime('%m/%d %H:%M UTC')
    print(f'\n{coin.upper()} Queue Analysis - {dt}')
    print('=' * 50)

    for side in ['up', 'down']:
        d = depths[side]
        s = spreads[side]
        if d:
            print(f'\n{side.upper()}:')
            print(f'  Depth (top 3 levels):')
            print(f'    avg: {sum(d)/len(d):,.0f} shares')
            print(f'    min: {min(d):,.0f}')
            print(f'    max: {max(d):,.0f}')
            print(f'  Spread:')
            print(f'    avg: {sum(s)/len(s)*100:.1f} cents')
            print(f'    min: {min(s)*100:.1f} cents')
            print(f'    max: {max(s)*100:.1f} cents')


if __name__ == '__main__':
    coin = 'btc'
    capital = 100
    max_windows = 0
    strategy = 'mm'
    tick_offset = 0
    cmd = 'run'

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == '--coin' and i + 1 < len(args):
            coin = args[i + 1]
            i += 2
        elif args[i] == '--capital' and i + 1 < len(args):
            capital = float(args[i + 1])
            i += 2
        elif args[i] == '--windows' and i + 1 < len(args):
            max_windows = int(args[i + 1])
            i += 2
        elif args[i] == '--strategy' and i + 1 < len(args):
            strategy = args[i + 1]
            i += 2
        elif args[i] == '--offset' and i + 1 < len(args):
            tick_offset = int(args[i + 1])
            i += 2
        elif args[i] == 'queue':
            cmd = 'queue'
            i += 1
        else:
            i += 1

    if cmd == 'queue':
        analyze_queue_depth(coin)
    else:
        run_backtest(coin, capital, max_windows, strategy, tick_offset)
