#!/usr/bin/env python3
"""
Window Collector - captures complete data for 15-minute updown markets

Waits for next window, captures:
- Market metadata from Gamma API
- All CLOB websocket events (orderbook, trades)
- Chainlink price feed from RTDS
- Resolution outcome

Usage: python3 scripts/window_collector.py
"""

import asyncio
import json
import time
import sqlite3
import os
from datetime import datetime
from pathlib import Path

import aiohttp
import websockets

# endpoints
GAMMA_API = 'https://gamma-api.polymarket.com'
CLOB_WS = 'wss://ws-subscriptions-clob.polymarket.com/ws/market'
RTDS_WS = 'wss://ws-live-data.polymarket.com'

# coins to track
COINS = ['btc', 'eth', 'sol', 'xrp']

# data directory
DATA_DIR = Path('data/windows')
DATA_DIR.mkdir(parents=True, exist_ok=True)


class WindowCollector:
    def __init__(self, window_ts: int):
        self.window_ts = window_ts
        self.window_end = window_ts + 900

        # market info
        self.markets = {}  # coin -> market data
        self.tokens = {}   # token_id -> (coin, side)

        # data storage
        self.db_path = DATA_DIR / f'window_{window_ts}.db'
        self.db = None

        # stats
        self.event_count = 0
        self.trade_count = 0

    def init_db(self):
        """initialize sqlite database"""
        self.db = sqlite3.connect(self.db_path)
        self.db.execute('''
            CREATE TABLE IF NOT EXISTS metadata (
                coin TEXT PRIMARY KEY,
                slug TEXT,
                condition_id TEXT,
                up_token TEXT,
                down_token TEXT,
                up_price REAL,
                down_price REAL,
                volume REAL,
                liquidity REAL,
                start_time TEXT,
                end_time TEXT,
                fetched_at INTEGER
            )
        ''')
        self.db.execute('''
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER,
                elapsed_ms INTEGER,
                source TEXT,
                event_type TEXT,
                coin TEXT,
                side TEXT,
                data TEXT
            )
        ''')
        self.db.execute('''
            CREATE TABLE IF NOT EXISTS prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER,
                elapsed_ms INTEGER,
                coin TEXT,
                up_bid REAL,
                up_ask REAL,
                down_bid REAL,
                down_ask REAL,
                combined_bid REAL,
                combined_ask REAL
            )
        ''')
        self.db.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER,
                elapsed_ms INTEGER,
                coin TEXT,
                side TEXT,
                trade_side TEXT,
                price REAL,
                size REAL
            )
        ''')
        self.db.execute('''
            CREATE TABLE IF NOT EXISTS chainlink (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER,
                elapsed_ms INTEGER,
                coin TEXT,
                price REAL
            )
        ''')
        self.db.execute('''
            CREATE TABLE IF NOT EXISTS resolution (
                coin TEXT PRIMARY KEY,
                outcome TEXT,
                open_price REAL,
                close_price REAL,
                resolved_at INTEGER
            )
        ''')
        self.db.execute('CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)')
        self.db.execute('CREATE INDEX IF NOT EXISTS idx_prices_ts ON prices(ts)')
        self.db.execute('CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(ts)')
        self.db.commit()

    async def fetch_markets(self):
        """fetch market metadata for all coins"""
        print(f'[{self._ts()}] fetching markets for window {self.window_ts}...')

        async with aiohttp.ClientSession() as session:
            for coin in COINS:
                slug = f'{coin}-updown-15m-{self.window_ts}'
                try:
                    async with session.get(f'{GAMMA_API}/markets/slug/{slug}') as resp:
                        if resp.status != 200:
                            print(f'  [{coin}] not found')
                            continue

                        data = await resp.json()

                        # parse tokens
                        tokens = data.get('clobTokenIds', [])
                        if isinstance(tokens, str):
                            tokens = json.loads(tokens)

                        if len(tokens) < 2:
                            print(f'  [{coin}] missing tokens')
                            continue

                        # parse prices
                        prices = data.get('outcomePrices', [])
                        if isinstance(prices, str):
                            prices = json.loads(prices)

                        up_token, down_token = tokens[0], tokens[1]
                        up_price = float(prices[0]) if prices else 0.5
                        down_price = float(prices[1]) if len(prices) > 1 else 0.5

                        self.markets[coin] = {
                            'slug': slug,
                            'condition_id': data.get('conditionId'),
                            'up_token': up_token,
                            'down_token': down_token,
                            'up_price': up_price,
                            'down_price': down_price,
                            'volume': float(data.get('volume', 0)),
                            'liquidity': float(data.get('liquidity', 0)),
                            'start_time': data.get('eventStartTime'),
                            'end_time': data.get('endDate'),
                        }

                        self.tokens[up_token] = (coin, 'up')
                        self.tokens[down_token] = (coin, 'down')

                        # save to db
                        self.db.execute('''
                            INSERT OR REPLACE INTO metadata
                            (coin, slug, condition_id, up_token, down_token, up_price, down_price, volume, liquidity, start_time, end_time, fetched_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            coin, slug, data.get('conditionId'),
                            up_token, down_token, up_price, down_price,
                            float(data.get('volume', 0)), float(data.get('liquidity', 0)),
                            data.get('eventStartTime'), data.get('endDate'),
                            int(time.time() * 1000)
                        ))

                        print(f'  [{coin}] {slug} up={up_price:.2f} down={down_price:.2f}')

                except Exception as e:
                    print(f'  [{coin}] error: {e}')

            self.db.commit()

        print(f'[{self._ts()}] loaded {len(self.markets)} markets, {len(self.tokens)} tokens')

    async def collect_clob(self):
        """collect CLOB websocket data"""
        if not self.tokens:
            print('[clob] no tokens to subscribe')
            return

        token_ids = list(self.tokens.keys())
        print(f'[{self._ts()}] connecting to CLOB with {len(token_ids)} tokens...')

        # track best prices per coin
        best = {coin: {'up_bid': 0, 'up_ask': 1, 'down_bid': 0, 'down_ask': 1} for coin in self.markets}
        last_save = 0

        try:
            async with websockets.connect(CLOB_WS) as ws:
                await ws.send(json.dumps({
                    'type': 'subscribe',
                    'channel': 'market',
                    'assets_ids': token_ids
                }))
                print(f'[{self._ts()}] CLOB connected')

                async for raw in ws:
                    now = int(time.time() * 1000)
                    elapsed = now - (self.window_ts * 1000)

                    # stop at window end
                    if time.time() >= self.window_end:
                        print(f'[{self._ts()}] window ended')
                        break

                    try:
                        data = json.loads(raw)
                        if isinstance(data, list):
                            data = data[0] if data else None
                        if not data:
                            continue

                        event_type = data.get('event_type', 'unknown')
                        self.event_count += 1

                        # save raw event
                        self.db.execute('''
                            INSERT INTO events (ts, elapsed_ms, source, event_type, data)
                            VALUES (?, ?, 'clob', ?, ?)
                        ''', (now, elapsed, event_type, raw))

                        # process price changes
                        if event_type == 'price_change':
                            for pc in data.get('price_changes', []):
                                token_id = pc.get('asset_id')
                                info = self.tokens.get(token_id)
                                if not info:
                                    continue

                                coin, side = info
                                bid = float(pc.get('best_bid', 0) or 0)
                                ask = float(pc.get('best_ask', 1) or 1)

                                if side == 'up':
                                    best[coin]['up_bid'] = bid
                                    best[coin]['up_ask'] = ask
                                else:
                                    best[coin]['down_bid'] = bid
                                    best[coin]['down_ask'] = ask

                        # process trades
                        elif event_type == 'last_trade_price':
                            token_id = data.get('asset_id')
                            info = self.tokens.get(token_id)
                            if info:
                                coin, side = info
                                self.trade_count += 1
                                self.db.execute('''
                                    INSERT INTO trades (ts, elapsed_ms, coin, side, trade_side, price, size)
                                    VALUES (?, ?, ?, ?, ?, ?, ?)
                                ''', (
                                    now, elapsed, coin, side,
                                    data.get('side', ''),
                                    float(data.get('price', 0)),
                                    float(data.get('size', 0))
                                ))

                        # save prices every second
                        if now - last_save >= 1000:
                            last_save = now
                            for coin, p in best.items():
                                combined_bid = p['up_bid'] + p['down_bid']
                                combined_ask = p['up_ask'] + p['down_ask']
                                self.db.execute('''
                                    INSERT INTO prices (ts, elapsed_ms, coin, up_bid, up_ask, down_bid, down_ask, combined_bid, combined_ask)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                ''', (now, elapsed, coin, p['up_bid'], p['up_ask'], p['down_bid'], p['down_ask'], combined_bid, combined_ask))

                            self.db.commit()

                            # log progress
                            minute = elapsed // 60000
                            if elapsed % 10000 < 1000:
                                edges = []
                                for coin, p in best.items():
                                    cb = p['up_bid'] + p['down_bid']
                                    if cb > 0:
                                        edges.append(f'{coin}:{cb:.3f}')
                                print(f'[{self._ts()}] min {minute} | {self.event_count} events, {self.trade_count} trades | {" ".join(edges)}')

                    except json.JSONDecodeError:
                        pass

        except Exception as e:
            print(f'[clob] error: {e}')

    async def collect_rtds(self):
        """collect chainlink price feed"""
        print(f'[{self._ts()}] connecting to RTDS...')

        try:
            async with websockets.connect(RTDS_WS) as ws:
                # subscribe to price feeds
                for coin in self.markets:
                    await ws.send(json.dumps({
                        'auth': {},
                        'type': 'live_activity',
                        'assets_ids': [],  # all
                    }))

                print(f'[{self._ts()}] RTDS connected')

                async for raw in ws:
                    now = int(time.time() * 1000)
                    elapsed = now - (self.window_ts * 1000)

                    if time.time() >= self.window_end:
                        break

                    try:
                        data = json.loads(raw)

                        # save raw event
                        self.db.execute('''
                            INSERT INTO events (ts, elapsed_ms, source, event_type, data)
                            VALUES (?, ?, 'rtds', ?, ?)
                        ''', (now, elapsed, data.get('type', 'unknown'), raw))

                        # extract chainlink prices
                        if 'price' in data:
                            # try to identify coin from context
                            for coin in self.markets:
                                if coin.upper() in str(data):
                                    self.db.execute('''
                                        INSERT INTO chainlink (ts, elapsed_ms, coin, price)
                                        VALUES (?, ?, ?, ?)
                                    ''', (now, elapsed, coin, float(data.get('price', 0))))
                                    break

                    except json.JSONDecodeError:
                        pass

        except Exception as e:
            print(f'[rtds] error: {e}')

    async def fetch_resolution(self):
        """fetch resolution outcome after window ends"""
        print(f'[{self._ts()}] fetching resolution...')
        await asyncio.sleep(30)  # wait for resolution

        async with aiohttp.ClientSession() as session:
            for coin in self.markets:
                slug = f'{coin}-updown-15m-{self.window_ts}'
                try:
                    # get open/close price
                    mkt = self.markets[coin]
                    if mkt.get('start_time') and mkt.get('end_time'):
                        url = f'https://polymarket.com/api/crypto/crypto-price?symbol={coin.upper()}&eventStartTime={mkt["start_time"]}&variant=fifteen&endDate={mkt["end_time"]}'
                        async with session.get(url) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                open_price = data.get('openPrice')
                                close_price = data.get('closePrice')

                                if open_price and close_price:
                                    outcome = 'up' if close_price > open_price else 'down'
                                    self.db.execute('''
                                        INSERT OR REPLACE INTO resolution (coin, outcome, open_price, close_price, resolved_at)
                                        VALUES (?, ?, ?, ?, ?)
                                    ''', (coin, outcome, open_price, close_price, int(time.time() * 1000)))
                                    print(f'  [{coin}] {outcome.upper()} open={open_price:.2f} close={close_price:.2f}')

                except Exception as e:
                    print(f'  [{coin}] resolution error: {e}')

            self.db.commit()

    def _ts(self):
        return datetime.now().strftime('%H:%M:%S')

    async def run(self):
        """main collection loop"""
        print('=' * 60)
        print(f'WINDOW COLLECTOR - {datetime.fromtimestamp(self.window_ts)}')
        print(f'Window: {self.window_ts} -> {self.window_end}')
        print(f'DB: {self.db_path}')
        print('=' * 60)

        self.init_db()
        await self.fetch_markets()

        if not self.markets:
            print('no markets found, exiting')
            return

        # run collectors in parallel
        await asyncio.gather(
            self.collect_clob(),
            self.collect_rtds(),
        )

        # fetch resolution
        await self.fetch_resolution()

        # final stats
        self.db.commit()
        print()
        print('=' * 60)
        print('COLLECTION COMPLETE')
        print(f'  Events: {self.event_count}')
        print(f'  Trades: {self.trade_count}')
        print(f'  DB: {self.db_path}')
        print('=' * 60)


async def main():
    # calculate next window
    now = int(time.time())
    current_window = now - (now % 900)
    next_window = current_window + 900

    wait_secs = next_window - now

    print(f'Current time: {datetime.now().strftime("%H:%M:%S")}')
    print(f'Next window: {datetime.fromtimestamp(next_window).strftime("%H:%M:%S")}')
    print(f'Waiting {wait_secs} seconds...')
    print()

    # wait for window start (minus 5 sec buffer to fetch markets)
    if wait_secs > 5:
        await asyncio.sleep(wait_secs - 5)

    collector = WindowCollector(next_window)
    await collector.run()


if __name__ == '__main__':
    asyncio.run(main())
