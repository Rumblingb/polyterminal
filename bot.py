"""
BTC 15m trading bot
edge: poly underreacts to btc moves mid-window
"""
import asyncio
import json
import time
import websockets
import aiohttp
from dataclasses import dataclass
from typing import Optional

BINANCE_WS = "wss://stream.binance.com:9443/ws/btcusdt@trade"
POLY_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"


def log(msg):
    print(msg, flush=True)


@dataclass
class Window:
    token_up: str
    token_down: str
    start_ts: int
    end_ts: int
    start_btc: Optional[float] = None  # BTC price at window start (from binance kline)


@dataclass
class Signal:
    side: str  # "UP" or "DOWN"
    entry_price: float
    btc_pct: float
    time_left: float
    edge: float  # expected - actual price


class Bot:
    def __init__(self):
        self.window: Optional[Window] = None
        self.btc_price: float = 0
        self.poly_bid: float = 0.5
        self.poly_ask: float = 0.5
        self.position: Optional[dict] = None
        self.pnl: float = 0
        self.trades: list = []

        # strategy params
        self.min_btc_move = 0.08  # minimum btc % move to trigger
        self.min_edge = 0.10  # minimum edge (mispricing) to enter
        self.min_time_left = 3  # minutes
        self.max_time_left = 12  # minutes

        # paper trading
        self.paper_trades = []
        self.paper_pnl = 0

    async def get_btc_at_time(self, ts: int) -> Optional[float]:
        """get BTC price at specific timestamp from binance klines"""
        async with aiohttp.ClientSession() as session:
            url = "https://api.binance.com/api/v3/klines"
            params = {
                "symbol": "BTCUSDT",
                "interval": "1m",
                "startTime": ts * 1000,
                "limit": 1
            }
            try:
                async with session.get(url, params=params) as resp:
                    if resp.status == 200:
                        klines = await resp.json()
                        if klines:
                            return float(klines[0][1])  # open price
            except:
                pass
        return None

    async def get_active_window(self) -> Optional[Window]:
        async with aiohttp.ClientSession() as session:
            url = f"{GAMMA_API}/events?tag_id=102467&closed=false&limit=10"
            async with session.get(url) as resp:
                data = await resp.json()

            import re
            now = int(time.time())

            for e in data:
                slug = e.get("slug", "")
                if "btc" not in slug.lower() or "15m" not in slug:
                    continue

                match = re.search(r'15m-(\d+)', slug)
                if not match:
                    continue

                start_ts = int(match.group(1))
                end_ts = start_ts + 900

                # check if window is active
                if start_ts <= now <= end_ts:
                    for m in e.get("markets", []):
                        tokens = m.get("clobTokenIds", "[]")
                        if isinstance(tokens, str):
                            tokens = json.loads(tokens)
                        if len(tokens) >= 2:
                            return Window(
                                token_up=tokens[0],
                                token_down=tokens[1],
                                start_ts=start_ts,
                                end_ts=end_ts
                            )
        return None

    def calc_expected_price(self, btc_pct: float, time_left: float) -> float:
        """
        expected UP price based on btc move from WINDOW START

        from backtest data:
        - BTC < -0.05%: UP wins 2% (poly prices at 0.39)
        - BTC > +0.05%: UP wins 100% (poly prices at 0.66)

        poly is SLOW to price in the near-certainty
        """
        if abs(btc_pct) < 0.05:
            return 0.50  # too close to call

        # from backtest: btc direction predicts outcome 98%+ when > 0.08%
        if btc_pct > 0.08:
            return 0.98  # UP almost certain
        elif btc_pct > 0.05:
            return 0.90
        elif btc_pct < -0.08:
            return 0.02  # DOWN almost certain
        elif btc_pct < -0.05:
            return 0.10
        else:
            return 0.50

    def check_signal(self) -> Optional[Signal]:
        if not self.window or not self.window.start_btc:
            return None
        if self.btc_price == 0:  # binance not connected yet
            return None

        now = time.time()
        time_left = (self.window.end_ts - now) / 60

        # check time bounds
        if time_left < self.min_time_left or time_left > self.max_time_left:
            return None

        # calc btc move
        btc_pct = (self.btc_price - self.window.start_btc) / self.window.start_btc * 100

        # need significant move
        if abs(btc_pct) < self.min_btc_move:
            return None

        # calc expected vs actual
        expected_up = self.calc_expected_price(btc_pct, time_left)
        actual_up = (self.poly_bid + self.poly_ask) / 2

        # check for mispricing
        if btc_pct > 0:
            # btc up, UP should win, check if UP is underpriced
            edge = expected_up - actual_up
            if edge > self.min_edge:
                return Signal(
                    side="UP",
                    entry_price=self.poly_ask,
                    btc_pct=btc_pct,
                    time_left=time_left,
                    edge=edge
                )
        else:
            # btc down, DOWN should win, check if DOWN is underpriced
            # DOWN price = 1 - UP price
            expected_down = 1 - expected_up
            actual_down = 1 - actual_up
            edge = expected_down - actual_down
            if edge > self.min_edge:
                return Signal(
                    side="DOWN",
                    entry_price=1 - self.poly_bid,  # buy DOWN at its ask
                    btc_pct=btc_pct,
                    time_left=time_left,
                    edge=edge
                )

        return None

    async def binance_feed(self):
        while True:
            try:
                async with websockets.connect(BINANCE_WS) as ws:
                    log("Connected to Binance")
                    async for msg in ws:
                        data = json.loads(msg)
                        self.btc_price = float(data["p"])
            except Exception as e:
                log(f"Binance error: {e}, reconnecting...")
                await asyncio.sleep(1)

    async def poly_feed(self):
        """poll REST /book endpoint - more reliable than WS"""
        async with aiohttp.ClientSession() as session:
            while True:
                if not self.window:
                    await asyncio.sleep(1)
                    continue

                try:
                    url = f"{CLOB_API}/book?token_id={self.window.token_up}"
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            bids = data.get("bids", [])
                            asks = data.get("asks", [])
                            if bids:
                                self.poly_bid = max(float(b["price"]) for b in bids)
                            if asks:
                                self.poly_ask = min(float(a["price"]) for a in asks)
                except Exception as e:
                    log(f"Poly REST error: {e}")

                await asyncio.sleep(0.5)  # poll every 500ms

    async def strategy_loop(self):
        await asyncio.sleep(2)  # wait for feeds

        while True:
            await asyncio.sleep(0.5)

            # check for signal
            if self.position:
                continue  # already in position

            signal = self.check_signal()
            if signal:
                log("")
                log(f"=== SIGNAL: {signal.side} ===")
                log(f"BTC: {signal.btc_pct:+.2f}%")
                log(f"Entry: {signal.entry_price:.2f}")
                log(f"Time left: {signal.time_left:.1f}m")
                log(f"Edge: {signal.edge:.2f}")

                # paper trade
                self.position = {
                    "side": signal.side,
                    "entry": signal.entry_price,
                    "time": time.time()
                }
                self.trades.append(signal)

    async def reporter(self):
        await asyncio.sleep(3)

        while True:
            await asyncio.sleep(2)

            if not self.window or not self.window.start_btc:
                continue

            now = time.time()
            time_left = (self.window.end_ts - now) / 60
            btc_pct = (self.btc_price - self.window.start_btc) / self.window.start_btc * 100

            expected = self.calc_expected_price(btc_pct, time_left)
            actual = (self.poly_bid + self.poly_ask) / 2
            edge = expected - actual if btc_pct > 0 else (1 - expected) - (1 - actual)

            status = f"BTC {btc_pct:+.3f}% | UP {self.poly_bid:.2f}/{self.poly_ask:.2f}"
            status += f" | exp {expected:.2f} | edge {edge:+.2f}"
            status += f" | {time_left:.1f}m left"

            if self.position:
                status += f" | POS: {self.position['side']}"

            log(status)

    async def window_manager(self):
        """refresh window every 15 min"""
        while True:
            window = await self.get_active_window()
            if window:
                # also fetch start_btc if current window doesn't have it
                need_btc = (not self.window or
                           window.start_ts != self.window.start_ts or
                           not self.window.start_btc)
                if need_btc:
                    # get BTC price at window start
                    start_btc = await self.get_btc_at_time(window.start_ts)
                    if start_btc:
                        window.start_btc = start_btc
                        log(f"\n=== NEW WINDOW ===")
                        log(f"Start: {window.start_ts}")
                        log(f"BTC at start: ${start_btc:,.0f}")
                        self.window = window
                        self.position = None  # reset position for new window
                    else:
                        log("Failed to get BTC start price")

            await asyncio.sleep(10)

    async def run(self):
        log("Starting bot...")

        # get initial window
        self.window = await self.get_active_window()
        if self.window:
            log(f"Found active window")
        else:
            log("No active window, waiting...")

        await asyncio.gather(
            self.binance_feed(),
            self.poly_feed(),
            self.strategy_loop(),
            self.reporter(),
            self.window_manager()
        )


if __name__ == "__main__":
    bot = Bot()
    asyncio.run(bot.run())
