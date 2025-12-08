#!/usr/bin/env python3
"""
Polymarket Raw Collector
Captures all data sources exactly as received - no transformations
"""

import asyncio
import json
import os
import time
from datetime import datetime

import aiohttp
import websockets
import clickhouse_connect

from .alerts import WindowAlerts

# endpoints
CLOB_WS = 'wss://ws-subscriptions-clob.polymarket.com/ws/market'
RTDS_WS = 'wss://ws-live-data.polymarket.com'
GAMMA_API = 'https://gamma-api.polymarket.com'
CRYPTO_PRICE_API = 'https://polymarket.com/api/crypto/crypto-price'

# clickhouse
CH_HOST = os.environ.get('CLICKHOUSE_HOST', 'n60fu3ciqd.eastus2.azure.clickhouse.cloud')
CH_USER = os.environ.get('CLICKHOUSE_USER', 'default')
CH_PASSWORD = os.environ.get('CLICKHOUSE_PASSWORD', '')

COINS = ['btc', 'eth', 'sol', 'xrp']
BATCH_SIZE = 500
FLUSH_INTERVAL = 3


class RawCollector:
    def __init__(self):
        self.client = None
        self.tokens = {}  # token_id -> (coin, side)
        self.market_times = {}  # coin -> (start_time, end_time)
        self.clob_buffer = []
        self.rtds_buffer = []
        self.stats = {'clob': 0, 'rtds': 0, 'gamma': 0, 'resolution': 0}
        self.alerts = WindowAlerts()

    def connect(self):
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
        # clob websocket events - book, price_change, last_trade_price
        self.client.command('''
            CREATE TABLE IF NOT EXISTS clob_events (
                ts DateTime64(3),
                window_ts UInt32,
                event_type LowCardinality(String),
                asset_id String,
                market String,
                raw String
            ) ENGINE = MergeTree()
            PARTITION BY toYYYYMMDD(ts)
            ORDER BY (window_ts, event_type, ts)
            TTL ts + INTERVAL 90 DAY
        ''')

        # rtds websocket events - crypto_prices_chainlink
        self.client.command('''
            CREATE TABLE IF NOT EXISTS rtds_events (
                ts DateTime64(3),
                window_ts UInt32,
                topic LowCardinality(String),
                symbol LowCardinality(String),
                raw String
            ) ENGINE = MergeTree()
            PARTITION BY toYYYYMMDD(ts)
            ORDER BY (window_ts, symbol, ts)
            TTL ts + INTERVAL 90 DAY
        ''')

        # gamma api - events endpoint
        self.client.command('''
            CREATE TABLE IF NOT EXISTS gamma_events (
                ts DateTime64(3),
                raw String
            ) ENGINE = MergeTree()
            PARTITION BY toYYYYMMDD(ts)
            ORDER BY ts
            TTL ts + INTERVAL 90 DAY
        ''')

        # gamma api - markets by slug
        self.client.command('''
            CREATE TABLE IF NOT EXISTS gamma_markets (
                ts DateTime64(3),
                window_ts UInt32,
                coin LowCardinality(String),
                slug String,
                raw String
            ) ENGINE = MergeTree()
            PARTITION BY toYYYYMMDD(ts)
            ORDER BY (window_ts, coin, ts)
            TTL ts + INTERVAL 90 DAY
        ''')

        # crypto-price api - resolution data
        self.client.command('''
            CREATE TABLE IF NOT EXISTS crypto_prices (
                ts DateTime64(3),
                window_ts UInt32,
                coin LowCardinality(String),
                raw String
            ) ENGINE = MergeTree()
            PARTITION BY toYYYYMMDD(ts)
            ORDER BY (window_ts, coin, ts)
            TTL ts + INTERVAL 90 DAY
        ''')

        # token registry for lookups
        self.client.command('''
            CREATE TABLE IF NOT EXISTS token_registry (
                window_ts UInt32,
                coin LowCardinality(String),
                side LowCardinality(String),
                token_id String,
                condition_id String,
                slug String,
                created_at DateTime64(3)
            ) ENGINE = ReplacingMergeTree()
            ORDER BY (window_ts, token_id)
        ''')

        print('[ch] tables ready')

    def flush_clob(self):
        if not self.client or not self.clob_buffer:
            self.clob_buffer.clear()
            return

        try:
            self.client.insert('clob_events', self.clob_buffer,
                column_names=['ts', 'window_ts', 'event_type', 'asset_id', 'market', 'raw'])
            self.stats['clob'] += len(self.clob_buffer)
            self.clob_buffer.clear()
        except Exception as e:
            print(f'[ch] clob flush error: {e}')

    def flush_rtds(self):
        if not self.client or not self.rtds_buffer:
            self.rtds_buffer.clear()
            return

        try:
            self.client.insert('rtds_events', self.rtds_buffer,
                column_names=['ts', 'window_ts', 'topic', 'symbol', 'raw'])
            self.stats['rtds'] += len(self.rtds_buffer)
            self.rtds_buffer.clear()
        except Exception as e:
            print(f'[ch] rtds flush error: {e}')

    async def fetch_gamma(self, window_ts: int):
        """fetch gamma api and store raw responses"""
        async with aiohttp.ClientSession() as session:
            # events endpoint
            try:
                async with session.get(f'{GAMMA_API}/events?tag_id=102467&closed=false&limit=20') as resp:
                    if resp.status == 200:
                        raw = await resp.text()
                        if self.client:
                            self.client.insert('gamma_events', [
                                (datetime.utcnow(), raw)
                            ], column_names=['ts', 'raw'])
                        self.stats['gamma'] += 1
            except Exception as e:
                print(f'[gamma] events error: {e}')

            # markets by slug
            for coin in COINS:
                slug = f'{coin}-updown-15m-{window_ts}'
                try:
                    async with session.get(f'{GAMMA_API}/markets?slug={slug}') as resp:
                        if resp.status != 200:
                            continue

                        raw = await resp.text()
                        data_list = json.loads(raw)
                        if not data_list:
                            continue
                        data = data_list[0] if isinstance(data_list, list) else data_list

                        # store raw
                        if self.client:
                            self.client.insert('gamma_markets', [
                                (datetime.utcnow(), window_ts, coin, slug, raw)
                            ], column_names=['ts', 'window_ts', 'coin', 'slug', 'raw'])
                            self.stats['gamma'] += 1

                        # extract tokens for subscription
                        tokens = data.get('clobTokenIds', [])
                        if isinstance(tokens, str):
                            tokens = json.loads(tokens)

                        if len(tokens) >= 2:
                            up_token, down_token = tokens[0], tokens[1]
                            self.tokens[up_token] = (coin, 'up')
                            self.tokens[down_token] = (coin, 'down')

                            # store timing for resolution
                            start_time = data.get('eventStartTime') or data.get('startDate')
                            end_time = data.get('endDate')
                            if start_time and end_time:
                                self.market_times[coin] = (start_time, end_time)

                            # store token registry
                            if self.client:
                                condition_id = data.get('conditionId', '')
                                self.client.insert('token_registry', [
                                    (window_ts, coin, 'up', up_token, condition_id, slug, datetime.utcnow()),
                                    (window_ts, coin, 'down', down_token, condition_id, slug, datetime.utcnow()),
                                ], column_names=['window_ts', 'coin', 'side', 'token_id', 'condition_id', 'slug', 'created_at'])

                            print(f'  [{coin}] {slug}')

                except Exception as e:
                    print(f'  [{coin}] error: {e}')

        print(f'[gamma] {self.stats["gamma"]} responses, {len(self.tokens)} tokens')

    async def fetch_resolution(self, window_ts: int):
        """fetch resolution from gamma api after window ends"""
        print('[resolution] waiting 60s for markets to close...')
        await asyncio.sleep(60)

        async with aiohttp.ClientSession() as session:
            for coin in COINS:
                slug = f'{coin}-updown-15m-{window_ts}'
                try:
                    # get market with resolution from gamma
                    async with session.get(f'{GAMMA_API}/markets?slug={slug}') as resp:
                        if resp.status != 200:
                            continue

                        raw = await resp.text()
                        data_list = json.loads(raw)
                        if not data_list:
                            continue
                        data = data_list[0] if isinstance(data_list, list) else data_list

                        # check if resolved
                        if not data.get('closed'):
                            print(f'  [{coin}] not closed yet')
                            continue

                        # store raw response
                        if self.client:
                            self.client.insert('crypto_prices', [
                                (datetime.utcnow(), window_ts, coin, raw)
                            ], column_names=['ts', 'window_ts', 'coin', 'raw'])
                            self.stats['resolution'] += 1

                        # parse outcome from outcomePrices
                        outcome_prices = data.get('outcomePrices', [])
                        outcomes = data.get('outcomes', ['Up', 'Down'])
                        if outcome_prices:
                            winner_idx = outcome_prices.index('1') if '1' in outcome_prices else -1
                            if winner_idx >= 0:
                                outcome = outcomes[winner_idx].upper()
                                print(f'  [{coin}] {outcome}')

                except Exception as e:
                    print(f'  [{coin}] error: {e}')

        print(f'[resolution] {self.stats["resolution"]} responses')

    async def collect_clob(self, window_ts: int, window_end: int):
        """collect clob websocket events"""
        if not self.tokens:
            return

        token_ids = list(self.tokens.keys())
        print(f'[clob] connecting with {len(token_ids)} tokens...')

        last_flush = time.time()
        last_log = time.time()

        try:
            async with websockets.connect(CLOB_WS) as ws:
                await ws.send(json.dumps({
                    'type': 'subscribe',
                    'channel': 'market',
                    'assets_ids': token_ids
                }))
                print('[clob] connected')

                async for raw in ws:
                    now = time.time()
                    if now >= window_end:
                        break

                    # parse to extract indexed fields
                    try:
                        data = json.loads(raw)
                        if isinstance(data, list):
                            data = data[0] if data else {}

                        event_type = data.get('event_type', '')
                        asset_id = data.get('asset_id', '')
                        market = data.get('market', '')

                        self.clob_buffer.append((
                            datetime.utcfromtimestamp(now),
                            window_ts,
                            event_type,
                            asset_id,
                            market,
                            raw
                        ))

                        elapsed = now - window_ts

                        # feed alerts module
                        if event_type == 'price_change':
                            for pc in data.get('price_changes', []):
                                info = self.tokens.get(pc.get('asset_id'))
                                if info:
                                    coin, side = info
                                    bid = float(pc.get('best_bid', 0))
                                    ask = float(pc.get('best_ask', 1))
                                    self.alerts.update_book(coin, side, bid, ask, elapsed)

                        elif event_type == 'last_trade_price':
                            info = self.tokens.get(data.get('asset_id'))
                            if info:
                                coin, side = info
                                price = float(data.get('price', 0))
                                size = float(data.get('size', 0))
                                trade_side = data.get('side', '')  # BUY or SELL
                                self.alerts.add_trade(coin, side, price, size, trade_side)

                    except:
                        # store anyway even if parse fails
                        self.clob_buffer.append((
                            datetime.utcfromtimestamp(now),
                            window_ts,
                            '',
                            '',
                            '',
                            raw
                        ))

                    # flush periodically
                    if len(self.clob_buffer) >= BATCH_SIZE or now - last_flush >= FLUSH_INTERVAL:
                        self.flush_clob()
                        last_flush = now

                    # log every 30s
                    if now - last_log >= 30:
                        elapsed = int(now - (window_ts))
                        print(f'[{elapsed//60}:{elapsed%60:02d}] clob={self.stats["clob"]} rtds={self.stats["rtds"]}')
                        last_log = now

        except Exception as e:
            print(f'[clob] error: {e}')

        self.flush_clob()

    async def collect_rtds(self, window_ts: int, window_end: int):
        """collect rtds websocket events - chainlink prices"""
        print('[rtds] connecting...')

        last_flush = time.time()

        try:
            async with websockets.connect(RTDS_WS) as ws:
                # subscribe to chainlink prices
                await ws.send(json.dumps({
                    'action': 'subscribe',
                    'subscriptions': [{
                        'topic': 'crypto_prices_chainlink',
                        'type': '*',
                        'filters': ''
                    }]
                }))
                print('[rtds] connected')

                async for raw in ws:
                    now = time.time()
                    if now >= window_end:
                        break

                    # parse to extract indexed fields
                    try:
                        data = json.loads(raw)
                        topic = data.get('topic', '')
                        symbol = data.get('payload', {}).get('symbol', '')

                        self.rtds_buffer.append((
                            datetime.utcfromtimestamp(now),
                            window_ts,
                            topic,
                            symbol,
                            raw
                        ))
                    except:
                        self.rtds_buffer.append((
                            datetime.utcfromtimestamp(now),
                            window_ts,
                            '',
                            '',
                            raw
                        ))

                    # flush periodically
                    if len(self.rtds_buffer) >= BATCH_SIZE or now - last_flush >= FLUSH_INTERVAL:
                        self.flush_rtds()
                        last_flush = now

        except Exception as e:
            print(f'[rtds] error: {e}')

        self.flush_rtds()

    async def collect_window(self, window_ts: int):
        window_end = window_ts + 900
        self.tokens.clear()
        self.market_times.clear()

        print(f'\n{"="*60}')
        print(f'WINDOW {window_ts} | {datetime.utcfromtimestamp(window_ts).strftime("%Y-%m-%d %H:%M:%S")} UTC')
        print(f'{"="*60}')

        await self.fetch_gamma(window_ts)

        if not self.tokens:
            print('[!] no tokens found')
            return

        # reset alerts for this window
        coins = list(set(coin for coin, _ in self.tokens.values()))
        self.alerts.reset(coins)

        # collect websockets in parallel
        await asyncio.gather(
            self.collect_clob(window_ts, window_end),
            self.collect_rtds(window_ts, window_end),
        )

        # send telegram summary
        await self.alerts.send_summary(window_ts, self.stats['clob'], self.stats['rtds'])

        # fetch resolution in background (don't block next window)
        asyncio.create_task(self.fetch_resolution(window_ts))

        print(f'[done] clob={self.stats["clob"]} rtds={self.stats["rtds"]} gamma={self.stats["gamma"]}')

    async def run(self):
        print('='*60)
        print('POLYMARKET RAW COLLECTOR')
        print(f'ClickHouse: {CH_HOST}')
        print('='*60)

        self.connect()

        # on startup, check if we should join current window
        now = int(time.time())
        current_window = now - (now % 900)
        elapsed = now - current_window

        # if we're in first 10 min of window, join it
        if elapsed < 600:
            print(f'\n[startup] joining current window ({elapsed}s in)')
            self.stats = {'clob': 0, 'rtds': 0, 'gamma': 0, 'resolution': 0}
            await self.collect_window(current_window)

        while True:
            now = int(time.time())
            current_window = now - (now % 900)
            next_window = current_window + 900
            wait = next_window - now

            print(f'\n[wait] {wait}s until {datetime.utcfromtimestamp(next_window).strftime("%H:%M:%S")} UTC')

            if wait > 5:
                await asyncio.sleep(wait - 5)

            # reset stats
            self.stats = {'clob': 0, 'rtds': 0, 'gamma': 0, 'resolution': 0}
            await self.collect_window(next_window)


if __name__ == '__main__':
    asyncio.run(RawCollector().run())
