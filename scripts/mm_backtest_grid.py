#!/usr/bin/env python3
"""
Multi-order grid market maker backtest

Tests multiple small orders across price levels instead of one large order.
Uses queue-based fill simulation with real ClickHouse data.
"""
import sys
sys.path.insert(0, 'skills/clickhouse')
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()
from ch import query

# strategy params
PRICE_LEVELS = [0.42, 0.44, 0.46, 0.48]  # bid prices to test
ORDER_SIZE = 10  # shares per order
ORDERS_PER_LEVEL = 5  # orders at each price level
CAPTURE_RATE = 0.15  # fraction of overflow we capture (competition)
TOTAL_CAPITAL = 200  # total capital constraint


@dataclass
class Order:
    price: float
    size: float
    filled: float = 0
    queue_ahead: float = 0


@dataclass
class WindowResult:
    window_ts: int
    # fills by price level
    up_fills: dict = field(default_factory=dict)  # price -> shares filled
    down_fills: dict = field(default_factory=dict)
    # book state
    up_queue: dict = field(default_factory=dict)  # price -> queue depth
    down_queue: dict = field(default_factory=dict)
    # trades
    up_sells: float = 0
    down_sells: float = 0

    def matched_pnl(self):
        """calculate pnl from matched fills only

        key insight: we need to match UP fills with DOWN fills.
        total matched = min(total_up, total_down)
        edge = weighted average of prices
        """
        total_up = sum(self.up_fills.values())
        total_down = sum(self.down_fills.values())

        if total_up == 0 or total_down == 0:
            return 0

        # weighted average prices
        up_cost = sum(p * q for p, q in self.up_fills.items())
        down_cost = sum(p * q for p, q in self.down_fills.items())

        avg_up = up_cost / total_up
        avg_down = down_cost / total_down

        matched = min(total_up, total_down)
        combined = avg_up + avg_down
        edge = 1.0 - combined

        return matched * edge


def get_queue_at_price(book_data: dict, target_price: float) -> float:
    """get total queue depth at or better than target price"""
    bids = book_data.get('bids', [])
    queue = 0
    for bid in bids:
        price = float(bid.get('price', 0))
        size = float(bid.get('size', 0))
        if price >= target_price:
            queue += size
    return queue


def simulate_window(window_ts: int, verbose: bool = False) -> WindowResult:
    """simulate grid MM for one window"""

    # get tokens
    tokens, _ = query(f"""
    SELECT token_id, side FROM token_registry
    WHERE coin='btc' AND window_ts={window_ts}
    """)
    token_map = {t[0]: t[1] for t in tokens}

    if len(token_map) < 2:
        return None

    # get all events
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

    result = WindowResult(window_ts=window_ts)

    # initialize orders at each price level
    orders = {
        'up': {p: Order(price=p, size=ORDER_SIZE * ORDERS_PER_LEVEL) for p in PRICE_LEVELS},
        'down': {p: Order(price=p, size=ORDER_SIZE * ORDERS_PER_LEVEL) for p in PRICE_LEVELS}
    }

    # track book state
    book = {'up': {}, 'down': {}}

    for ts, event_type, asset_id, raw in events:
        side = token_map.get(asset_id)
        if not side:
            continue

        data = json.loads(raw)
        if isinstance(data, list):
            data = data[0] if data else {}

        if event_type == 'book':
            book[side] = data
            # update queue depth for each price level
            for price in PRICE_LEVELS:
                queue = get_queue_at_price(data, price)
                orders[side][price].queue_ahead = queue
                if side == 'up':
                    result.up_queue[price] = queue
                else:
                    result.down_queue[price] = queue

        elif event_type == 'last_trade_price':
            trade_side = data.get('side', '')
            if trade_side != 'SELL':
                continue

            trade_price = float(data.get('price', 0))
            trade_size = float(data.get('size', 0))

            if side == 'up':
                result.up_sells += trade_size
            else:
                result.down_sells += trade_size

            # check each price level for fills
            for price in PRICE_LEVELS:
                order = orders[side][price]

                # trade must be at or below our price
                if trade_price > price + 0.02:
                    continue

                # queue-based fill
                if trade_size <= order.queue_ahead:
                    order.queue_ahead -= trade_size
                    continue

                # overflow past queue
                overflow = trade_size - order.queue_ahead
                available = overflow * CAPTURE_RATE
                fill = min(available, order.size - order.filled)

                if fill > 0:
                    order.filled += fill
                    order.queue_ahead = 0

                    if side == 'up':
                        result.up_fills[price] = result.up_fills.get(price, 0) + fill
                    else:
                        result.down_fills[price] = result.down_fills.get(price, 0) + fill

                    if verbose:
                        elapsed = ts - window_ts
                        print(f"  {elapsed:>4.0f}s FILL {side:>4} @ {price:.2f}: {fill:.1f} shares")

    return result


