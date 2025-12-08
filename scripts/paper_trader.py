#!/usr/bin/env python3
"""
paper trader for gabagool strategy
simulates limit order market making on both sides of 15m binary markets

key insight: we only get filled when trades happen at our price level
this uses actual trade flow from websocket to simulate realistic fills

usage: python scripts/paper_trader.py
"""
import asyncio
import json
import time
import os
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional
import aiohttp
import websockets

CLOB_WS = 'wss://ws-subscriptions-clob.polymarket.com/ws/market'
RTDS_WS = 'wss://ws-live-data.polymarket.com'
GAMMA_API = 'https://gamma-api.polymarket.com'

# strategy params (from gabagool analysis + live observation)
ORDER_SIZE = 12  # shares per order (gabagool avg ~10)
MAX_POSITION = 300  # max shares per side per window
# observed: ~91 SELLs in 3 min = ~30/min, ~520 shares/min across 4 coins
# we compete with other MMs, assume we capture 10-20% of flow when at top of book
FILL_RATE = 0.10  # assume we capture 10% of flow at our price level
MIN_EDGE = 0.01  # min 1% edge to post orders (observed edge is 1-3%)
POST_INTERVAL = 3  # seconds between order updates
REBALANCE_THRESHOLD = 0.30  # 30% imbalance triggers rebalance

RESULTS_FILE = 'data/paper_trades.jsonl'
RAW_CLOB_FILE = 'data/raw_clob.jsonl'
RAW_RTDS_FILE = 'data/raw_rtds.jsonl'


@dataclass
class Position:
    up_shares: float = 0
    up_cost: float = 0
    down_shares: float = 0
    down_cost: float = 0

    @property
    def up_avg(self) -> float:
        return self.up_cost / self.up_shares if self.up_shares > 0 else 0

    @property
    def down_avg(self) -> float:
        return self.down_cost / self.down_shares if self.down_shares > 0 else 0

    @property
    def imbalance(self) -> float:
        total = self.up_shares + self.down_shares
        if total == 0:
            return 0
        return abs(self.up_shares - self.down_shares) / total


@dataclass
class Order:
    side: str  # 'up' or 'down'
    price: float
    size: float
    ts: float


@dataclass
class Fill:
    ts: float
    side: str
    price: float
    size: float
    role: str  # 'maker' or 'taker'


@dataclass
class WindowResult:
    coin: str
    window_ts: int
    window_start: str
    window_end: str

    # position at resolution
    up_shares: float = 0
    up_cost: float = 0
    down_shares: float = 0
    down_cost: float = 0

    # fills
    maker_fills: int = 0
    taker_fills: int = 0
    total_volume: float = 0

    # edge captured
    avg_combined_bid: float = 0

    # resolution
    outcome: str = ''  # 'up' or 'down' or 'pending'
    pnl: float = 0
    pnl_pct: float = 0


