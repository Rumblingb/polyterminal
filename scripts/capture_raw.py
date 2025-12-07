#!/usr/bin/env python3
"""
raw websocket event capture for polymarket 15m markets
stores everything in clickhouse for strategy analysis

usage: python scripts/capture_raw.py
"""
import asyncio
import json
import signal
import time
import os
from datetime import datetime
from typing import Optional
import aiohttp
import clickhouse_connect

CLOB_WS = 'wss://ws-subscriptions-clob.polymarket.com/ws/market'
RTDS_WS = 'wss://ws-live-data.polymarket.com'
GAMMA_API = 'https://gamma-api.polymarket.com'

# clickhouse config
CH_HOST = os.getenv('CLICKHOUSE_HOST', 'localhost')
CH_PORT = int(os.getenv('CLICKHOUSE_PORT', 8123))
CH_USER = os.getenv('CLICKHOUSE_USER', 'default')
CH_PASS = os.getenv('CLICKHOUSE_PASSWORD', '')
CH_DB = os.getenv('CLICKHOUSE_DB', 'polymarket')


class ClickHouseWriter:
    def __init__(self):
        self.client = None
        self.buffers = {
            'clob_book': [],
            'clob_price_change': [],
            'clob_trade': [],
            'crypto_price': [],
            'market_metadata': []
        }
        self.flush_interval = 5  # seconds
        self.batch_size = 1000

    def connect(self):
        self.client = clickhouse_connect.get_client(
            host=CH_HOST,
            port=CH_PORT,
            username=CH_USER,
            password=CH_PASS,
        )
        self._init_tables()
        print(f'clickhouse connected: {CH_HOST}:{CH_PORT}/{CH_DB}')

    def _init_tables(self):
        # create database
        self.client.command(f'CREATE DATABASE IF NOT EXISTS {CH_DB}')

        # raw clob book snapshots
        self.client.command(f'''
            CREATE TABLE IF NOT EXISTS {CH_DB}.clob_book (
                received_at DateTime64(3),
                event_ts DateTime64(3),
                asset_id String,
                coin LowCardinality(String),
                side LowCardinality(String),
                window_ts UInt32,
                bids String,
                asks String,
                raw_msg String
            ) ENGINE = MergeTree()
            PARTITION BY toYYYYMMDD(received_at)
            ORDER BY (coin, window_ts, received_at)
        ''')

        # raw clob price changes
        self.client.command(f'''
            CREATE TABLE IF NOT EXISTS {CH_DB}.clob_price_change (
                received_at DateTime64(3),
                event_ts DateTime64(3),
                asset_id String,
                coin LowCardinality(String),
                side LowCardinality(String),
                window_ts UInt32,
                price Decimal(10, 4),
                size Decimal(18, 6),
                order_side LowCardinality(String),
                best_bid Decimal(10, 4),
                best_ask Decimal(10, 4),
                raw_msg String
            ) ENGINE = MergeTree()
            PARTITION BY toYYYYMMDD(received_at)
            ORDER BY (coin, window_ts, received_at)
        ''')

        # raw clob trades
        self.client.command(f'''
            CREATE TABLE IF NOT EXISTS {CH_DB}.clob_trade (
                received_at DateTime64(3),
                event_ts DateTime64(3),
                asset_id String,
                coin LowCardinality(String),
                side LowCardinality(String),
                window_ts UInt32,
                price Decimal(10, 4),
                size Decimal(18, 6),
                trade_side LowCardinality(String),
                raw_msg String
            ) ENGINE = MergeTree()
            PARTITION BY toYYYYMMDD(received_at)
            ORDER BY (coin, window_ts, received_at)
        ''')

        # crypto prices (binance + chainlink)
        self.client.command(f'''
            CREATE TABLE IF NOT EXISTS {CH_DB}.crypto_price (
                received_at DateTime64(3),
                source_ts DateTime64(3),
                source LowCardinality(String),
                symbol LowCardinality(String),
                price Decimal(18, 8),
                raw_msg String
            ) ENGINE = MergeTree()
            PARTITION BY toYYYYMMDD(received_at)
            ORDER BY (symbol, source, received_at)
        ''')

        # market metadata snapshots
        self.client.command(f'''
            CREATE TABLE IF NOT EXISTS {CH_DB}.market_metadata (
                captured_at DateTime64(3),
                coin LowCardinality(String),
                window_ts UInt32,
                slug String,
                up_token String,
                down_token String,
                condition_id String,
                volume Decimal(18, 2),
                liquidity Decimal(18, 2),
                up_price Decimal(10, 4),
                down_price Decimal(10, 4),
                raw_json String
            ) ENGINE = ReplacingMergeTree()
            PARTITION BY toYYYYMMDD(captured_at)
            ORDER BY (coin, window_ts)
        ''')

        print('tables initialized')

    def buffer_event(self, table: str, row: tuple):
        self.buffers[table].append(row)
        if len(self.buffers[table]) >= self.batch_size:
            self._flush_table(table)

    def _flush_table(self, table: str):
        if not self.buffers[table]:
            return

        rows = self.buffers[table]
        self.buffers[table] = []

        try:
            if table == 'clob_book':
                self.client.insert(f'{CH_DB}.clob_book', rows, column_names=[
                    'received_at', 'event_ts', 'asset_id', 'coin', 'side',
                    'window_ts', 'bids', 'asks', 'raw_msg'
                ])
            elif table == 'clob_price_change':
                self.client.insert(f'{CH_DB}.clob_price_change', rows, column_names=[
                    'received_at', 'event_ts', 'asset_id', 'coin', 'side',
                    'window_ts', 'price', 'size', 'order_side', 'best_bid', 'best_ask', 'raw_msg'
                ])
            elif table == 'clob_trade':
                self.client.insert(f'{CH_DB}.clob_trade', rows, column_names=[
                    'received_at', 'event_ts', 'asset_id', 'coin', 'side',
                    'window_ts', 'price', 'size', 'trade_side', 'raw_msg'
                ])
            elif table == 'crypto_price':
                self.client.insert(f'{CH_DB}.crypto_price', rows, column_names=[
                    'received_at', 'source_ts', 'source', 'symbol', 'price', 'raw_msg'
                ])
            elif table == 'market_metadata':
                self.client.insert(f'{CH_DB}.market_metadata', rows, column_names=[
                    'captured_at', 'coin', 'window_ts', 'slug', 'up_token', 'down_token',
                    'condition_id', 'volume', 'liquidity', 'up_price', 'down_price', 'raw_json'
                ])
        except Exception as e:
            print(f'flush error [{table}]: {e}')

    def flush_all(self):
        for table in self.buffers:
            self._flush_table(table)

    def close(self):
        self.flush_all()
        if self.client:
            self.client.close()


