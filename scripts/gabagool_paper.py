#!/usr/bin/env python3
"""
Gabagool Paper Trader - simulates passive limit order market making

Strategy:
1. Post limit BUY on both UP and DOWN at best_bid
2. When combined_bid < 0.98, we have edge
3. Get filled when retail SELLs into our bids
4. Hold to resolution, one side pays $1.00
5. Profit = matched_shares * (1 - combined_bid)
"""

import asyncio
import json
import time
from datetime import datetime
from dataclasses import dataclass, field

import aiohttp
import websockets

GAMMA_API = 'https://gamma-api.polymarket.com'
CLOB_WS = 'wss://ws-subscriptions-clob.polymarket.com/ws/market'
CRYPTO_PRICE_API = 'https://polymarket.com/api/crypto/crypto-price'

COINS = ['btc', 'eth', 'sol', 'xrp']
MIN_EDGE = 0.02  # 2% minimum edge to enter
MAX_IMBALANCE = 0.20  # 20% max position imbalance
POSITION_SIZE = 100  # shares per side target


@dataclass
class Position:
    coin: str
    up_shares: float = 0
    down_shares: float = 0
    up_cost: float = 0
    down_cost: float = 0
    up_bid: float = 0.5
    down_bid: float = 0.5
    filled_up: bool = False
    filled_down: bool = False

    @property
    def combined_bid(self):
        return self.up_bid + self.down_bid

    @property
    def edge(self):
        return max(0, 1 - self.combined_bid)

    @property
    def imbalance(self):
        total = self.up_shares + self.down_shares
        if total == 0:
            return 0
        return abs(self.up_shares - self.down_shares) / total

    @property
    def matched_shares(self):
        return min(self.up_shares, self.down_shares)

    def pnl(self, outcome: str) -> float:
        """calculate P&L given outcome"""
        if outcome == 'up':
            # UP pays $1, DOWN pays $0
            return self.up_shares * 1.0 - self.up_cost - self.down_cost
        else:
            # DOWN pays $1, UP pays $0
            return self.down_shares * 1.0 - self.up_cost - self.down_cost


@dataclass
class WindowResult:
    window_ts: int
    coin: str
    up_shares: float
    down_shares: float
    up_cost: float
    down_cost: float
    combined_bid: float
    outcome: str
    pnl: float


