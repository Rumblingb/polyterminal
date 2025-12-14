#!/usr/bin/env python3
"""
BTC 15m binary market maker - multi-level grid

strategy:
- post at multiple price levels: 0.44, 0.46, 0.48
- small orders (~$5 each) to reduce queue competition
- edges: 12%, 8%, 4% respectively
- post 24h ahead for queue priority

from backtest (mm_backtest_realistic.py):
- queue 0% ahead: $188/day expected
- queue 75% ahead: $142/day expected
- unmatched fills have positive EV (buying below 0.50)
"""

import asyncio
import json
import os
import time
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from dotenv import load_dotenv

import aiohttp
import websockets
import clickhouse_connect

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

load_dotenv()

# endpoints
CLOB_WS = 'wss://ws-subscriptions-clob.polymarket.com/ws/market'
CLOB_HOST = 'https://clob.polymarket.com'
GAMMA_API = 'https://gamma-api.polymarket.com'

# clickhouse
CH_HOST = os.getenv('CLICKHOUSE_HOST', 'n60fu3ciqd.eastus2.azure.clickhouse.cloud')
CH_USER = os.getenv('CLICKHOUSE_USER', 'default')
CH_PASSWORD = os.getenv('CLICKHOUSE_PASSWORD', '')

# telegram
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')

# strategy params - multi-level grid
PRICE_LEVELS = [0.44, 0.46, 0.48]
CAPITAL_PER_SIDE = 150  # $150 per side
ORDER_SIZE = int(CAPITAL_PER_SIDE / len(PRICE_LEVELS) / 0.46)  # ~36 shares per level

# mode
PAPER_MODE = os.getenv('PAPER_MODE', 'true').lower() == 'true'


@dataclass
class PendingWindow:
    """tracks a window we've posted orders for"""
    window_ts: int
    up_token: str
    down_token: str
    # order_ids[side][price] = order_id
    order_ids: dict = field(default_factory=lambda: {'up': {}, 'down': {}})
    # fills[side][price] = filled_qty
    fills: dict = field(default_factory=lambda: {'up': {p: 0.0 for p in PRICE_LEVELS},
                                                   'down': {p: 0.0 for p in PRICE_LEVELS}})
    posted_at: float = 0
    status: str = 'pending'  # pending, active, done

    def total_filled(self, side: str) -> float:
        return sum(self.fills[side].values())

    def matched_at_level(self, price: float) -> float:
        return min(self.fills['up'][price], self.fills['down'][price])

    def total_matched(self) -> float:
        return sum(self.matched_at_level(p) for p in PRICE_LEVELS)

    def calculate_pnl(self) -> float:
        pnl = 0
        for p in PRICE_LEVELS:
            matched = self.matched_at_level(p)
            edge = 1 - p * 2
            pnl += matched * edge
        return pnl