class MarketTracker:
    def __init__(self):
        self.markets = {}  # coin -> market data
        self.token_map = {}  # token_id -> {coin, side, window_ts}
        self.last_refresh = 0

    async def refresh(self, session: aiohttp.ClientSession):
        now = int(time.time())

        try:
            async with session.get(f'{GAMMA_API}/events?tag_id=102467&closed=false&limit=50') as resp:
                if resp.status != 200:
                    return
                events = await resp.json()
        except Exception as e:
            print(f'market refresh error: {e}')
            return

        import re
        new_markets = {}
        new_token_map = {}

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

            outcome_prices = mkt.get('outcomePrices')
            if isinstance(outcome_prices, str):
                outcome_prices = json.loads(outcome_prices)

            up_token, down_token = tokens[0], tokens[1]

            new_markets[coin] = {
                'coin': coin,
                'window_ts': window_ts,
                'slug': slug,
                'up_token': up_token,
                'down_token': down_token,
                'condition_id': mkt.get('conditionId', ''),
                'volume': float(event.get('volume', 0) or mkt.get('volume', 0) or 0),
                'liquidity': float(event.get('liquidity', 0) or mkt.get('liquidity', 0) or 0),
                'up_price': float(outcome_prices[0]) if outcome_prices else 0.5,
                'down_price': float(outcome_prices[1]) if outcome_prices else 0.5,
                'raw': event
            }

            new_token_map[up_token] = {'coin': coin, 'side': 'up', 'window_ts': window_ts}
            new_token_map[down_token] = {'coin': coin, 'side': 'down', 'window_ts': window_ts}

        self.markets = new_markets
        self.token_map = new_token_map
        self.last_refresh = now

        print(f'markets refreshed: {list(new_markets.keys())}')
        return list(new_token_map.keys())

    def get_token_info(self, token_id: str) -> Optional[dict]:
        return self.token_map.get(token_id)

    def get_all_tokens(self) -> list:
        return list(self.token_map.keys())


