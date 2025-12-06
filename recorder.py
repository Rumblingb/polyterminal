"""
live recorder for polymarket 15m markets
polls every 500ms, stores to supabase
"""
import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional

import aiohttp
import websockets

# config
POLL_INTERVAL = 0.5  # 500ms
COINS = ["btc", "eth", "sol", "xrp"]

# apis
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
BINANCE_WS = "wss://stream.binance.com:9443/ws"

# supabase
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")


@dataclass
class Market:
    coin: str
    window_ts: int
    up_token: str
    down_token: str
    db_id: Optional[int] = None


class Recorder:
    def __init__(self):
        self.markets: dict[str, Market] = {}  # coin -> market
        self.spot_prices: dict[str, float] = {}  # coin -> price
        self.session: Optional[aiohttp.ClientSession] = None
        self.running = True

    async def init_db(self):
        """test supabase connection"""
        if not SUPABASE_URL or not SUPABASE_KEY:
            print("WARNING: SUPABASE_URL/KEY not set, running in dry-run mode")
            return False

        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json"
        }

        async with self.session.get(
            f"{SUPABASE_URL}/rest/v1/markets?limit=1",
            headers=headers
        ) as resp:
            if resp.status == 200:
                print("Supabase connected")
                return True
            else:
                print(f"Supabase error: {resp.status}")
                return False

    async def get_or_create_market(self, coin: str, window_ts: int, up_token: str, down_token: str) -> Optional[int]:
        """get market id from db, create if not exists"""
        if not SUPABASE_URL:
            return None

        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=representation"
        }

        # check if exists
        async with self.session.get(
            f"{SUPABASE_URL}/rest/v1/markets?coin=eq.{coin}&window_ts=eq.{window_ts}",
            headers=headers
        ) as resp:
            data = await resp.json()
            if data:
                return data[0]["id"]

        # create
        payload = {
            "coin": coin,
            "window_ts": window_ts,
            "up_token": up_token,
            "down_token": down_token
        }

        async with self.session.post(
            f"{SUPABASE_URL}/rest/v1/markets",
            headers=headers,
            json=payload
        ) as resp:
            if resp.status == 201:
                data = await resp.json()
                return data[0]["id"]
            else:
                print(f"Failed to create market: {resp.status}")
                return None

    async def insert_snapshot(self, market_id: int, ts: datetime, spot: float,
                             up_bid: float, up_ask: float, down_bid: float, down_ask: float,
                             up_depth: dict, down_depth: dict):
        """insert snapshot to db"""
        if not SUPABASE_URL or not market_id:
            return

        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json"
        }

        payload = {
            "ts": ts.isoformat(),
            "market_id": market_id,
            "spot_price": spot,
            "up_bid": up_bid,
            "up_ask": up_ask,
            "down_bid": down_bid,
            "down_ask": down_ask,
            "up_depth": up_depth,
            "down_depth": down_depth
        }

        try:
            async with self.session.post(
                f"{SUPABASE_URL}/rest/v1/snapshots",
                headers=headers,
                json=payload
            ) as resp:
                if resp.status != 201:
                    print(f"Snapshot insert failed: {resp.status}")
        except Exception as e:
            print(f"Snapshot error: {e}")

    async def fetch_active_markets(self):
        """get active 15m markets for all coins"""
        url = f"{GAMMA_API}/events?tag_id=102467&closed=false&limit=20"

        try:
            async with self.session.get(url) as resp:
                data = await resp.json()
        except Exception as e:
            print(f"Gamma API error: {e}")
            return

        now = int(time.time())

        for e in data:
            slug = e.get("slug", "")

            # extract coin and timestamp
            match = re.search(r'(btc|eth|sol|xrp).*15m-(\d+)', slug.lower())
            if not match:
                continue

            coin = match.group(1)
            window_ts = int(match.group(2))
            end_ts = window_ts + 900

            # check if window is active
            if not (window_ts <= now <= end_ts):
                continue

            # get tokens
            for m in e.get("markets", []):
                tokens = m.get("clobTokenIds", "[]")
                if isinstance(tokens, str):
                    tokens = json.loads(tokens)
                if len(tokens) >= 2:
                    # check if new market
                    if coin not in self.markets or self.markets[coin].window_ts != window_ts:
                        market = Market(
                            coin=coin,
                            window_ts=window_ts,
                            up_token=tokens[0],
                            down_token=tokens[1]
                        )
                        market.db_id = await self.get_or_create_market(
                            coin, window_ts, tokens[0], tokens[1]
                        )
                        self.markets[coin] = market
                        print(f"[{coin.upper()}] New window: {window_ts} (db_id={market.db_id})")
                    break

    async def fetch_book(self, token: str) -> tuple[float, float, dict]:
        """fetch order book, return (best_bid, best_ask, full_depth)"""
        url = f"{CLOB_API}/book?token_id={token}"

        try:
            async with self.session.get(url) as resp:
                if resp.status != 200:
                    return 0, 1, {}
                data = await resp.json()

            bids = data.get("bids", [])
            asks = data.get("asks", [])

            best_bid = max((float(b["price"]) for b in bids), default=0)
            best_ask = min((float(a["price"]) for a in asks), default=1)

            depth = {"bids": bids, "asks": asks}
            return best_bid, best_ask, depth

        except Exception as e:
            return 0, 1, {}

    async def binance_feed(self):
        """websocket feed for spot prices"""
        streams = "/".join([f"{c}usdt@trade" for c in COINS])
        url = f"{BINANCE_WS}/{streams}"

        while self.running:
            try:
                async with websockets.connect(url) as ws:
                    print("Binance WS connected")
                    async for msg in ws:
                        data = json.loads(msg)
                        symbol = data.get("s", "").lower().replace("usdt", "")
                        price = float(data.get("p", 0))
                        if symbol in COINS:
                            self.spot_prices[symbol] = price
            except Exception as e:
                print(f"Binance WS error: {e}, reconnecting...")
                await asyncio.sleep(1)

    async def poll_loop(self):
        """main polling loop - 500ms"""
        await asyncio.sleep(2)  # wait for binance

        while self.running:
            loop_start = time.perf_counter()

            # refresh markets every 30 sec
            if int(time.time()) % 30 == 0:
                await self.fetch_active_markets()

            # fetch all books in parallel
            tasks = []
            for coin, market in self.markets.items():
                tasks.append(self.fetch_book(market.up_token))
                tasks.append(self.fetch_book(market.down_token))

            if tasks:
                results = await asyncio.gather(*tasks)

                ts = datetime.now(timezone.utc)

                # process results (2 per coin: up, down)
                idx = 0
                for coin, market in self.markets.items():
                    up_bid, up_ask, up_depth = results[idx]
                    down_bid, down_ask, down_depth = results[idx + 1]
                    idx += 2

                    spot = self.spot_prices.get(coin, 0)

                    # insert to db
                    await self.insert_snapshot(
                        market.db_id, ts, spot,
                        up_bid, up_ask, down_bid, down_ask,
                        up_depth, down_depth
                    )

            # maintain 500ms interval
            elapsed = time.perf_counter() - loop_start
            sleep_time = max(0, POLL_INTERVAL - elapsed)
            await asyncio.sleep(sleep_time)

    async def reporter(self):
        """log status every 5 sec"""
        await asyncio.sleep(3)

        while self.running:
            await asyncio.sleep(5)

            lines = []
            for coin, market in self.markets.items():
                spot = self.spot_prices.get(coin, 0)
                time_left = (market.window_ts + 900 - time.time()) / 60
                lines.append(f"{coin.upper()}: ${spot:,.0f} | {time_left:.1f}m left")

            if lines:
                print(f"[{datetime.utcnow().strftime('%H:%M:%S')}] " + " | ".join(lines))

    async def run(self):
        """main entry point"""
        print("Starting recorder...")
        print(f"Polling interval: {POLL_INTERVAL}s")
        print(f"Coins: {', '.join(COINS)}")
        print()

        self.session = aiohttp.ClientSession()

        try:
            # init db
            db_ok = await self.init_db()
            if not db_ok:
                print("Running without database (dry-run)")

            # get initial markets
            await self.fetch_active_markets()

            # run all tasks
            await asyncio.gather(
                self.binance_feed(),
                self.poll_loop(),
                self.reporter()
            )
        finally:
            await self.session.close()


if __name__ == "__main__":
    recorder = Recorder()
    asyncio.run(recorder.run())
