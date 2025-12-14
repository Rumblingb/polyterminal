#!/usr/bin/env python3
"""
Dynamic market maker backtest using Avellaneda-Stoikov principles

Key differences from fixed grid:
1. Reservation price shifts based on inventory imbalance
2. Spread widens with volatility
3. Prices adjust as window progresses
"""
import sys
sys.path.insert(0, 'skills/clickhouse')
import json
import math
from dataclasses import dataclass
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()
from ch import query

# strategy params
BASE_BID = 0.48  # starting bid when balanced
MIN_BID = 0.40   # floor
MAX_BID = 0.50   # ceiling (no edge above this)
ORDER_SIZE = 50  # shares per side
MAX_CAPITAL = 100  # per side
GAMMA = 0.1  # risk aversion (higher = more aggressive rebalancing)
CAPTURE_RATE = 0.15


@dataclass
class Position:
    up_qty: float = 0
    up_cost: float = 0
    down_qty: float = 0
    down_cost: float = 0

    @property
    def inventory_imbalance(self):
        """positive = long UP, negative = long DOWN"""
        return self.up_qty - self.down_qty

    @property
    def total_qty(self):
        return self.up_qty + self.down_qty


def calculate_dynamic_bids(pos: Position, volatility: float, time_remaining: float):
    """
    Avellaneda-Stoikov pricing adapted for binary options

    REAL A-S formula:
      reservation_price = mid - q × γ × σ² × (T-t)
      optimal_spread = γ × σ² × (T-t) + (2/γ) × ln(1 + γ/κ)

    For binary options where UP + DOWN = 1:
      - mid_price is always 0.50 (fair value when uncertain)
      - q = inventory imbalance (up_qty - down_qty)
      - We bid BELOW reservation to capture edge

    Parameters:
      γ (GAMMA) = 0.1 = risk aversion
      κ (KAPPA) = 1.5 = order book liquidity (higher = tighter spreads)
      σ (volatility) = estimated from recent price moves
    """
    KAPPA = 1.5  # liquidity parameter

    # inventory imbalance: positive = long UP
    q = pos.inventory_imbalance

    # A-S reservation price (where we're indifferent)
    # r = s - q × γ × σ² × (T-t)
    mid = 0.50
    sigma_sq = volatility ** 2
    reservation = mid - q * GAMMA * sigma_sq * time_remaining

    # A-S optimal spread
    # δ = γσ²(T-t) + (2/γ)ln(1 + γ/κ)
    spread = GAMMA * sigma_sq * time_remaining + (2/GAMMA) * math.log(1 + GAMMA/KAPPA)

    # for binary options, we want to bid BELOW mid to have edge
    # but shift based on inventory
    # UP bid: reservation - spread/2 (lower when long UP)
    # DOWN bid: (1 - reservation) - spread/2 = 0.5 + (0.5 - reservation) - spread/2

    # simplified: just shift base bid by inventory adjustment
    inventory_shift = q * GAMMA * sigma_sq * time_remaining

    up_bid = BASE_BID - inventory_shift - spread/2
    down_bid = BASE_BID + inventory_shift - spread/2

    # clamp to valid range
    up_bid = max(MIN_BID, min(MAX_BID, up_bid))
    down_bid = max(MIN_BID, min(MAX_BID, down_bid))

    return up_bid, down_bid


def estimate_volatility(prices: list) -> float:
    """simple volatility estimate from recent prices"""
    if len(prices) < 2:
        return 0.05  # default
    returns = [abs(prices[i] - prices[i-1]) / prices[i-1]
               for i in range(1, len(prices)) if prices[i-1] > 0]
    if not returns:
        return 0.05
    return min(0.2, sum(returns) / len(returns))