def analyze_price_levels():
    """analyze which price levels get hit most often"""

    windows, _ = query("""
    SELECT DISTINCT window_ts FROM token_registry
    WHERE coin='btc'
    ORDER BY window_ts DESC
    LIMIT 50
    """)

    print(f"Analyzing {len(windows)} windows...")
    print(f"Price levels: {PRICE_LEVELS}")
    print(f"Order size per level: {ORDER_SIZE * ORDERS_PER_LEVEL} shares")
    print()

    # aggregate stats
    fills_by_level = {p: {'up': 0, 'down': 0, 'windows': 0} for p in PRICE_LEVELS}
    queue_by_level = {p: [] for p in PRICE_LEVELS}
    total_pnl = 0
    windows_with_fills = 0
    both_sides_filled = 0

    for (window_ts,) in windows:
        result = simulate_window(window_ts)
        if not result:
            continue

        has_fills = False
        has_both = result.up_fills and result.down_fills

        for price in PRICE_LEVELS:
            up_fill = result.up_fills.get(price, 0)
            down_fill = result.down_fills.get(price, 0)

            if up_fill > 0 or down_fill > 0:
                has_fills = True
                fills_by_level[price]['windows'] += 1

            fills_by_level[price]['up'] += up_fill
            fills_by_level[price]['down'] += down_fill

            # track queue depths
            if price in result.up_queue:
                queue_by_level[price].append(result.up_queue[price])
            if price in result.down_queue:
                queue_by_level[price].append(result.down_queue[price])

        if has_fills:
            windows_with_fills += 1
        if has_both:
            both_sides_filled += 1

        pnl = result.matched_pnl()
        total_pnl += pnl

        if pnl != 0:
            dt = datetime.utcfromtimestamp(window_ts)
            print(f"{dt.strftime('%m/%d %H:%M')} | "
                  f"up={sum(result.up_fills.values()):>5.0f} "
                  f"dn={sum(result.down_fills.values()):>5.0f} | "
                  f"pnl=${pnl:>+6.2f}")

    # print summary
    print()
    print("=" * 70)
    print("FILL RATES BY PRICE LEVEL")
    print("=" * 70)
    print(f"{'Price':>6} | {'Edge':>5} | {'UP Fills':>10} | {'DN Fills':>10} | {'Windows':>8} | {'Avg Queue':>10}")
    print("-" * 70)

    for price in PRICE_LEVELS:
        edge = (1.0 - price * 2) * 100
        stats = fills_by_level[price]
        avg_queue = sum(queue_by_level[price]) / len(queue_by_level[price]) if queue_by_level[price] else 0

        print(f"{price:>6.2f} | {edge:>4.0f}% | {stats['up']:>10.0f} | {stats['down']:>10.0f} | "
              f"{stats['windows']:>8} | {avg_queue:>10.0f}")

    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Windows analyzed: {len(windows)}")
    print(f"Windows with fills: {windows_with_fills} ({windows_with_fills/len(windows)*100:.0f}%)")
    print(f"Windows with BOTH sides: {both_sides_filled} ({both_sides_filled/len(windows)*100:.0f}%)")
    print(f"Total PnL: ${total_pnl:.2f}")
    print(f"Per window: ${total_pnl/len(windows):.2f}")

    hours = len(windows) * 0.25
    print(f"Per day (projected): ${total_pnl/hours*24:.2f}")


def compare_strategies():
    """compare single large order vs grid"""

    windows, _ = query("""
    SELECT DISTINCT window_ts FROM token_registry
    WHERE coin='btc'
    ORDER BY window_ts DESC
    LIMIT 30
    """)

    print("Comparing: Single Order @ 0.48 vs Grid [0.42-0.48]")
    print()

    single_pnl = 0
    grid_pnl = 0

    for (window_ts,) in windows:
        # grid strategy
        result = simulate_window(window_ts)
        if result:
            grid_pnl += result.matched_pnl()

        # single order at 0.48 (simulate with just that level)
        # TODO: run separate simulation

    print(f"Grid PnL: ${grid_pnl:.2f}")


if __name__ == '__main__':
    analyze_price_levels()