@dataclass
class Market:
    coin: str
    window_ts: int
    up_token: str
    down_token: str
    slug: str

    # live orderbook state
    up_bid: float = 0
    up_ask: float = 1
    down_bid: float = 0
    down_ask: float = 1

    # our limit orders (price we're bidding at)
    our_up_bid: float = 0
    our_down_bid: float = 0

    # our position
    position: Position = field(default_factory=Position)

    # fill history
    fills: list = field(default_factory=list)

    # tracking
    combined_bids: list = field(default_factory=list)
    start_chainlink: float = 0  # price at window start
    last_order_ts: float = 0
    active: bool = True

    @property
    def combined_bid(self) -> float:
        return self.up_bid + self.down_bid

    @property
    def combined_ask(self) -> float:
        return self.up_ask + self.down_ask

    @property
    def edge(self) -> float:
        if self.combined_bid > 0 and self.combined_bid < 1:
            return 1 - self.combined_bid
        return 0

    @property
    def time_left(self) -> int:
        return max(0, self.window_ts + 900 - int(time.time()))

    @property
    def minute(self) -> int:
        elapsed = int(time.time()) - self.window_ts
        return max(0, min(14, elapsed // 60))


class PaperTrader:
    def __init__(self):
        self.markets: dict[str, Market] = {}
        self.token_map: dict[str, tuple[str, str]] = {}  # token -> (coin, side)
        self.chainlink_prices: dict[str, float] = {}
        self.results: list[WindowResult] = []
        self.running = True

        # stats
        self.total_windows = 0
        self.total_pnl = 0

        os.makedirs('data', exist_ok=True)

        # raw data capture - append mode, no processing
        self.clob_file = open(RAW_CLOB_FILE, 'a')
        self.rtds_file = open(RAW_RTDS_FILE, 'a')

    async def run(self):
        print('='*60)
        print('PAPER TRADER - Gabagool Strategy')
        print('='*60)
        print(f'Order size: {ORDER_SIZE} shares')
        print(f'Max position: {MAX_POSITION} shares/side')
        print(f'Min edge: {MIN_EDGE*100:.0f}%')
        print(f'Results: {RESULTS_FILE}')
        print()

        async with aiohttp.ClientSession() as session:
            tasks = [
                asyncio.create_task(self._market_loop(session)),
                asyncio.create_task(self._clob_loop()),
                asyncio.create_task(self._rtds_loop()),
                asyncio.create_task(self._strategy_loop()),
                asyncio.create_task(self._resolution_loop()),
                asyncio.create_task(self._display_loop()),
            ]

            try:
                await asyncio.gather(*tasks)
            except asyncio.CancelledError:
                pass

    async def _market_loop(self, session: aiohttp.ClientSession):
        """refresh markets every minute"""
        while self.running:
            await self._fetch_markets(session)
            await asyncio.sleep(60)

    async def _fetch_markets(self, session: aiohttp.ClientSession):
        now = int(time.time())

        try:
            async with session.get(f'{GAMMA_API}/events?tag_id=102467&closed=false&limit=50') as resp:
                events = await resp.json()
        except Exception as e:
            print(f'market fetch error: {e}')
            return

        import re
        new_tokens = []

        for event in events:
            slug = event.get('slug', '')
            match = re.match(r'(btc|eth|sol|xrp).*15m-(\d+)', slug.lower())
            if not match:
                continue

            coin = match.group(1)
            window_ts = int(match.group(2))

            # only active windows
            if not (window_ts <= now <= window_ts + 900):
                continue

            mkt = event.get('markets', [{}])[0]
            tokens = mkt.get('clobTokenIds')
            if isinstance(tokens, str):
                tokens = json.loads(tokens)
            if not tokens or len(tokens) < 2:
                continue

            up_token, down_token = tokens[0], tokens[1]

            # create or update market
            if coin not in self.markets or self.markets[coin].window_ts != window_ts:
                # capture starting chainlink price
                start_price = self.chainlink_prices.get(coin, 0)

                self.markets[coin] = Market(
                    coin=coin,
                    window_ts=window_ts,
                    up_token=up_token,
                    down_token=down_token,
                    slug=slug,
                    start_chainlink=start_price
                )
                print(f'[{coin}] new window {datetime.fromtimestamp(window_ts).strftime("%H:%M")} | start=${start_price:,.2f}')

            self.token_map[up_token] = (coin, 'up')
            self.token_map[down_token] = (coin, 'down')
            new_tokens.extend([up_token, down_token])

        return new_tokens

    async def _clob_loop(self):
        """websocket for orderbook updates"""
        while self.running:
            tokens = list(self.token_map.keys())
            if not tokens:
                await asyncio.sleep(2)
                continue

            try:
                async with websockets.connect(CLOB_WS) as ws:
                    await ws.send(json.dumps({
                        'type': 'subscribe',
                        'channel': 'market',
                        'assets_ids': tokens
                    }))

                    async for msg in ws:
                        if not self.running:
                            break
                        self._handle_clob(msg)

            except Exception as e:
                print(f'clob error: {e}')
                await asyncio.sleep(2)

    def _handle_clob(self, raw: str):
        # store raw first - no processing
        ts = int(time.time() * 1000)
        self.clob_file.write(json.dumps({'ts': ts, 'raw': raw}) + '\n')
        self.clob_file.flush()

        try:
            data = json.loads(raw)
            if isinstance(data, list):
                data = data[0] if data else None
            if not data:
                return

            event_type = data.get('event_type')

            if event_type == 'price_change':
                for pc in data.get('price_changes', []):
                    info = self.token_map.get(pc.get('asset_id'))
                    if not info:
                        continue

                    coin, side = info
                    market = self.markets.get(coin)
                    if not market or not market.active:
                        continue

                    new_bid = float(pc.get('best_bid', 0))
                    new_ask = float(pc.get('best_ask', 1))

                    if side == 'up':
                        market.up_bid = new_bid
                        market.up_ask = new_ask
                    else:
                        market.down_bid = new_bid
                        market.down_ask = new_ask

            elif event_type == 'last_trade_price':
                # this is the key - actual trades happening
                info = self.token_map.get(data.get('asset_id'))
                if not info:
                    return

                coin, side = info
                market = self.markets.get(coin)
                if not market or not market.active:
                    return

                trade_price = float(data.get('price', 0))
                trade_size = float(data.get('size', 0))
                trade_side = data.get('side', '')  # BUY or SELL

                # if someone SOLD, they hit a bid - could be ours
                if trade_side == 'SELL':
                    self._try_fill(market, side, trade_price, trade_size)

        except Exception as e:
            pass

    def _try_fill(self, market: Market, side: str, trade_price: float, trade_size: float):
        """
        try to fill our order based on a market trade

        observed dynamics (first 3 min of window):
        - ~30 SELLs/min, ~520 shares/min total across 4 coins
        - SELL prices range $0.30-$0.62 (mid-range, not extreme)
        - combined bid stays at $0.97-$0.99 (1-3% edge)
        - volume ramps up: min0=279, min1=463, min2=820 shares
        """
        now = time.time()

        # only try fills in accumulate phase (min 0-4, based on observation)
        # gabagool data shows 45% of trades in first 4 min
        if market.minute > 4:
            return

        # check if we have an order on this side
        if side == 'up':
            our_bid = market.our_up_bid
            current_shares = market.position.up_shares
        else:
            our_bid = market.our_down_bid
            current_shares = market.position.down_shares

        # no order posted
        if our_bid <= 0:
            return

        # already at max position
        if current_shares >= MAX_POSITION:
            return

        # trade happened at or below our bid - we get filled
        if trade_price <= our_bid:
            # we capture a fraction of the flow
            fill_size = min(
                trade_size * FILL_RATE,
                ORDER_SIZE,
                MAX_POSITION - current_shares
            )

            if fill_size < 0.1:
                return

            # record fill
            if side == 'up':
                market.position.up_shares += fill_size
                market.position.up_cost += fill_size * trade_price
            else:
                market.position.down_shares += fill_size
                market.position.down_cost += fill_size * trade_price

            market.fills.append(Fill(
                ts=now,
                side=side,
                price=trade_price,
                size=fill_size,
                role='maker'
            ))

            # log fill
            print(f'💰 [{market.coin}] FILL: {side.upper()} {fill_size:.1f} @ ${trade_price:.3f}')

    async def _rtds_loop(self):
        """websocket for chainlink prices"""
        while self.running:
            try:
                async with websockets.connect(RTDS_WS) as ws:
                    await ws.send(json.dumps({
                        'action': 'subscribe',
                        'subscriptions': [
                            {'topic': 'crypto_prices_chainlink', 'type': '*', 'filters': ''}
                        ]
                    }))

                    # ping to keep alive
                    async def ping():
                        while self.running:
                            try:
                                await ws.send('PING')
                            except:
                                break
                            await asyncio.sleep(5)

                    ping_task = asyncio.create_task(ping())

                    try:
                        async for msg in ws:
                            if not self.running:
                                break
                            if not msg.startswith('{'):
                                continue

                            # store raw first - no processing
                            ts = int(time.time() * 1000)
                            self.rtds_file.write(json.dumps({'ts': ts, 'raw': msg}) + '\n')
                            self.rtds_file.flush()

                            data = json.loads(msg)
                            if data.get('topic') == 'crypto_prices_chainlink':
                                payload = data.get('payload', {})
                                symbol = payload.get('symbol', '').lower()
                                value = payload.get('value')
                                if symbol and value:
                                    # btc/usd -> btc
                                    coin = symbol.split('/')[0]
                                    self.chainlink_prices[coin] = float(value)
                    finally:
                        ping_task.cancel()

            except Exception as e:
                print(f'rtds error: {e}')
                await asyncio.sleep(2)

    async def _strategy_loop(self):
        """main strategy execution"""
        while self.running:
            now = time.time()

            for coin, market in list(self.markets.items()):
                # skip if window ended
                if market.time_left <= 0:
                    continue

                minute = market.minute

                # PHASE 1: ACCUMULATE (min 0-4) - post limit orders
                if minute < 5:
                    await self._accumulate(market)

                # PHASE 2: REBALANCE (min 5-10) - fix imbalance if needed
                elif minute < 11:
                    await self._rebalance(market)

                # PHASE 3: HOLD (min 11-14)
                # do nothing, wait for resolution

                # track combined bid for averaging
                if market.combined_bid > 0:
                    market.combined_bids.append(market.combined_bid)

            await asyncio.sleep(1)

    async def _accumulate(self, market: Market):
        """post limit orders on both sides - orders get filled by _try_fill when trades happen"""
        now = time.time()

        # don't update orders too frequently
        if now - market.last_order_ts < POST_INTERVAL:
            return

        # check edge - only post if combined < 1
        if market.edge < MIN_EDGE:
            market.our_up_bid = 0
            market.our_down_bid = 0
            return

        # skip if bids are too low (placeholder MM orders)
        if market.up_bid < 0.10 or market.down_bid < 0.10:
            return

        # post orders at current best bid
        # (in reality we'd post at or near best bid)
        if market.position.up_shares < MAX_POSITION:
            market.our_up_bid = market.up_bid

        if market.position.down_shares < MAX_POSITION:
            market.our_down_bid = market.down_bid

        market.last_order_ts = now

    async def _rebalance(self, market: Market):
        """rebalance if position is skewed"""
        if market.position.imbalance < REBALANCE_THRESHOLD:
            return

        # determine which side is light
        if market.position.up_shares > market.position.down_shares:
            # need more DOWN
            deficit = market.position.up_shares - market.position.down_shares
            fill_size = min(ORDER_SIZE, deficit)
            if market.down_ask < 0.95:  # don't buy too expensive
                market.position.down_shares += fill_size
                market.position.down_cost += fill_size * market.down_ask
                market.fills.append(Fill(
                    ts=time.time(),
                    side='down',
                    price=market.down_ask,
                    size=fill_size,
                    role='taker'
                ))
        else:
            # need more UP
            deficit = market.position.down_shares - market.position.up_shares
            fill_size = min(ORDER_SIZE, deficit)
            if market.up_ask < 0.95:
                market.position.up_shares += fill_size
                market.position.up_cost += fill_size * market.up_ask
                market.fills.append(Fill(
                    ts=time.time(),
                    side='up',
                    price=market.up_ask,
                    size=fill_size,
                    role='taker'
                ))

    async def _resolution_loop(self):
        """check for window resolutions"""
        while self.running:
            now = int(time.time())

            for coin, market in list(self.markets.items()):
                # check if window just ended
                if market.time_left <= 0 and market.position.up_shares > 0:
                    await self._resolve_window(market)

            await asyncio.sleep(5)

    async def _resolve_window(self, market: Market):
        """calculate P&L for completed window"""
        market.active = False

        # determine outcome using chainlink price
        coin = market.coin
        current_price = self.chainlink_prices.get(coin, 0)
        start_price = market.start_chainlink

        if start_price > 0 and current_price > 0:
            if current_price >= start_price:
                outcome = 'up'
            else:
                outcome = 'down'
        else:
            # fallback to market prices
            if market.up_ask > 0.6:
                outcome = 'up'
            elif market.down_ask > 0.6:
                outcome = 'down'
            else:
                outcome = 'unknown'

        # calculate P&L
        pos = market.position
        matched = min(pos.up_shares, pos.down_shares)
        unmatched = abs(pos.up_shares - pos.down_shares)

        if outcome == 'up':
            payout = pos.up_shares * 1.0 + pos.down_shares * 0.0
        elif outcome == 'down':
            payout = pos.up_shares * 0.0 + pos.down_shares * 1.0
        else:
            payout = (pos.up_shares + pos.down_shares) * 0.5

        total_cost = pos.up_cost + pos.down_cost
        pnl = payout - total_cost
        pnl_pct = (pnl / total_cost * 100) if total_cost > 0 else 0

        # create result
        result = WindowResult(
            coin=market.coin,
            window_ts=market.window_ts,
            window_start=datetime.fromtimestamp(market.window_ts).isoformat(),
            window_end=datetime.fromtimestamp(market.window_ts + 900).isoformat(),
            up_shares=pos.up_shares,
            up_cost=pos.up_cost,
            down_shares=pos.down_shares,
            down_cost=pos.down_cost,
            maker_fills=len([f for f in market.fills if f.role == 'maker']),
            taker_fills=len([f for f in market.fills if f.role == 'taker']),
            total_volume=total_cost,
            avg_combined_bid=sum(market.combined_bids) / len(market.combined_bids) if market.combined_bids else 0,
            outcome=outcome,
            pnl=pnl,
            pnl_pct=pnl_pct
        )

        self.results.append(result)
        self.total_windows += 1
        self.total_pnl += pnl

        # write to file
        with open(RESULTS_FILE, 'a') as f:
            f.write(json.dumps(asdict(result)) + '\n')

        # log
        if pos.up_shares == 0 and pos.down_shares == 0:
            print(f'\n⚪ [{market.coin}] NO POSITION - skipped')
        else:
            emoji = '✅' if pnl > 0 else '❌' if pnl < 0 else '⚪'
            print(f'\n{emoji} [{market.coin}] RESOLVED: {outcome.upper()}')
            print(f'   Chainlink: ${start_price:,.2f} -> ${current_price:,.2f} ({(current_price/start_price-1)*100:+.2f}%)')
            print(f'   Position: {pos.up_shares:.0f} UP @ ${pos.up_avg:.3f} + {pos.down_shares:.0f} DOWN @ ${pos.down_avg:.3f}')
            print(f'   Matched: {matched:.0f} | Unmatched: {unmatched:.0f} ({pos.imbalance*100:.0f}% imbalance)')
            print(f'   Cost: ${total_cost:.2f} | Payout: ${payout:.2f} | PnL: ${pnl:+.2f} ({pnl_pct:+.1f}%)')
            print(f'   Cumulative: {self.total_windows} windows, ${self.total_pnl:+.2f}')

        # clear market
        del self.markets[market.coin]

    async def _display_loop(self):
        """periodic status display"""
        while self.running:
            await asyncio.sleep(15)

            if not self.markets:
                print(f'\n--- {datetime.now().strftime("%H:%M:%S")} --- waiting for markets...')
                continue

            print(f'\n--- {datetime.now().strftime("%H:%M:%S")} ---')

            # show chainlink prices
            prices_str = ' | '.join([f'{c.upper()}=${p:,.0f}' for c, p in sorted(self.chainlink_prices.items())])
            print(f'Chainlink: {prices_str}')

            for coin, m in sorted(self.markets.items()):
                if not m.active:
                    continue

                pos = m.position
                combined = m.combined_bid
                edge = m.edge * 100 if m.edge > 0 else 0

                # show our orders
                orders_str = ''
                if m.our_up_bid > 0 or m.our_down_bid > 0:
                    orders_str = f' | orders: UP@{m.our_up_bid:.2f} DOWN@{m.our_down_bid:.2f}'

                print(f'[{coin.upper()}] min {m.minute:>2}/{m.time_left//60}m | '
                      f'bid={combined:.3f} edge={edge:+.1f}% | '
                      f'pos: {pos.up_shares:.0f}UP/{pos.down_shares:.0f}DOWN imb={pos.imbalance*100:.0f}%{orders_str}')


def main():
    import signal

    trader = PaperTrader()

    def handle_signal(sig, frame):
        print('\nshutting down...')
        trader.running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    asyncio.run(trader.run())

    # final summary
    print('\n' + '='*60)
    print('FINAL RESULTS')
    print('='*60)
    print(f'Windows traded: {trader.total_windows}')
    print(f'Total P&L: ${trader.total_pnl:+.2f}')
    if trader.total_windows > 0:
        print(f'Avg P&L/window: ${trader.total_pnl/trader.total_windows:+.2f}')


if __name__ == '__main__':
    main()
