"""
polymarket 15m markets data recorder
records order books + spot prices at 500ms intervals
"""
import asyncio
import json
import os
import re
import time
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional

import aiohttp
import websockets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("recorder")

# config
POLL_INTERVAL = 0.5  # 500ms
MARKET_REFRESH = 30  # seconds
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
    slug: str
    up_token: str
    down_token: str
    db_id: Optional[int] = None


@dataclass
class Snapshot:
    ts: datetime
    market_id: int
    spot_price: float
    up_bid: float
    up_ask: float
    down_bid: float
    down_ask: float
    up_depth: dict = field(default_factory=dict)
    down_depth: dict = field(default_factory=dict)


class Recorder:
    def __init__(self):
        self.markets: dict[str, Market] = {}
        self.spot_prices: dict[str, float] = {}
        self.session: Optional[aiohttp.ClientSession] = None
        self.db_enabled = False
        self.stats = {"snapshots": 0, "markets": 0, "errors": 0}

    # =========================================================================
    # database
    # =========================================================================

    async def init_db(self) -> bool:
        if not SUPABASE_URL or not SUPABASE_KEY:
            log.warning("SUPABASE_URL/KEY not set - dry run mode")
            return False

        headers = self._db_headers()
        try:
            async with self.session.get(
                f"{SUPABASE_URL}/rest/v1/markets?limit=1",
                headers=headers
            ) as resp:
                if resp.status == 200:
                    log.info("supabase connected")
                    return True
                else:
                    log.error(f"supabase error: {resp.status}")
                    return False
        except Exception as e:
            log.error(f"supabase connection failed: {e}")
            return False

    def _db_headers(self):
        return {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=representation"
        }

    async def get_or_create_market(self, market: Market) -> Optional[int]:
        if not self.db_enabled:
            return None

        headers = self._db_headers()

        # check exists
        url = f"{SUPABASE_URL}/rest/v1/markets?coin=eq.{market.coin}&window_ts=eq.{market.window_ts}"
        try:
            async with self.session.get(url, headers=headers) as resp:
                data = await resp.json()
                if data:
                    return data[0]["id"]
        except Exception as e:
            log.error(f"db lookup error: {e}")
            return None

        # create
        payload = {
            "coin": market.coin,
            "window_ts": market.window_ts,
            "slug": market.slug,
            "up_token": market.up_token,
            "down_token": market.down_token,
        }
        try:
            async with self.session.post(
                f"{SUPABASE_URL}/rest/v1/markets",
                headers=headers,
                json=payload
            ) as resp:
                if resp.status == 201:
                    data = await resp.json()
                    self.stats["markets"] += 1
                    return data[0]["id"]
        except Exception as e:
            log.error(f"db insert error: {e}")
        return None

    async def insert_snapshot(self, snap: Snapshot):
        if not self.db_enabled or not snap.market_id:
            return

        headers = self._db_headers()
        payload = {
            "ts": snap.ts.isoformat(),
            "market_id": snap.market_id,
            "spot_price": snap.spot_price,
            "up_bid": snap.up_bid,
            "up_ask": snap.up_ask,
            "down_bid": snap.down_bid,
            "down_ask": snap.down_ask,
            "up_depth": snap.up_depth,
            "down_depth": snap.down_depth,
        }
        try:
            async with self.session.post(
                f"{SUPABASE_URL}/rest/v1/snapshots",
                headers=headers,
                json=payload
            ) as resp:
                if resp.status == 201:
                    self.stats["snapshots"] += 1
                else:
                    self.stats["errors"] += 1
        except Exception as e:
            self.stats["errors"] += 1

    async def update_resolution(self, market_id: int, outcome: str, spot_start: float, spot_end: float):
        if not self.db_enabled:
            return

        headers = self._db_headers()
        payload = {
            "outcome": outcome,
            "spot_start": spot_start,
            "spot_end": spot_end,
        }
        try:
            async with self.session.patch(
                f"{SUPABASE_URL}/rest/v1/markets?id=eq.{market_id}",
                headers=headers,
                json=payload
            ) as resp:
                if resp.status == 200:
                    log.info(f"resolution updated: market {market_id} -> {outcome}")
        except Exception as e:
            log.error(f"resolution update error: {e}")

    # =========================================================================
    # market discovery
    # =========================================================================

    async def fetch_markets(self):
        url = f"{GAMMA_API}/events?tag_id=102467&closed=false&limit=20"
        try:
            async with self.session.get(url) as resp:
                events = await resp.json()
        except Exception as e:
            log.error(f"gamma error: {e}")
            return

        now = int(time.time())

        for event in events:
            slug = event.get("slug", "")

            # parse coin and timestamp
            match = re.search(r"(btc|eth|sol|xrp).*15m-(\d+)", slug.lower())
            if not match:
                continue

            coin = match.group(1)
            window_ts = int(match.group(2))
            end_ts = window_ts + 900

            # only active windows
            if not (window_ts <= now <= end_ts):
                continue

            # get tokens
            markets = event.get("markets", [])
            if not markets:
                continue

            m = markets[0]
            tokens = m.get("clobTokenIds", "[]")
            if isinstance(tokens, str):
                tokens = json.loads(tokens)
            if len(tokens) < 2:
                continue

            # check if new market
            if coin not in self.markets or self.markets[coin].window_ts != window_ts:
                market = Market(
                    coin=coin,
                    window_ts=window_ts,
                    slug=slug,
                    up_token=tokens[0],
                    down_token=tokens[1],
                )
                market.db_id = await self.get_or_create_market(market)
                self.markets[coin] = market
                log.info(f"[{coin.upper()}] new window: {slug} (db_id={market.db_id})")

    # =========================================================================
    # order book fetching
    # =========================================================================

    async def fetch_book(self, token: str) -> tuple[float, float, dict]:
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

            return best_bid, best_ask, {"bids": bids, "asks": asks}
        except Exception as e:
            return 0, 1, {}

    # =========================================================================
    # binance websocket
    # =========================================================================

    async def binance_ws(self):
        streams = "/".join([f"{c}usdt@trade" for c in COINS])
        url = f"{BINANCE_WS}/{streams}"

        while True:
            try:
                async with websockets.connect(url) as ws:
                    log.info("binance ws connected")
                    async for msg in ws:
                        data = json.loads(msg)
                        symbol = data.get("s", "").lower().replace("usdt", "")
                        price = float(data.get("p", 0))
                        if symbol in COINS:
                            self.spot_prices[symbol] = price
            except Exception as e:
                log.error(f"binance ws error: {e}")
                await asyncio.sleep(1)

    # =========================================================================
    # main loops
    # =========================================================================

    async def poll_loop(self):
        await asyncio.sleep(2)  # wait for binance
        last_market_refresh = 0

        while True:
            loop_start = time.perf_counter()

            # refresh markets periodically
            if time.time() - last_market_refresh > MARKET_REFRESH:
                await self.fetch_markets()
                last_market_refresh = time.time()

            # fetch all books in parallel
            if self.markets:
                tokens = []
                for coin, market in self.markets.items():
                    tokens.append((coin, "up", market.up_token))
                    tokens.append((coin, "down", market.down_token))

                results = await asyncio.gather(
                    *[self.fetch_book(t[2]) for t in tokens]
                )

                # build snapshots
                ts = datetime.now(timezone.utc)
                books = {}
                for i, (coin, side, token) in enumerate(tokens):
                    bid, ask, depth = results[i]
                    if coin not in books:
                        books[coin] = {}
                    books[coin][side] = {"bid": bid, "ask": ask, "depth": depth}

                # insert snapshots
                for coin, market in self.markets.items():
                    if coin not in books:
                        continue

                    up = books[coin].get("up", {})
                    down = books[coin].get("down", {})

                    snap = Snapshot(
                        ts=ts,
                        market_id=market.db_id,
                        spot_price=self.spot_prices.get(coin, 0),
                        up_bid=up.get("bid", 0),
                        up_ask=up.get("ask", 1),
                        down_bid=down.get("bid", 0),
                        down_ask=down.get("ask", 1),
                        up_depth=up.get("depth", {}),
                        down_depth=down.get("depth", {}),
                    )
                    await self.insert_snapshot(snap)

            # maintain interval
            elapsed = time.perf_counter() - loop_start
            sleep_time = max(0, POLL_INTERVAL - elapsed)
            await asyncio.sleep(sleep_time)

    async def reporter(self):
        await asyncio.sleep(5)

        while True:
            await asyncio.sleep(10)

            lines = []
            for coin, market in self.markets.items():
                spot = self.spot_prices.get(coin, 0)
                time_left = (market.window_ts + 900 - time.time()) / 60
                lines.append(f"{coin.upper()}: ${spot:,.0f} ({time_left:.1f}m)")

            status = " | ".join(lines) if lines else "no active markets"
            stats = f"snaps={self.stats['snapshots']} mkts={self.stats['markets']} errs={self.stats['errors']}"
            log.info(f"{status} | {stats}")

    async def run(self):
        log.info("=" * 60)
        log.info("POLYMARKET 15M DATA RECORDER")
        log.info("=" * 60)
        log.info(f"poll interval: {POLL_INTERVAL}s")
        log.info(f"coins: {', '.join(COINS)}")
        log.info(f"supabase: {'enabled' if SUPABASE_URL else 'disabled (dry run)'}")
        log.info("=" * 60)

        self.session = aiohttp.ClientSession()

        try:
            self.db_enabled = await self.init_db()
            await self.fetch_markets()

            await asyncio.gather(
                self.binance_ws(),
                self.poll_loop(),
                self.reporter(),
            )
        except KeyboardInterrupt:
            log.info("shutting down...")
        finally:
            await self.session.close()
            log.info(f"final stats: {self.stats}")


if __name__ == "__main__":
    recorder = Recorder()
    asyncio.run(recorder.run())
