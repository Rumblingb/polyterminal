#!/usr/bin/env python3
"""
Paper Trader - multi-level grid strategy

Posts at fixed price levels: 0.44, 0.46, 0.48
Small orders (~$5 each) per level
Edges: 12%, 8%, 4% respectively

From backtest (mm_backtest_realistic.py):
- Queue 0% ahead: $188/day expected
- Queue 75% ahead: $142/day expected
- Unmatched fills have positive EV (buying below 0.50)
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

GAMMA_API = 'https://gamma-api.polymarket.com'

# clickhouse
CH_HOST = os.environ.get('CLICKHOUSE_HOST', 'n60fu3ciqd.eastus2.azure.clickhouse.cloud')
CH_USER = os.environ.get('CLICKHOUSE_USER', 'default')
CH_PASSWORD = os.environ.get('CLICKHOUSE_PASSWORD', '')
CLOB_WS = 'wss://ws-subscriptions-clob.polymarket.com/ws/market'

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')

COINS = ['btc']

# multi-level grid params
PRICE_LEVELS = [0.44, 0.46, 0.48]
CAPITAL_PER_SIDE = 150  # $150 per side
ORDER_SIZE = int(CAPITAL_PER_SIDE / len(PRICE_LEVELS) / 0.46)  # ~36 shares per level
CAPTURE_RATE = 0.15  # 15% of overflow


@dataclass
class CoinBook:
    coin: str
    up_token: str = ''
    down_token: str = ''
    # fills[side][price] = qty
    fills: dict = field(default_factory=lambda: {
        'up': {p: 0.0 for p in PRICE_LEVELS},
        'down': {p: 0.0 for p in PRICE_LEVELS}
    })
    # queue depth at each level from book snapshots
    queue: dict = field(default_factory=lambda: {
        'up': {p: 3000.0 for p in PRICE_LEVELS},
        'down': {p: 3000.0 for p in PRICE_LEVELS}
    })
    # market totals
    market_up_sells: float = 0
    market_down_sells: float = 0
    trade_count: int = 0
    latency_samples: list = field(default_factory=list)

    def total_filled(self, side: str) -> float:
        return sum(self.fills[side].values())

    def matched_at_level(self, price: float) -> float:
        return min(self.fills['up'][price], self.fills['down'][price])

    def total_matched(self) -> float:
        return sum(self.matched_at_level(p) for p in PRICE_LEVELS)

    def try_fill(self, side: str, trade_price: float, size: float) -> tuple:
        """try to fill at each price level"""
        filled_at = []

        for level in PRICE_LEVELS:
            # trade must reach our level
            if trade_price > level + 0.01:
                continue

            remaining = ORDER_SIZE - self.fills[side][level]
            if remaining <= 0:
                continue

            # queue-based fill
            queue = self.queue[side][level]
            if size <= queue:
                continue

            overflow = size - queue
            available = overflow * CAPTURE_RATE
            fill = min(available, remaining)

            if fill > 0:
                self.fills[side][level] += fill
                filled_at.append((level, fill))

        return filled_at

    def add_market_sell(self, side: str, size: float):
        if side == 'up':
            self.market_up_sells += size
        else:
            self.market_down_sells += size

    def update_queue(self, side: str, bids: list):
        """update queue depth from book snapshot"""
        for level in PRICE_LEVELS:
            depth = sum(float(b['size']) for b in bids if float(b['price']) >= level)
            if depth > 0:
                self.queue[side][level] = depth * 1.2  # safety margin

    def calc_pnl(self):
        """calculate pnl per level"""
        total_pnl = 0
        for p in PRICE_LEVELS:
            matched = self.matched_at_level(p)
            edge = 1 - p * 2
            total_pnl += matched * edge

        up_total = self.total_filled('up')
        down_total = self.total_filled('down')
        up_cost = sum(self.fills['up'][p] * p for p in PRICE_LEVELS)
        down_cost = sum(self.fills['down'][p] * p for p in PRICE_LEVELS)

        return total_pnl, up_total, down_total, up_cost + down_cost


class PaperTrader:
    def __init__(self):
        self.tokens = {}
        self.books = {}
        self.results = []
        self.session_pnl = 0
        self.session_capital = 0
        self.client = None
        self.fill_buffer = []

    def connect_ch(self):
        if not CH_PASSWORD:
            print('[ch] no password, dry-run mode')
            return False

        self.client = clickhouse_connect.get_client(
            host=CH_HOST,
            port=8443,
            username=CH_USER,
            password=CH_PASSWORD,
            secure=True
        )
        print(f'[ch] connected to {CH_HOST}')
        self.init_tables()
        return True

    def init_tables(self):
        # limit orders we'd post
        self.client.command('''
            CREATE TABLE IF NOT EXISTS paper_orders (
                ts DateTime64(3),
                window_ts UInt32,
                coin LowCardinality(String),
                side LowCardinality(String),
                price Float64,
                size Float64,
                cost Float64
            ) ENGINE = MergeTree()
            PARTITION BY toYYYYMMDD(ts)
            ORDER BY (window_ts, coin, side, ts)
            TTL ts + INTERVAL 90 DAY
        ''')

        # individual fills
        self.client.command('''
            CREATE TABLE IF NOT EXISTS paper_fills (
                ts DateTime64(3),
                window_ts UInt32,
                coin LowCardinality(String),
                side LowCardinality(String),
                price Float64,
                size Float64,
                cost Float64
            ) ENGINE = MergeTree()
            PARTITION BY toYYYYMMDD(ts)
            ORDER BY (window_ts, coin, side, ts)
            TTL ts + INTERVAL 90 DAY
        ''')

        # window summaries
        self.client.command('''
            CREATE TABLE IF NOT EXISTS paper_windows (
                ts DateTime64(3),
                window_ts UInt32,
                coin LowCardinality(String),
                up_shares Float64,
                down_shares Float64,
                up_cost Float64,
                down_cost Float64,
                pnl Float64,
                avg_edge Float64,
                market_up_sells Float64,
                market_down_sells Float64
            ) ENGINE = MergeTree()
            PARTITION BY toYYYYMMDD(ts)
            ORDER BY (window_ts, coin)
            TTL ts + INTERVAL 90 DAY
        ''')

        print('[ch] paper tables ready')

    def store_fill(self, window_ts: int, coin: str, side: str, price: float, size: float):
        if not self.client:
            return
        self.fill_buffer.append((
            datetime.utcnow(),
            window_ts,
            coin,
            side,
            price,
            size,
            price * size
        ))

    def flush_fills(self):
        if not self.client or not self.fill_buffer:
            return
        try:
            self.client.insert('paper_fills', self.fill_buffer,
                column_names=['ts', 'window_ts', 'coin', 'side', 'price', 'size', 'cost'])
            self.fill_buffer.clear()
        except Exception as e:
            print(f'[ch] fill flush error: {e}')

    def store_window(self, window_ts: int, coin: str, book):
        if not self.client:
            return
        pnl, up_shares, down_shares, total_cost = book.calc_pnl()
        up_cost = sum(book.fills['up'][p] * p for p in PRICE_LEVELS)
        down_cost = sum(book.fills['down'][p] * p for p in PRICE_LEVELS)
        avg_edge = (1 - (up_cost + down_cost) / (up_shares + down_shares)) if (up_shares + down_shares) > 0 else 0

        try:
            self.client.insert('paper_windows', [(
                datetime.utcnow(),
                window_ts,
                coin,
                up_shares,
                down_shares,
                up_cost,
                down_cost,
                pnl,
                avg_edge,
                book.market_up_sells,
                book.market_down_sells
            )], column_names=['ts', 'window_ts', 'coin', 'up_shares', 'down_shares',
                              'up_cost', 'down_cost', 'pnl', 'avg_edge',
                              'market_up_sells', 'market_down_sells'])
        except Exception as e:
            print(f'[ch] window store error: {e}')

    async def send_telegram(self, msg: str):
        if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
            print(f'[tg] {msg[:100]}...')
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

    async def fetch_markets(self, window_ts: int):
        self.tokens.clear()
        self.books.clear()

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

                        market = data[0] if isinstance(data, list) else data
                        tokens = market.get('clobTokenIds', [])
                        if isinstance(tokens, str):
                            tokens = json.loads(tokens)

                        if len(tokens) >= 2:
                            up_token, down_token = tokens[0], tokens[1]
                            self.tokens[up_token] = (coin, 'up')
                            self.tokens[down_token] = (coin, 'down')
                            self.books[coin] = CoinBook(coin=coin, up_token=up_token, down_token=down_token)

                except Exception as e:
                    print(f'[paper] {coin} error: {e}')

    async def run_window(self, window_ts: int):
        window_end = window_ts + 900
        accumulate_end = window_ts + 540  # first 9 min

        dt = datetime.utcfromtimestamp(window_ts)
        print(f'\n[paper] window {dt.strftime("%H:%M")} UTC')

        await self.fetch_markets(window_ts)

        if not self.tokens:
            return

        fills_log = []

        try:
            async with websockets.connect(CLOB_WS) as ws:
                await ws.send(json.dumps({
                    'type': 'subscribe',
                    'channel': 'market',
                    'assets_ids': list(self.tokens.keys())
                }))

                async for raw in ws:
                    now = time.time()
                    if now >= window_end:
                        break

                    try:
                        data = json.loads(raw)
                        if isinstance(data, list):
                            data = data[0] if data else {}

                        event_type = data.get('event_type')
                        elapsed = now - window_ts

                        # calc latency from event timestamp
                        event_ts = data.get('timestamp')
                        if event_ts:
                            latency_ms = (now * 1000) - int(event_ts)
                            aid = data.get('asset_id')
                            if aid:
                                info = self.tokens.get(aid)
                                if info:
                                    book = self.books.get(info[0])
                                    if book:
                                        book.latency_samples.append(latency_ms)

                        if event_type == 'book':
                            asset_id = data.get('asset_id')
                            info = self.tokens.get(asset_id)
                            if info:
                                coin, side = info
                                book = self.books.get(coin)
                                if book:
                                    bids = data.get('bids', [])
                                    if bids:
                                        book.update_queue(side, bids)

                        elif event_type == 'last_trade_price':
                            info = self.tokens.get(data.get('asset_id'))
                            if not info:
                                continue

                            coin, side = info
                            book = self.books.get(coin)
                            if not book:
                                continue

                            price = float(data.get('price', 0))
                            size = float(data.get('size', 0))
                            trade_side = data.get('side', '')

                            book.trade_count += 1

                            if trade_side == 'SELL':
                                book.add_market_sell(side, size)

                                if now < accumulate_end:
                                    filled_at = book.try_fill(side, price, size)
                                    for level, fill in filled_at:
                                        fills_log.append(f'{elapsed:.0f}s {side}@{level} x{fill:.0f}')
                                        self.store_fill(window_ts, coin, side, level, fill)

                    except:
                        pass

        except Exception as e:
            print(f'[paper] ws error: {e}')

        # calculate results
        total_pnl = 0
        total_capital = 0
        lines = [f'<b>Window {dt.strftime("%H:%M")} UTC</b>']
        levels_str = '/'.join([str(p) for p in PRICE_LEVELS])
        lines.append(f'<i>Grid: {levels_str} x {ORDER_SIZE}</i>\n')

        for coin, book in sorted(self.books.items()):
            pnl, up_shares, down_shares, capital = book.calc_pnl()
            total_pnl += pnl
            total_capital += capital

            # latency stats
            lat = book.latency_samples
            avg_lat = sum(lat) / len(lat) if lat else 0

            if up_shares == 0 and down_shares == 0:
                lines.append(f'<b>{coin.upper()}</b>: no fills (lat {avg_lat:.0f}ms)')
                continue

            matched = book.total_matched()

            # breakdown by level
            level_details = []
            for p in PRICE_LEVELS:
                m = book.matched_at_level(p)
                if m > 0:
                    level_details.append(f'{p}:{m:.0f}')

            emoji = '+' if pnl > 0 else '-' if pnl < 0 else '='
            lines.append(
                f'{emoji} <b>{coin.upper()}</b>: '
                f'↑{up_shares:.0f} ↓{down_shares:.0f} (matched: {matched:.0f}) | '
                f'<b>${pnl:+.2f}</b>'
            )
            if level_details:
                lines.append(f'   levels: {" ".join(level_details)}')

        self.session_pnl += total_pnl
        self.session_capital += total_capital
        self.results.append({'window': window_ts, 'pnl': total_pnl, 'capital': total_capital})

        # store to clickhouse
        self.flush_fills()
        for coin, book in self.books.items():
            self.store_window(window_ts, coin, book)

        lines.append(f'\n<b>Window PnL:</b> ${total_pnl:+.2f}')
        lines.append(f'<b>Session PnL:</b> ${self.session_pnl:+.2f}')
        lines.append(f'<b>Windows:</b> {len(self.results)}')

        msg = '\n'.join(lines)
        await self.send_telegram(msg)

        # aggregate latency for console
        all_lat = []
        for book in self.books.values():
            all_lat.extend(book.latency_samples)
        avg_lat = sum(all_lat) / len(all_lat) if all_lat else 0

        print(f'[paper] pnl=${total_pnl:+.2f} lat={avg_lat:.0f}ms')

    async def run(self):
        print('=' * 60)
        print('PAPER TRADER - Multi-level Grid')
        print(f'Levels: {PRICE_LEVELS} (edges: {[f"{(1-p*2)*100:.0f}%" for p in PRICE_LEVELS]})')
        print(f'Size: {ORDER_SIZE} shares per level')
        print(f'Capture rate: {CAPTURE_RATE*100:.0f}% of overflow')
        print(f'Coins: {COINS}')
        print('=' * 60)

        self.connect_ch()

        levels_str = '/'.join([str(p) for p in PRICE_LEVELS])
        startup_msg = (
            f'<b>Paper Trader Started</b>\n'
            f'Grid: {levels_str} x {ORDER_SIZE}\n'
            f'Capture rate: {CAPTURE_RATE*100:.0f}%\n'
            f'Coins: {", ".join(c.upper() for c in COINS)}'
        )
        await self.send_telegram(startup_msg)

        # on startup, join current window if in first 10 min
        now = int(time.time())
        current_window = now - (now % 900)
        elapsed = now - current_window

        if elapsed < 600:
            print(f'[startup] joining current window ({elapsed}s in)')
            await self.run_window(current_window)

        while True:
            now = int(time.time())
            current_window = now - (now % 900)
            next_window = current_window + 900
            wait = next_window - now

            if wait > 5:
                await asyncio.sleep(wait - 5)

            await self.run_window(next_window)


if __name__ == '__main__':
    asyncio.run(PaperTrader().run())
