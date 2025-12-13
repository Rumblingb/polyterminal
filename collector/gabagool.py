#!/usr/bin/env python3
"""
Gabagool Bot - snipes cheap prices on both sides of BTC 15m binaries

Strategy:
- Buy YES when cheap, buy NO when cheap (at different times)
- Goal: avg_YES + avg_NO < 1.00
- Profit = min(qty_yes, qty_no) when market resolves

Capital: $500 ($250 per side max)
Buy size: $20 per snipe
"""

import asyncio
import json
import os
import time
from datetime import datetime
from dataclasses import dataclass, field

import aiohttp
import websockets
import clickhouse_connect

# config
CLOB_WS = 'wss://ws-subscriptions-clob.polymarket.com/ws/market'
CLOB_API = 'https://clob.polymarket.com'
GAMMA_API = 'https://gamma-api.polymarket.com'

CH_HOST = os.environ.get('CLICKHOUSE_HOST', 'n60fu3ciqd.eastus2.azure.clickhouse.cloud')
CH_USER = os.environ.get('CLICKHOUSE_USER', 'default')
CH_PASSWORD = os.environ.get('CLICKHOUSE_PASSWORD', '')

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')

# strategy params
CAPITAL = 500
MAX_PER_SIDE = CAPITAL / 2  # $250
BUY_SIZE = 20  # $20 per snipe
MIN_EDGE = 0.025  # 2.5% minimum edge (combined < 0.975)
MAX_IMBALANCE = 2.0  # don't let one side exceed 2x the other
MIN_BUY_INTERVAL = 0.5  # seconds between buys (rate limit)

# modes
PAPER_MODE = True  # set False for live trading


@dataclass
class Position:
    qty_up: float = 0
    qty_down: float = 0
    cost_up: float = 0
    cost_down: float = 0
    buys: list = field(default_factory=list)  # (ts, side, price, qty, cost)

    @property
    def avg_up(self):
        return self.cost_up / self.qty_up if self.qty_up > 0 else 0.50

    @property
    def avg_down(self):
        return self.cost_down / self.qty_down if self.qty_down > 0 else 0.50

    @property
    def combined(self):
        return self.avg_up + self.avg_down

    @property
    def edge(self):
        return 1.0 - self.combined

    @property
    def matched(self):
        return min(self.qty_up, self.qty_down)

    @property
    def pnl(self):
        if self.matched == 0:
            return 0
        return self.matched * self.edge

    @property
    def locked(self):
        """true if we've locked in profit (matched > total cost)"""
        return self.matched > (self.cost_up + self.cost_down)