class RawCapture:
    def __init__(self):
        self.db = ClickHouseWriter()
        self.tracker = MarketTracker()
        self.running = True
        self.stats = {
            'clob_msgs': 0,
            'rtds_msgs': 0,
            'errors': 0
        }

    async def run(self):
        self.db.connect()

        async with aiohttp.ClientSession() as session:
            # initial market fetch
            await self.tracker.refresh(session)
            await self._save_market_metadata()

            # start tasks
            tasks = [
                asyncio.create_task(self._clob_loop(session)),
                asyncio.create_task(self._rtds_loop()),
                asyncio.create_task(self._refresh_loop(session)),
                asyncio.create_task(self._flush_loop()),
                asyncio.create_task(self._stats_loop()),
            ]

            try:
                await asyncio.gather(*tasks)
            except asyncio.CancelledError:
                pass
            finally:
                self.db.close()

    async def _clob_loop(self, session: aiohttp.ClientSession):
        import websockets

        while self.running:
            tokens = self.tracker.get_all_tokens()
            if not tokens:
                print('no tokens, waiting...')
                await asyncio.sleep(5)
                continue

            try:
                async with websockets.connect(CLOB_WS) as ws:
                    print(f'clob connected, subscribing to {len(tokens)} tokens')

                    await ws.send(json.dumps({
                        'type': 'subscribe',
                        'channel': 'market',
                        'assets_ids': tokens
                    }))

                    async for msg in ws:
                        if not self.running:
                            break
                        await self._handle_clob_msg(msg)

            except Exception as e:
                self.stats['errors'] += 1
                print(f'clob error: {e}')
                await asyncio.sleep(2)

    async def _handle_clob_msg(self, raw: str):
        received_at = datetime.utcnow()
        self.stats['clob_msgs'] += 1

        try:
            data = json.loads(raw)
            if isinstance(data, list):
                data = data[0] if data else None
            if not data:
                return

            event_type = data.get('event_type')
            event_ts_str = data.get('timestamp', '0')
            event_ts = datetime.utcfromtimestamp(int(event_ts_str) / 1000) if event_ts_str else received_at

            if event_type == 'book':
                asset_id = data.get('asset_id', '')
                info = self.tracker.get_token_info(asset_id)
                if not info:
                    return

                self.db.buffer_event('clob_book', (
                    received_at,
                    event_ts,
                    asset_id,
                    info['coin'],
                    info['side'],
                    info['window_ts'],
                    json.dumps(data.get('bids', [])),
                    json.dumps(data.get('asks', [])),
                    raw
                ))

            elif event_type == 'price_change':
                for pc in data.get('price_changes', []):
                    asset_id = pc.get('asset_id', '')
                    info = self.tracker.get_token_info(asset_id)
                    if not info:
                        continue

                    self.db.buffer_event('clob_price_change', (
                        received_at,
                        event_ts,
                        asset_id,
                        info['coin'],
                        info['side'],
                        info['window_ts'],
                        float(pc.get('price', 0)),
                        float(pc.get('size', 0)),
                        pc.get('side', ''),
                        float(pc.get('best_bid', 0)),
                        float(pc.get('best_ask', 0)),
                        json.dumps(pc)
                    ))

            elif event_type == 'last_trade_price':
                asset_id = data.get('asset_id', '')
                info = self.tracker.get_token_info(asset_id)
                if not info:
                    return

                self.db.buffer_event('clob_trade', (
                    received_at,
                    event_ts,
                    asset_id,
                    info['coin'],
                    info['side'],
                    info['window_ts'],
                    float(data.get('price', 0)),
                    float(data.get('size', 0)),
                    data.get('side', ''),
                    raw
                ))

        except Exception as e:
            self.stats['errors'] += 1

    async def _rtds_loop(self):
        import websockets

        while self.running:
            try:
                async with websockets.connect(RTDS_WS) as ws:
                    print('rtds connected')

                    # subscribe to both price feeds
                    await ws.send(json.dumps({
                        'action': 'subscribe',
                        'subscriptions': [
                            {'topic': 'crypto_prices', 'type': '*', 'filters': ''},
                            {'topic': 'crypto_prices_chainlink', 'type': '*', 'filters': ''}
                        ]
                    }))

                    # ping task
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
                            await self._handle_rtds_msg(msg)
                    finally:
                        ping_task.cancel()

            except Exception as e:
                self.stats['errors'] += 1
                print(f'rtds error: {e}')
                await asyncio.sleep(2)

    async def _handle_rtds_msg(self, raw: str):
        if not raw.startswith('{'):
            return

        received_at = datetime.utcnow()
        self.stats['rtds_msgs'] += 1

        try:
            data = json.loads(raw)
            topic = data.get('topic', '')
            payload = data.get('payload', {})

            if topic in ('crypto_prices', 'crypto_prices_chainlink'):
                symbol = payload.get('symbol', '')
                value = payload.get('value')
                ts = payload.get('timestamp', 0)

                if symbol and value:
                    source = 'chainlink' if topic == 'crypto_prices_chainlink' else 'binance'
                    source_ts = datetime.utcfromtimestamp(ts / 1000) if ts else received_at

                    self.db.buffer_event('crypto_price', (
                        received_at,
                        source_ts,
                        source,
                        symbol.lower(),
                        float(value),
                        raw
                    ))

        except Exception as e:
            self.stats['errors'] += 1

    async def _refresh_loop(self, session: aiohttp.ClientSession):
        while self.running:
            await asyncio.sleep(60)  # every minute

            now = int(time.time())
            window_start = now - (now % 900)

            # check if we need new markets (window changed)
            needs_refresh = False
            for coin, market in self.tracker.markets.items():
                if market['window_ts'] != window_start:
                    needs_refresh = True
                    break

            if needs_refresh or not self.tracker.markets:
                await self.tracker.refresh(session)
                await self._save_market_metadata()

    async def _save_market_metadata(self):
        now = datetime.utcnow()
        for coin, m in self.tracker.markets.items():
            self.db.buffer_event('market_metadata', (
                now,
                m['coin'],
                m['window_ts'],
                m['slug'],
                m['up_token'],
                m['down_token'],
                m['condition_id'],
                m['volume'],
                m['liquidity'],
                m['up_price'],
                m['down_price'],
                json.dumps(m['raw'])
            ))
        self.db._flush_table('market_metadata')

    async def _flush_loop(self):
        while self.running:
            await asyncio.sleep(self.db.flush_interval)
            self.db.flush_all()

    async def _stats_loop(self):
        while self.running:
            await asyncio.sleep(30)
            print(f"stats: clob={self.stats['clob_msgs']:,} rtds={self.stats['rtds_msgs']:,} errors={self.stats['errors']}")

    def stop(self):
        self.running = False


def main():
    print('='*60)
    print('POLYMARKET RAW DATA CAPTURE')
    print('='*60)
    print(f'clickhouse: {CH_HOST}:{CH_PORT}/{CH_DB}')
    print()

    capture = RawCapture()

    def handle_signal(sig, frame):
        print('\nshutting down...')
        capture.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    asyncio.run(capture.run())
    print('done')


if __name__ == '__main__':
    main()
