#!/usr/bin/env python3
"""
Paper Trader - queue-based fill simulation using websocket book events
Posts at best_bid (back of queue), only fills when large SELLs sweep through
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

# realistic params
CAPITAL_PER_SIDE = 50  # $50 bid on each side per coin
MIN_ORDER_SIZE = 5  # minimum $5 per order like gabagool


@dataclass
class CoinBook:
    coin: str
    up_token: str = ''
    down_token: str = ''
    capital_up: float = CAPITAL_PER_SIDE
    capital_down: float = CAPITAL_PER_SIDE
    up_bid: float = 0
    up_ask: float = 1
    down_bid: float = 0
    down_ask: float = 1
    # queue depth at best bid (from websocket book events)
    up_queue: float = 0
    down_queue: float = 0
    up_queue_samples: list = field(default_factory=list)
    down_queue_samples: list = field(default_factory=list)
    # our simulated fills
    up_fills: list = field(default_factory=list)
    down_fills: list = field(default_factory=list)
    # market totals (for comparison)
    market_up_sells: float = 0
    market_down_sells: float = 0
    trade_count: int = 0
    edge_samples: list = field(default_factory=list)
    # latency tracking (ms)
    latency_samples: list = field(default_factory=list)

    @property
    def combined_bid(self):
        return self.up_bid + self.down_bid

    @property
    def edge(self):
        return 1.0 - self.combined_bid if self.combined_bid < 1 else 0

    def try_fill(self, side: str, trade_price: float, size: float) -> float:
        """try to fill from a market SELL using queue-based model

        we post at best_bid, so we fill at OUR bid price, not the trade price
        trade just needs to sweep through our level
        """
        # skip if no edge
        if self.combined_bid >= 1.0:
            return 0

        if side == 'up':
            our_bid = self.up_bid
            if self.capital_up < MIN_ORDER_SIZE or our_bid <= 0:
                return 0
            # trade must be at or below our bid to reach us
            if trade_price > our_bid + 0.02:
                return 0
            # queue-based: only fill overflow beyond queue depth
            if size <= self.up_queue:
                return 0
            overflow = size - self.up_queue
            max_shares = self.capital_up / our_bid
            fill_size = min(overflow, max_shares)
            if fill_size > 0:
                cost = fill_size * our_bid  # fill at OUR bid, not trade price
                self.capital_up -= cost
                self.up_fills.append((our_bid, fill_size))
            return fill_size
        else:
            our_bid = self.down_bid
            if self.capital_down < MIN_ORDER_SIZE or our_bid <= 0:
                return 0
            if trade_price > our_bid + 0.02:
                return 0
            if size <= self.down_queue:
                return 0
            overflow = size - self.down_queue
            max_shares = self.capital_down / our_bid
            fill_size = min(overflow, max_shares)
            if fill_size > 0:
                cost = fill_size * our_bid  # fill at OUR bid, not trade price
                self.capital_down -= cost
                self.down_fills.append((our_bid, fill_size))
            return fill_size

    def add_market_sell(self, side: str, size: float):
        """track total market SELL volume"""
        if side == 'up':
            self.market_up_sells += size
        else:
            self.market_down_sells += size

    def sample_edge(self):
        if self.combined_bid > 0:
            self.edge_samples.append(self.edge)

    def calc_pnl(self):
        up_shares = sum(s for _, s in self.up_fills)
        down_shares = sum(s for _, s in self.down_fills)
        up_cost = sum(p * s for p, s in self.up_fills)
        down_cost = sum(p * s for p, s in self.down_fills)

        if up_shares == 0 or down_shares == 0:
            return 0, up_shares, down_shares, 1.0, up_cost + down_cost

        matched = min(up_shares, down_shares)
        up_avg = up_cost / up_shares
        down_avg = down_cost / down_shares
        combined = up_avg + down_avg
        edge = 1.0 - combined

        matched_pnl = matched * edge
        # unmatched is a coin flip (EV = 0 at fair price)
        unmatched_up = max(0, up_shares - down_shares)
        unmatched_down = max(0, down_shares - up_shares)
        # assume 50% win rate on unmatched
        unmatched_ev = unmatched_up * (0.5 - up_avg) + unmatched_down * (0.5 - down_avg)

        capital_used = up_cost + down_cost
        return matched_pnl + unmatched_ev, up_shares, down_shares, combined, capital_used


class PaperTrader:
    def __init__(self):
        self.tokens = {}
        self.books = {}
        self.results = []
        self.session_pnl = 0
        self.session_capital = 0
        self.client = None
        self.fill_buffer = []
        self.order_buffer = []
        self.last_order_store = {}

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

    def store_order(self, window_ts: int, coin: str, side: str, price: float, size: float, now: float):
        """store limit order we would post - sample every 5s or on price change"""
        if not self.client:
            return

        key = f'{coin}_{side}'
        last = self.last_order_store.get(key, (0, 0))
        last_time, last_price = last

        # only store every 5s or if price changed by >1%
        if now - last_time < 5 and abs(price - last_price) / last_price < 0.01 if last_price > 0 else False:
            return

        self.last_order_store[key] = (now, price)
        self.order_buffer.append((
            datetime.utcnow(),
            window_ts,
            coin,
            side,
            price,
            size,
            price * size
        ))

    def flush_orders(self):
        if not self.client or not self.order_buffer:
            return
        try:
            self.client.insert('paper_orders', self.order_buffer,
                column_names=['ts', 'window_ts', 'coin', 'side', 'price', 'size', 'cost'])
            self.order_buffer.clear()
        except Exception as e:
            print(f'[ch] order flush error: {e}')

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
        up_shares = sum(s for _, s in book.up_fills)
        down_shares = sum(s for _, s in book.down_fills)
        up_cost = sum(p * s for p, s in book.up_fills)
        down_cost = sum(p * s for p, s in book.down_fills)
        avg_edge = sum(book.edge_samples) / len(book.edge_samples) if book.edge_samples else 0
        pnl, _, _, _, _ = book.calc_pnl()

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

        self.last_order_store.clear()
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
                            # get coin for this event
                            aid = data.get('asset_id') or (data.get('price_changes', [{}])[0].get('asset_id') if data.get('price_changes') else None)
                            if aid:
                                info = self.tokens.get(aid)
                                if info:
                                    book = self.books.get(info[0])
                                    if book:
                                        book.latency_samples.append(latency_ms)

                        if event_type == 'book':
                            # update queue depth from book snapshot
                            asset_id = data.get('asset_id')
                            info = self.tokens.get(asset_id)
                            if info:
                                coin, side = info
                                book = self.books.get(coin)
                                if book:
                                    bids = data.get('bids', [])
                                    if bids:
                                        best = max(bids, key=lambda x: float(x['price']))
                                        depth = float(best['size'])
                                        if side == 'up':
                                            book.up_queue = depth
                                            book.up_queue_samples.append(depth)
                                        else:
                                            book.down_queue = depth
                                            book.down_queue_samples.append(depth)

                        elif event_type == 'price_change':
                            for pc in data.get('price_changes', []):
                                info = self.tokens.get(pc.get('asset_id'))
                                if not info:
                                    continue
                                coin, side = info
                                book = self.books.get(coin)
                                if not book:
                                    continue

                                bid = float(pc.get('best_bid', 0))
                                ask = float(pc.get('best_ask', 1))

                                if side == 'up':
                                    book.up_bid = bid
                                    book.up_ask = ask
                                    capital = book.capital_up
                                else:
                                    book.down_bid = bid
                                    book.down_ask = ask
                                    capital = book.capital_down

                                book.sample_edge()

                                # store limit order we'd post (only during accumulate, with edge)
                                if now < accumulate_end and capital >= MIN_ORDER_SIZE and book.edge > 0 and bid > 0:
                                    order_size = capital / bid
                                    self.store_order(window_ts, coin, side, bid, order_size, now)

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

                            # only try to fill on SELL trades in first 9 min
                            if trade_side == 'SELL':
                                book.add_market_sell(side, size)

                                if now < accumulate_end:
                                    fill = book.try_fill(side, price, size)
                                    if fill > 0:
                                        fills_log.append(f'{elapsed:.0f}s {coin}/{side} {fill:.1f}@${price:.2f}')
                                        self.store_fill(window_ts, coin, side, price, fill)

                    except:
                        pass

        except Exception as e:
            print(f'[paper] ws error: {e}')

        # calculate results
        total_pnl = 0
        total_capital = 0
        lines = [f'<b>Window {dt.strftime("%H:%M")} UTC</b>']
        lines.append(f'<i>Capital: ${CAPITAL_PER_SIDE}/side, queue-based fills</i>\n')

        for coin, book in sorted(self.books.items()):
            pnl, up_shares, down_shares, combined, capital = book.calc_pnl()
            total_pnl += pnl
            total_capital += capital

            avg_edge = sum(book.edge_samples) / len(book.edge_samples) * 100 if book.edge_samples else 0
            market_sells = book.market_up_sells + book.market_down_sells
            our_fills = up_shares + down_shares
            capture_rate = (our_fills / market_sells * 100) if market_sells > 0 else 0

            # avg queue depth
            avg_up_q = sum(book.up_queue_samples) / len(book.up_queue_samples) if book.up_queue_samples else 0
            avg_down_q = sum(book.down_queue_samples) / len(book.down_queue_samples) if book.down_queue_samples else 0

            # latency stats
            lat = book.latency_samples
            avg_lat = sum(lat) / len(lat) if lat else 0
            p50_lat = sorted(lat)[len(lat)//2] if lat else 0
            p99_lat = sorted(lat)[int(len(lat)*0.99)] if lat else 0

            if up_shares == 0 and down_shares == 0:
                lines.append(f'<b>{coin.upper()}</b>: no fills (queue ↑{avg_up_q:.0f} ↓{avg_down_q:.0f} | lat {avg_lat:.0f}ms)')
                continue

            emoji = '+' if pnl > 0 else '-' if pnl < 0 else '='
            lines.append(
                f'{emoji} <b>{coin.upper()}</b>: '
                f'↑{up_shares:.0f} ↓{down_shares:.0f} | '
                f'${capital:.0f} used | '
                f'edge {avg_edge:+.1f}% | '
                f'<b>${pnl:+.2f}</b>'
            )
            lines.append(
                f'   queue: ↑{avg_up_q:.0f} ↓{avg_down_q:.0f} | '
                f'lat: {p50_lat:.0f}ms p50 / {p99_lat:.0f}ms p99'
            )

        self.session_pnl += total_pnl
        self.session_capital += total_capital
        self.results.append({'window': window_ts, 'pnl': total_pnl, 'capital': total_capital})

        # store to clickhouse
        self.flush_fills()
        self.flush_orders()
        for coin, book in self.books.items():
            self.store_window(window_ts, coin, book)

        # summary
        roi = (total_pnl / total_capital * 100) if total_capital > 0 else 0
        session_roi = (self.session_pnl / self.session_capital * 100) if self.session_capital > 0 else 0

        lines.append(f'\n<b>Window:</b> ${total_pnl:+.2f} on ${total_capital:.0f} ({roi:+.1f}% ROI)')
        lines.append(f'<b>Session:</b> ${self.session_pnl:+.2f} on ${self.session_capital:.0f} ({session_roi:+.1f}% ROI)')
        lines.append(f'<b>Windows:</b> {len(self.results)}')

        msg = '\n'.join(lines)
        await self.send_telegram(msg)

        # aggregate latency for console
        all_lat = []
        for book in self.books.values():
            all_lat.extend(book.latency_samples)
        avg_lat = sum(all_lat) / len(all_lat) if all_lat else 0

        print(f'[paper] pnl=${total_pnl:+.2f} roi={roi:+.1f}% lat={avg_lat:.0f}ms')

    async def run(self):
        print('=' * 60)
        print('PAPER TRADER (queue-based fills)')
        print(f'Capital: ${CAPITAL_PER_SIDE} per side per coin')
        print(f'Coins: {COINS}')
        print('=' * 60)

        self.connect_ch()

        startup_msg = (
            f'<b>Paper Trader Started</b>\n'
            f'Capital: ${CAPITAL_PER_SIDE}/side/coin\n'
            f'Model: queue-based (back of queue)\n'
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