class GabagoolBot:
    def __init__(self):
        self.position = Position()
        self.tokens = {}  # token_id -> side ('up' or 'down')
        self.token_ids = {'up': None, 'down': None}
        self.window_ts = 0

        # current book state
        self.book = {
            'up': {'bid': 0, 'ask': 1, 'bid_size': 0, 'ask_size': 0},
            'down': {'bid': 0, 'ask': 1, 'bid_size': 0, 'ask_size': 0}
        }

        # rate limiting
        self.last_buy_time = 0

        # stats
        self.opportunities_seen = 0
        self.buys_attempted = 0
        self.buys_filled = 0

        # clickhouse
        self.ch_client = None

    def connect_ch(self):
        if not CH_PASSWORD:
            print('[ch] no password, logging disabled')
            return

        self.ch_client = clickhouse_connect.get_client(
            host=CH_HOST, port=8443,
            username=CH_USER, password=CH_PASSWORD,
            secure=True
        )

        # create trades table
        self.ch_client.command('''
            CREATE TABLE IF NOT EXISTS gabagool_trades (
                ts DateTime64(3),
                window_ts UInt32,
                side LowCardinality(String),
                price Float64,
                qty Float64,
                cost Float64,
                avg_after Float64,
                combined_after Float64,
                edge_after Float64,
                paper UInt8
            ) ENGINE = MergeTree()
            ORDER BY (window_ts, ts)
            TTL ts + INTERVAL 90 DAY
        ''')

        # create windows summary table
        self.ch_client.command('''
            CREATE TABLE IF NOT EXISTS gabagool_windows (
                window_ts UInt32,
                qty_up Float64,
                qty_down Float64,
                cost_up Float64,
                cost_down Float64,
                avg_up Float64,
                avg_down Float64,
                combined Float64,
                matched Float64,
                pnl Float64,
                buys Int32,
                opportunities Int32,
                paper UInt8
            ) ENGINE = ReplacingMergeTree()
            ORDER BY window_ts
        ''')

        print(f'[ch] connected')

    def log_trade(self, side: str, price: float, qty: float, cost: float):
        if not self.ch_client:
            return

        try:
            self.ch_client.insert('gabagool_trades', [(
                datetime.utcnow(),
                self.window_ts,
                side,
                price,
                qty,
                cost,
                self.position.avg_up if side == 'up' else self.position.avg_down,
                self.position.combined,
                self.position.edge,
                1 if PAPER_MODE else 0
            )], column_names=[
                'ts', 'window_ts', 'side', 'price', 'qty', 'cost',
                'avg_after', 'combined_after', 'edge_after', 'paper'
            ])
        except Exception as e:
            print(f'[ch] log error: {e}')

    def log_window(self):
        if not self.ch_client:
            return

        try:
            self.ch_client.insert('gabagool_windows', [(
                self.window_ts,
                self.position.qty_up,
                self.position.qty_down,
                self.position.cost_up,
                self.position.cost_down,
                self.position.avg_up,
                self.position.avg_down,
                self.position.combined,
                self.position.matched,
                self.position.pnl,
                len(self.position.buys),
                self.opportunities_seen,
                1 if PAPER_MODE else 0
            )], column_names=[
                'window_ts', 'qty_up', 'qty_down', 'cost_up', 'cost_down',
                'avg_up', 'avg_down', 'combined', 'matched', 'pnl',
                'buys', 'opportunities', 'paper'
            ])
        except Exception as e:
            print(f'[ch] log window error: {e}')

    async def send_telegram(self, msg: str):
        if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
            return
        try:
            url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
            async with aiohttp.ClientSession() as session:
                await session.post(url, json={
                    'chat_id': TELEGRAM_CHAT_ID,
                    'text': msg,
                    'parse_mode': 'HTML'
                })
        except Exception as e:
            print(f'[tg] error: {e}')

    async def fetch_tokens(self, window_ts: int) -> bool:
        """fetch UP/DOWN token IDs for this window"""
        slug = f'btc-updown-15m-{window_ts}'

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(f'{GAMMA_API}/markets?slug={slug}') as resp:
                    if resp.status != 200:
                        return False

                    data = await resp.json()
                    if not data:
                        return False

                    market = data[0] if isinstance(data, list) else data
                    tokens = market.get('clobTokenIds', [])
                    if isinstance(tokens, str):
                        tokens = json.loads(tokens)

                    if len(tokens) >= 2:
                        self.token_ids = {'up': tokens[0], 'down': tokens[1]}
                        self.tokens = {tokens[0]: 'up', tokens[1]: 'down'}
                        return True
            except Exception as e:
                print(f'[gamma] error: {e}')

        return False

    def should_buy(self, side: str) -> tuple[bool, str]:
        """check if we should buy this side"""
        now = time.time()

        # rate limit
        if now - self.last_buy_time < MIN_BUY_INTERVAL:
            return False, 'rate_limit'

        # capital check
        current_cost = self.position.cost_up if side == 'up' else self.position.cost_down
        if current_cost >= MAX_PER_SIDE:
            return False, 'max_capital'

        # get current ask
        ask = self.book[side]['ask']
        if ask <= 0 or ask >= 1:
            return False, 'invalid_ask'

        # calculate new average if we buy
        current_qty = self.position.qty_up if side == 'up' else self.position.qty_down
        current_cost = self.position.cost_up if side == 'up' else self.position.cost_down

        new_qty = current_qty + (BUY_SIZE / ask)
        new_cost = current_cost + BUY_SIZE
        new_avg = new_cost / new_qty

        # other side average
        other_side = 'down' if side == 'up' else 'up'
        other_qty = self.position.qty_down if side == 'up' else self.position.qty_up
        other_avg = self.position.avg_down if side == 'up' else self.position.avg_up

        # edge check
        new_combined = new_avg + other_avg
        if new_combined >= (1 - MIN_EDGE):
            return False, f'no_edge_{new_combined:.3f}'

        # balance check
        if other_qty > 0:
            new_ratio = new_qty / other_qty
            if new_ratio > MAX_IMBALANCE:
                return False, f'imbalance_{new_ratio:.1f}'

        return True, 'ok'

    async def execute_buy(self, side: str):
        """execute a buy order"""
        ask = self.book[side]['ask']
        qty = BUY_SIZE / ask

        if PAPER_MODE:
            # paper trade - instant fill at ask
            if side == 'up':
                self.position.qty_up += qty
                self.position.cost_up += BUY_SIZE
            else:
                self.position.qty_down += qty
                self.position.cost_down += BUY_SIZE

            self.position.buys.append((time.time(), side, ask, qty, BUY_SIZE))
            self.buys_filled += 1

            # log
            self.log_trade(side, ask, qty, BUY_SIZE)

            print(f'  [BUY] {side.upper()} @ {ask:.3f} | '
                  f'qty={qty:.0f} | combined={self.position.combined:.3f} | '
                  f'edge={self.position.edge*100:.1f}%')
        else:
            # live trade - place market order
            # TODO: implement actual order placement
            pass

        self.last_buy_time = time.time()
        self.buys_attempted += 1

    def on_price_update(self, side: str, bid: float, ask: float, bid_size: float, ask_size: float):
        """handle price update from websocket"""
        self.book[side] = {
            'bid': bid, 'ask': ask,
            'bid_size': bid_size, 'ask_size': ask_size
        }

        # check if opportunity exists (combined ask < threshold)
        combined_ask = self.book['up']['ask'] + self.book['down']['ask']
        if combined_ask < (1 - MIN_EDGE):
            self.opportunities_seen += 1

    async def check_and_buy(self):
        """check both sides and buy if conditions met"""
        for side in ['up', 'down']:
            should, reason = self.should_buy(side)
            if should:
                await self.execute_buy(side)

    async def run_window(self, window_ts: int):
        """run strategy for one window"""
        self.window_ts = window_ts
        self.position = Position()
        self.opportunities_seen = 0
        self.buys_attempted = 0
        self.buys_filled = 0
        self.book = {
            'up': {'bid': 0, 'ask': 1, 'bid_size': 0, 'ask_size': 0},
            'down': {'bid': 0, 'ask': 1, 'bid_size': 0, 'ask_size': 0}
        }

        window_end = window_ts + 900  # 15 min
        dt = datetime.utcfromtimestamp(window_ts)

        print(f'\n{"="*60}')
        print(f'GABAGOOL | {dt.strftime("%H:%M")} UTC | {"PAPER" if PAPER_MODE else "LIVE"}')
        print(f'{"="*60}')

        # fetch tokens
        if not await self.fetch_tokens(window_ts):
            print('[!] no tokens found')
            return

        print(f'[tokens] UP={self.token_ids["up"][:16]}... DOWN={self.token_ids["down"][:16]}...')

        # connect to websocket
        try:
            async with websockets.connect(CLOB_WS) as ws:
                # subscribe
                await ws.send(json.dumps({
                    'type': 'subscribe',
                    'channel': 'market',
                    'assets_ids': list(self.tokens.keys())
                }))
                print('[ws] connected')

                async for raw in ws:
                    now = time.time()
                    if now >= window_end - 30:  # stop 30s before end
                        break

                    try:
                        data = json.loads(raw)
                        if isinstance(data, list):
                            data = data[0] if data else {}

                        event_type = data.get('event_type')

                        if event_type == 'price_change':
                            for pc in data.get('price_changes', []):
                                asset_id = pc.get('asset_id')
                                side = self.tokens.get(asset_id)
                                if side:
                                    bid = float(pc.get('best_bid', 0))
                                    ask = float(pc.get('best_ask', 1))
                                    bid_size = float(pc.get('best_bid_size', 0))
                                    ask_size = float(pc.get('best_ask_size', 0))

                                    self.on_price_update(side, bid, ask, bid_size, ask_size)
                                    await self.check_and_buy()

                        elif event_type == 'book':
                            asset_id = data.get('asset_id')
                            side = self.tokens.get(asset_id)
                            if side:
                                asks = data.get('asks', [])
                                bids = data.get('bids', [])
                                if asks:
                                    best_ask = min(asks, key=lambda x: float(x['price']))
                                    ask = float(best_ask['price'])
                                    ask_size = float(best_ask['size'])
                                else:
                                    ask, ask_size = 1, 0
                                if bids:
                                    best_bid = max(bids, key=lambda x: float(x['price']))
                                    bid = float(best_bid['price'])
                                    bid_size = float(best_bid['size'])
                                else:
                                    bid, bid_size = 0, 0

                                self.on_price_update(side, bid, ask, bid_size, ask_size)
                                await self.check_and_buy()

                    except Exception as e:
                        pass  # ignore parse errors

        except Exception as e:
            print(f'[ws] error: {e}')

        # window complete - log results
        self.log_window()

        # summary
        print(f'\n[RESULT]')
        print(f'  UP:   {self.position.qty_up:.0f} shares @ {self.position.avg_up:.3f} avg (${self.position.cost_up:.0f})')
        print(f'  DOWN: {self.position.qty_down:.0f} shares @ {self.position.avg_down:.3f} avg (${self.position.cost_down:.0f})')
        print(f'  Combined: {self.position.combined:.3f} | Edge: {self.position.edge*100:.1f}%')
        print(f'  Matched: {self.position.matched:.0f} | PnL: ${self.position.pnl:.2f}')
        print(f'  Buys: {self.buys_filled} | Opportunities: {self.opportunities_seen}')

        # telegram
        msg = (
            f'<b>Gabagool {dt.strftime("%H:%M")} UTC</b>\n'
            f'{"📝 PAPER" if PAPER_MODE else "💰 LIVE"}\n\n'
            f'UP: {self.position.qty_up:.0f} @ {self.position.avg_up:.3f}\n'
            f'DOWN: {self.position.qty_down:.0f} @ {self.position.avg_down:.3f}\n'
            f'Combined: {self.position.combined:.3f}\n'
            f'<b>PnL: ${self.position.pnl:+.2f}</b>\n\n'
            f'Buys: {self.buys_filled} | Opps: {self.opportunities_seen}'
        )
        await self.send_telegram(msg)

    async def run(self):
        print('='*60)
        print(f'GABAGOOL BOT | {"PAPER MODE" if PAPER_MODE else "LIVE MODE"}')
        print(f'Capital: ${CAPITAL} | Buy size: ${BUY_SIZE}')
        print(f'Min edge: {MIN_EDGE*100:.1f}% | Max imbalance: {MAX_IMBALANCE}x')
        print('='*60)

        self.connect_ch()

        # startup message
        await self.send_telegram(
            f'<b>Gabagool Started</b>\n'
            f'Mode: {"PAPER" if PAPER_MODE else "LIVE"}\n'
            f'Capital: ${CAPITAL}\n'
            f'Min edge: {MIN_EDGE*100:.1f}%'
        )

        # check if we should join current window
        now = int(time.time())
        current_window = now - (now % 900)
        elapsed = now - current_window

        if elapsed < 600:  # join if < 10 min in
            print(f'[startup] joining current window ({elapsed}s in)')
            await self.run_window(current_window)

        # main loop
        while True:
            now = int(time.time())
            current_window = now - (now % 900)
            next_window = current_window + 900
            wait = next_window - now

            print(f'\n[wait] {wait}s until next window')

            if wait > 5:
                await asyncio.sleep(wait - 5)

            await self.run_window(next_window)


if __name__ == '__main__':
    asyncio.run(GabagoolBot().run())