class Trader:
    def __init__(self):
        self.windows = {}  # window_ts -> PendingWindow
        self.ch_client = None
        self.clob_client = None

    def connect_ch(self):
        if not CH_PASSWORD:
            print('[ch] disabled')
            return
        self.ch_client = clickhouse_connect.get_client(
            host=CH_HOST, port=8443,
            username=CH_USER, password=CH_PASSWORD,
            secure=True
        )
        print('[ch] connected')

    def connect_clob(self):
        self.clob_client = ClobClient(
            host=CLOB_HOST,
            key=os.getenv('PRIVATE_KEY'),
            chain_id=137,
            signature_type=1,
            funder=os.getenv('POLY_ADDRESS')
        )
        creds = ApiCreds(
            api_key=os.getenv('POLY_API_KEY'),
            api_secret=os.getenv('POLY_API_SECRET'),
            api_passphrase=os.getenv('POLY_PASSPHRASE')
        )
        self.clob_client.set_api_creds(creds)
        print(f'[clob] ready ({"PAPER" if PAPER_MODE else "LIVE"})')

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
        except:
            pass

    def log_window(self, w: PendingWindow):
        if not self.ch_client:
            return
        try:
            up_total = w.total_filled('up')
            down_total = w.total_filled('down')
            # weighted average price
            up_cost = sum(w.fills['up'][p] * p for p in PRICE_LEVELS)
            down_cost = sum(w.fills['down'][p] * p for p in PRICE_LEVELS)
            avg_up = up_cost / up_total if up_total > 0 else 0
            avg_down = down_cost / down_total if down_total > 0 else 0
            matched = w.total_matched()
            pnl = w.calculate_pnl()

            self.ch_client.insert('live_windows', [(
                w.window_ts,
                up_total,
                down_total,
                up_cost,
                down_cost,
                avg_up, avg_down,
                avg_up + avg_down if matched > 0 else 0,
                matched,
                pnl,
                2 if up_total > 0 and down_total > 0 else (1 if up_total > 0 or down_total > 0 else 0),
                0,
                1 if PAPER_MODE else 0
            )], column_names=[
                'window_ts', 'qty_up', 'qty_down', 'cost_up', 'cost_down',
                'avg_up', 'avg_down', 'combined', 'matched', 'pnl',
                'buys', 'opportunities', 'paper'
            ])
        except Exception as e:
            print(f'[ch] {e}')

    async def fetch_market(self, window_ts: int) -> dict:
        """fetch market tokens for a window"""
        slug = f'btc-updown-15m-{window_ts}'
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(f'{GAMMA_API}/markets?slug={slug}') as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    if not data:
                        return None
                    market = data[0] if isinstance(data, list) else data
                    tokens = market.get('clobTokenIds', [])
                    if isinstance(tokens, str):
                        tokens = json.loads(tokens)
                    if len(tokens) >= 2:
                        return {'up': tokens[0], 'down': tokens[1]}
            except:
                pass
        return None

    async def post_orders_for_window(self, window_ts: int, tokens: dict):
        """post limit orders at all price levels for a future window"""
        if window_ts in self.windows:
            return  # already posted

        w = PendingWindow(
            window_ts=window_ts,
            up_token=tokens['up'],
            down_token=tokens['down'],
            posted_at=time.time()
        )

        orders_posted = 0
        for side in ['up', 'down']:
            token_id = tokens[side]

            for price in PRICE_LEVELS:
                if PAPER_MODE:
                    order_id = f'paper_{side}_{price}_{window_ts}'
                    w.order_ids[side][price] = order_id
                    orders_posted += 1
                else:
                    try:
                        order = self.clob_client.create_order(OrderArgs(
                            price=price,
                            size=ORDER_SIZE,
                            side=BUY,
                            token_id=token_id
                        ))
                        resp = self.clob_client.post_order(order, OrderType.GTC)

                        if resp.get('success'):
                            w.order_ids[side][price] = resp.get('orderID', '')
                            orders_posted += 1
                        else:
                            print(f'  [ERR] {side}@{price}: {resp.get("errorMsg")}')
                    except Exception as e:
                        print(f'  [ERR] {side}@{price}: {e}')

        if orders_posted == 0:
            return

        self.windows[window_ts] = w

        dt = datetime.utcfromtimestamp(window_ts)
        hours_until = (window_ts - time.time()) / 3600
        levels_str = '/'.join([str(p) for p in PRICE_LEVELS])
        print(f'[POST] {dt.strftime("%m/%d %H:%M")} | {hours_until:.1f}h ahead | {ORDER_SIZE}x{len(PRICE_LEVELS)} @ {levels_str}')

        await self.send_telegram(
            f'<b>Orders Posted</b>\n'
            f'{dt.strftime("%m/%d %H:%M")} UTC\n'
            f'{hours_until:.1f}h ahead\n'
            f'{ORDER_SIZE} shares x {len(PRICE_LEVELS)} levels\n'
            f'Prices: {levels_str}'
        )

    async def check_and_post_future_windows(self):
        """check for markets opening 24h ahead"""
        now = int(time.time())

        # check windows for next 25 hours
        for hours_ahead in range(1, 26):
            future_ts = now + (hours_ahead * 3600)
            window_ts = future_ts - (future_ts % 900)

            if window_ts in self.windows:
                continue

            # check if market exists
            tokens = await self.fetch_market(window_ts)
            if tokens:
                await self.post_orders_for_window(window_ts, tokens)

    async def cancel_orders(self, w: PendingWindow):
        """cancel unfilled orders"""
        if PAPER_MODE:
            return

        order_ids = []
        for side in ['up', 'down']:
            for price in PRICE_LEVELS:
                order_id = w.order_ids[side].get(price)
                if order_id and w.fills[side][price] < ORDER_SIZE:
                    order_ids.append(order_id)

        if order_ids:
            try:
                self.clob_client.cancel_orders(order_ids)
            except:
                pass

    async def process_window_end(self, window_ts: int):
        """process a window that just ended"""
        if window_ts not in self.windows:
            return

        w = self.windows[window_ts]
        w.status = 'done'

        await self.cancel_orders(w)
        self.log_window(w)

        up_total = w.total_filled('up')
        down_total = w.total_filled('down')
        matched = w.total_matched()
        pnl = w.calculate_pnl()

        # breakdown by level
        level_details = []
        for p in PRICE_LEVELS:
            m = w.matched_at_level(p)
            if m > 0:
                level_details.append(f'{p}:{m:.0f}')

        dt = datetime.utcfromtimestamp(window_ts)
        details_str = ' '.join(level_details) if level_details else 'no fills'
        print(f'[DONE] {dt.strftime("%H:%M")} | up={up_total:.0f} dn={down_total:.0f} | '
              f'{details_str} | pnl=${pnl:.2f}')

        await self.send_telegram(
            f'<b>Window Complete</b>\n'
            f'{dt.strftime("%H:%M")} UTC\n'
            f'UP: {up_total:.0f} | DOWN: {down_total:.0f}\n'
            f'Matched: {matched:.0f}\n'
            f'{details_str}\n'
            f'<b>PnL: ${pnl:+.2f}</b>'
        )

        del self.windows[window_ts]

    def on_fill(self, token_id: str, trade_price: float, size: float):
        """handle fill notification - attribute to matching price level"""
        for w in self.windows.values():
            if w.status == 'done':
                continue

            side = None
            if token_id == w.up_token:
                side = 'up'
            elif token_id == w.down_token:
                side = 'down'
            else:
                continue

            # find which level this fill belongs to
            for level in PRICE_LEVELS:
                if trade_price <= level + 0.01:
                    remaining = ORDER_SIZE - w.fills[side][level]
                    if remaining > 0:
                        fill = min(size, remaining)
                        w.fills[side][level] += fill
                        total = w.total_filled(side)
                        print(f'  [FILL] {side.upper()} {fill:.0f} @ {level} | total={total:.0f}')
                    break

    async def monitor_fills(self):
        """monitor websocket for fills across all pending windows"""
        while True:
            # collect all tokens we care about
            tokens = {}
            for w in self.windows.values():
                if w.status != 'done':
                    tokens[w.up_token] = 'up'
                    tokens[w.down_token] = 'down'

            if not tokens:
                await asyncio.sleep(10)
                continue

            try:
                async with websockets.connect(CLOB_WS) as ws:
                    await ws.send(json.dumps({
                        'type': 'subscribe',
                        'channel': 'market',
                        'assets_ids': list(tokens.keys())
                    }))

                    async for raw in ws:
                        try:
                            data = json.loads(raw)
                            if isinstance(data, list):
                                data = data[0] if data else {}

                            if data.get('event_type') == 'last_trade_price':
                                if data.get('side') == 'SELL':
                                    asset_id = data.get('asset_id')
                                    price = float(data.get('price', 1))
                                    size = float(data.get('size', 0))
                                    self.on_fill(asset_id, price, size)

                        except:
                            pass

                        # check for window ends
                        now = int(time.time())
                        for window_ts in list(self.windows.keys()):
                            if now > window_ts + 900:  # window ended
                                await self.process_window_end(window_ts)

            except Exception as e:
                print(f'[ws] {e}')
                await asyncio.sleep(5)

    async def posting_loop(self):
        """periodically check for new windows to post"""
        while True:
            await self.check_and_post_future_windows()
            await asyncio.sleep(300)  # check every 5 min

    async def run(self):
        print('='*60)
        print(f'MARKET MAKER | {"PAPER" if PAPER_MODE else "LIVE"}')
        print(f'Strategy: Multi-level grid, post 24h ahead')
        print(f'Levels: {PRICE_LEVELS} (edges: {[f"{(1-p*2)*100:.0f}%" for p in PRICE_LEVELS]})')
        print(f'Size: {ORDER_SIZE} shares per level (~${ORDER_SIZE*0.46:.0f}/order)')
        print('='*60)

        self.connect_ch()
        self.connect_clob()

        levels_str = '/'.join([str(p) for p in PRICE_LEVELS])
        await self.send_telegram(
            f'<b>MM Started</b>\n'
            f'Mode: {"PAPER" if PAPER_MODE else "LIVE"}\n'
            f'Grid: {levels_str} x {ORDER_SIZE}\n'
            f'Posts 24h ahead'
        )

        # initial posting check
        await self.check_and_post_future_windows()

        # run both loops
        await asyncio.gather(
            self.posting_loop(),
            self.monitor_fills()
        )


if __name__ == '__main__':
    asyncio.run(Trader().run())