def simulate_window_dynamic(window_ts: int, verbose: bool = False):
    """simulate dynamic MM for one window"""

    # get tokens
    tokens, _ = query(f"""
    SELECT token_id, side FROM token_registry
    WHERE coin='btc' AND window_ts={window_ts}
    """)
    token_map = {t[0]: t[1] for t in tokens}
    if len(token_map) < 2:
        return None

    # get events
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

    pos = Position()
    recent_prices = {'up': [], 'down': []}
    book = {'up': {'bid': 0.5}, 'down': {'bid': 0.5}}
    queue = {'up': 5000, 'down': 5000}  # estimated queue

    bid_history = []  # track how bids evolved

    for ts, event_type, asset_id, raw in events:
        side = token_map.get(asset_id)
        if not side:
            continue

        data = json.loads(raw)
        if isinstance(data, list):
            data = data[0] if data else {}

        elapsed = ts - window_ts
        time_remaining = max(0, 1 - elapsed / 900)  # 0 to 1

        if event_type == 'book':
            bids = data.get('bids', [])
            if bids:
                best = max(bids, key=lambda x: float(x['price']))
                book[side]['bid'] = float(best['price'])
                queue[side] = float(best['size'])

        elif event_type == 'last_trade_price':
            if data.get('side') != 'SELL':
                continue

            trade_price = float(data.get('price', 0))
            trade_size = float(data.get('size', 0))

            # track prices for volatility
            recent_prices[side].append(trade_price)
            if len(recent_prices[side]) > 20:
                recent_prices[side] = recent_prices[side][-20:]

            # calculate dynamic bids
            vol = estimate_volatility(recent_prices[side])
            up_bid, down_bid = calculate_dynamic_bids(pos, vol, time_remaining)
            our_bid = up_bid if side == 'up' else down_bid

            # record bid evolution
            if len(bid_history) == 0 or elapsed - bid_history[-1]['t'] > 30:
                bid_history.append({
                    't': elapsed,
                    'up_bid': up_bid,
                    'down_bid': down_bid,
                    'imbalance': pos.inventory_imbalance
                })

            # check for fill
            if trade_price > our_bid + 0.02:
                continue

            # queue-based fill
            if trade_size <= queue[side]:
                queue[side] -= trade_size
                continue

            overflow = trade_size - queue[side]
            available = overflow * CAPTURE_RATE
            max_qty = (MAX_CAPITAL - (pos.up_cost if side == 'up' else pos.down_cost)) / our_bid
            fill = min(available, ORDER_SIZE, max_qty)

            if fill > 0:
                cost = fill * our_bid
                if side == 'up':
                    pos.up_qty += fill
                    pos.up_cost += cost
                else:
                    pos.down_qty += fill
                    pos.down_cost += cost
                queue[side] = 0

                if verbose:
                    print(f"  {elapsed:>4.0f}s FILL {side:>4} @ {our_bid:.3f} x {fill:.0f} | "
                          f"imbalance={pos.inventory_imbalance:+.0f}")

    # calculate result
    if pos.up_qty == 0 or pos.down_qty == 0:
        return None

    matched = min(pos.up_qty, pos.down_qty)
    avg_up = pos.up_cost / pos.up_qty
    avg_down = pos.down_cost / pos.down_qty
    combined = avg_up + avg_down
    edge = 1 - combined
    pnl = matched * edge

    return {
        'window_ts': window_ts,
        'up_qty': pos.up_qty,
        'down_qty': pos.down_qty,
        'avg_up': avg_up,
        'avg_down': avg_down,
        'combined': combined,
        'edge': edge,
        'matched': matched,
        'pnl': pnl,
        'bid_history': bid_history
    }