class GabagoolPaper:
    def __init__(self):
        self.positions: dict[str, Position] = {}
        self.tokens: dict[str, tuple[str, str]] = {}  # token_id -> (coin, side)
        self.market_times: dict[str, tuple[str, str]] = {}  # coin -> (start, end)
        self.results: list[WindowResult] = []
        self.window_ts = 0

    async def fetch_markets(self, window_ts: int):
        """fetch market data for all coins"""
        self.tokens.clear()
        self.positions.clear()
        self.market_times.clear()

        async with aiohttp.ClientSession() as session:
            for coin in COINS:
                slug = f'{coin}-updown-15m-{window_ts}'
                try:
                    async with session.get(f'{GAMMA_API}/markets?slug={slug}') as resp:
                        if resp.status != 200:
                            continue

                        data = await resp.json()
                        if not data:
                            continue
                        data = data[0] if isinstance(data, list) else data

                        tokens = data.get('clobTokenIds', [])
                        if isinstance(tokens, str):
                            tokens = json.loads(tokens)
                        if len(tokens) < 2:
                            continue

                        up_token, down_token = tokens[0], tokens[1]
                        self.tokens[up_token] = (coin, 'up')
                        self.tokens[down_token] = (coin, 'down')
                        self.positions[coin] = Position(coin=coin)

                        start_time = data.get('eventStartTime') or data.get('startDate')
                        end_time = data.get('endDate')
                        if start_time and end_time:
                            self.market_times[coin] = (start_time, end_time)

                        print(f'  [{coin}] {slug}')

                except Exception as e:
                    print(f'  [{coin}] error: {e}')

        print(f'[markets] {len(self.positions)} coins, {len(self.tokens)} tokens')

    async def fetch_resolution(self, window_ts: int):
        """fetch resolution outcomes"""
        print('[resolution] fetching...')
        await asyncio.sleep(30)  # wait for resolution

        async with aiohttp.ClientSession() as session:
            for coin, times in self.market_times.items():
                start_time, end_time = times
                try:
                    url = f'{CRYPTO_PRICE_API}?symbol={coin.upper()}&eventStartTime={start_time}&variant=fifteen&endDate={end_time}'
                    async with session.get(url) as resp:
                        if resp.status != 200:
                            continue

                        data = await resp.json()
                        op = data.get('openPrice')
                        cp = data.get('closePrice')

                        if op is None or cp is None:
                            continue

                        outcome = 'up' if cp > op else 'down'
                        pos = self.positions.get(coin)
                        if pos:
                            pnl = pos.pnl(outcome)
                            result = WindowResult(
                                window_ts=window_ts,
                                coin=coin,
                                up_shares=pos.up_shares,
                                down_shares=pos.down_shares,
                                up_cost=pos.up_cost,
                                down_cost=pos.down_cost,
                                combined_bid=pos.combined_bid,
                                outcome=outcome,
                                pnl=pnl
                            )
                            self.results.append(result)

                            print(f'  [{coin}] {outcome.upper()} | up={pos.up_shares:.0f} down={pos.down_shares:.0f} | pnl=${pnl:+.2f}')

                except Exception as e:
                    print(f'  [{coin}] error: {e}')

    async def run_window(self, window_ts: int):
        """run paper trading for one window"""
        window_end = window_ts + 900

        print(f'\n{"="*60}')
        print(f'WINDOW {window_ts} | {datetime.utcfromtimestamp(window_ts).strftime("%H:%M:%S")} UTC')
        print(f'{"="*60}')

        self.window_ts = window_ts
        await self.fetch_markets(window_ts)

        if not self.tokens:
            print('[!] no markets found')
            return

        token_ids = list(self.tokens.keys())
        accumulate_end = window_ts + 240  # first 4 minutes

        try:
            async with websockets.connect(CLOB_WS) as ws:
                await ws.send(json.dumps({
                    'type': 'subscribe',
                    'channel': 'market',
                    'assets_ids': token_ids
                }))
                print('[clob] connected')

                last_log = time.time()

                async for raw in ws:
                    now = time.time()
                    if now >= window_end:
                        break

                    try:
                        data = json.loads(raw)
                        if isinstance(data, list):
                            data = data[0] if data else {}

                        event_type = data.get('event_type', '')

                        # update best bids from price_change
                        if event_type == 'price_change':
                            for pc in data.get('price_changes', []):
                                token_id = pc.get('asset_id')
                                info = self.tokens.get(token_id)
                                if not info:
                                    continue

                                coin, side = info
                                pos = self.positions.get(coin)
                                if not pos:
                                    continue

                                bid = float(pc.get('best_bid', 0) or 0)
                                if side == 'up':
                                    pos.up_bid = bid
                                else:
                                    pos.down_bid = bid

                        # simulate fills from last_trade_price SELL events
                        elif event_type == 'last_trade_price':
                            trade_side = data.get('side', '')
                            if trade_side != 'SELL':
                                continue  # only SELL fills our bids

                            token_id = data.get('asset_id')
                            info = self.tokens.get(token_id)
                            if not info:
                                continue

                            coin, side = info
                            pos = self.positions.get(coin)
                            if not pos:
                                continue

                            # only accumulate in first 4 minutes
                            if now < accumulate_end:
                                # check if we have edge
                                if pos.edge < MIN_EDGE:
                                    continue

                                price = float(data.get('price', 0))
                                size = float(data.get('size', 0))

                                # simulate partial fill (we get portion of trade)
                                fill_size = min(size * 0.1, POSITION_SIZE - (pos.up_shares if side == 'up' else pos.down_shares))
                                if fill_size <= 0:
                                    continue

                                if side == 'up':
                                    pos.up_shares += fill_size
                                    pos.up_cost += fill_size * price
                                else:
                                    pos.down_shares += fill_size
                                    pos.down_cost += fill_size * price

                        # log every 30s
                        if now - last_log >= 30:
                            elapsed = int(now - window_ts)
                            min_sec = f'{elapsed // 60}:{elapsed % 60:02d}'

                            status = []
                            for coin, pos in sorted(self.positions.items()):
                                if pos.up_shares > 0 or pos.down_shares > 0:
                                    status.append(f'{coin}:{pos.up_shares:.0f}/{pos.down_shares:.0f}')

                            if status:
                                print(f'[{min_sec}] {" ".join(status)}')
                            last_log = now

                    except Exception:
                        pass

        except Exception as e:
            print(f'[clob] error: {e}')

        # show final positions
        print(f'\n[positions]')
        for coin, pos in sorted(self.positions.items()):
            if pos.up_shares > 0 or pos.down_shares > 0:
                total_cost = pos.up_cost + pos.down_cost
                avg_combined = total_cost / max(pos.matched_shares, 1)
                edge = (1 - avg_combined) * 100 if pos.matched_shares > 0 else 0
                print(f'  {coin}: up={pos.up_shares:.0f} down={pos.down_shares:.0f} cost=${total_cost:.2f} edge={edge:+.1f}%')

        # get resolution
        await self.fetch_resolution(window_ts)

    def print_summary(self):
        """print overall summary"""
        if not self.results:
            print('\n[summary] no results')
            return

        total_pnl = sum(r.pnl for r in self.results)
        wins = sum(1 for r in self.results if r.pnl > 0)
        losses = sum(1 for r in self.results if r.pnl < 0)

        print(f'\n{"="*60}')
        print(f'PAPER TRADING SUMMARY')
        print(f'{"="*60}')
        print(f'  Windows: {len(self.results)}')
        print(f'  Wins: {wins} | Losses: {losses}')
        print(f'  Total P&L: ${total_pnl:+.2f}')
        print(f'  Avg P&L per window: ${total_pnl / len(self.results):+.2f}')

        # per-coin breakdown
        print(f'\n  By coin:')
        for coin in COINS:
            coin_results = [r for r in self.results if r.coin == coin]
            if coin_results:
                coin_pnl = sum(r.pnl for r in coin_results)
                print(f'    {coin}: ${coin_pnl:+.2f} ({len(coin_results)} windows)')

    async def run(self, num_windows: int = 1):
        """run paper trading for multiple windows"""
        print('='*60)
        print('GABAGOOL PAPER TRADER')
        print(f'Strategy: Passive limit order market making')
        print(f'Min edge: {MIN_EDGE*100:.0f}% | Max imbalance: {MAX_IMBALANCE*100:.0f}%')
        print('='*60)

        for i in range(num_windows):
            now = int(time.time())
            current_window = now - (now % 900)
            next_window = current_window + 900
            wait = next_window - now

            print(f'\n[wait] {wait}s until window {i+1}/{num_windows}')

            if wait > 5:
                await asyncio.sleep(wait - 5)

            await self.run_window(next_window)

        self.print_summary()


if __name__ == '__main__':
    import sys
    num_windows = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    asyncio.run(GabagoolPaper().run(num_windows))