def simulate_window_fixed(window_ts: int, fixed_bid: float = 0.48):
    """simulate fixed price MM for comparison"""

    tokens, _ = query(f"""
    SELECT token_id, side FROM token_registry
    WHERE coin='btc' AND window_ts={window_ts}
    """)
    token_map = {t[0]: t[1] for t in tokens}
    if len(token_map) < 2:
        return None

    events, _ = query(f"""
    SELECT
        toUnixTimestamp(ts) as ts,
        event_type,
        asset_id,
        raw
    FROM clob_events
    WHERE window_ts = {window_ts}
      AND event_type = 'last_trade_price'
    ORDER BY ts
    """)

    pos = Position()
    queue = {'up': 5000, 'down': 5000}

    for ts, event_type, asset_id, raw in events:
        side = token_map.get(asset_id)
        if not side:
            continue

        data = json.loads(raw)
        if isinstance(data, list):
            data = data[0] if data else {}

        if data.get('side') != 'SELL':
            continue

        trade_price = float(data.get('price', 0))
        trade_size = float(data.get('size', 0))

        if trade_price > fixed_bid + 0.02:
            continue

        if trade_size <= queue[side]:
            queue[side] -= trade_size
            continue

        overflow = trade_size - queue[side]
        available = overflow * CAPTURE_RATE
        max_qty = (MAX_CAPITAL - (pos.up_cost if side == 'up' else pos.down_cost)) / fixed_bid
        fill = min(available, ORDER_SIZE, max_qty)

        if fill > 0:
            cost = fill * fixed_bid
            if side == 'up':
                pos.up_qty += fill
                pos.up_cost += cost
            else:
                pos.down_qty += fill
                pos.down_cost += cost
            queue[side] = 0

    if pos.up_qty == 0 or pos.down_qty == 0:
        return None

    matched = min(pos.up_qty, pos.down_qty)
    combined = fixed_bid * 2
    edge = 1 - combined
    pnl = matched * edge

    return {
        'window_ts': window_ts,
        'up_qty': pos.up_qty,
        'down_qty': pos.down_qty,
        'combined': combined,
        'edge': edge,
        'matched': matched,
        'pnl': pnl
    }


def compare_strategies():
    """compare fixed vs dynamic"""

    windows, _ = query("""
    SELECT DISTINCT window_ts FROM token_registry
    WHERE coin='btc'
    ORDER BY window_ts DESC
    LIMIT 50
    """)

    print("FIXED vs DYNAMIC comparison")
    print(f"Windows: {len(windows)}")
    print(f"Fixed bid: {BASE_BID}")
    print(f"Dynamic range: {MIN_BID}-{MAX_BID}, gamma={GAMMA}")
    print()

    fixed_results = []
    dynamic_results = []

    for (window_ts,) in windows:
        fixed = simulate_window_fixed(window_ts)
        dynamic = simulate_window_dynamic(window_ts)

        if fixed:
            fixed_results.append(fixed)
        if dynamic:
            dynamic_results.append(dynamic)

        if fixed and dynamic:
            dt = datetime.utcfromtimestamp(window_ts)
            diff = dynamic['pnl'] - fixed['pnl']
            winner = 'DYN' if diff > 0 else 'FIX' if diff < 0 else 'TIE'
            print(f"{dt.strftime('%m/%d %H:%M')} | "
                  f"FIX: ${fixed['pnl']:>+5.2f} (edge={fixed['edge']*100:.1f}%) | "
                  f"DYN: ${dynamic['pnl']:>+5.2f} (edge={dynamic['edge']*100:.1f}%) | "
                  f"{winner}")

    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)

    if fixed_results:
        fixed_pnl = sum(r['pnl'] for r in fixed_results)
        fixed_edge = sum(r['edge'] for r in fixed_results) / len(fixed_results)
        print(f"FIXED:   {len(fixed_results)} windows, ${fixed_pnl:.2f} total, "
              f"avg edge={fixed_edge*100:.1f}%")

    if dynamic_results:
        dynamic_pnl = sum(r['pnl'] for r in dynamic_results)
        dynamic_edge = sum(r['edge'] for r in dynamic_results) / len(dynamic_results)
        print(f"DYNAMIC: {len(dynamic_results)} windows, ${dynamic_pnl:.2f} total, "
              f"avg edge={dynamic_edge*100:.1f}%")

    if fixed_results and dynamic_results:
        diff = sum(r['pnl'] for r in dynamic_results) - sum(r['pnl'] for r in fixed_results)
        print()
        print(f"Difference: ${diff:+.2f} ({'DYNAMIC' if diff > 0 else 'FIXED'} wins)")

        # count wins
        wins = {'fixed': 0, 'dynamic': 0, 'tie': 0}
        for f, d in zip(fixed_results, dynamic_results):
            if d['pnl'] > f['pnl']:
                wins['dynamic'] += 1
            elif f['pnl'] > d['pnl']:
                wins['fixed'] += 1
            else:
                wins['tie'] += 1
        print(f"Win rate: FIXED {wins['fixed']}, DYNAMIC {wins['dynamic']}, TIE {wins['tie']}")


if __name__ == '__main__':
    compare_strategies()
