"""
arb monitor - watches for combined < 1.00 opportunities
uses CLOB websocket for real-time orderbook prices
"""
import asyncio
import json
import time
import re
from datetime import datetime
from dataclasses import dataclass

import aiohttp
import websockets

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


@dataclass
class Market:
    coin: str
    window_ts: int
    up_token: str
    down_token: str
    up_ask: float = 1.0
    down_ask: float = 1.0
    up_bid: float = 0.0
    down_bid: float = 0.0
    last_update: float = 0

    @property
    def combined_ask(self) -> float:
        return self.up_ask + self.down_ask

    @property
    def combined_bid(self) -> float:
        return self.up_bid + self.down_bid

    @property
    def time_left(self) -> float:
        return (self.window_ts + 900 - time.time()) / 60


class ArbMonitor:
    def __init__(self):
        self.markets: dict[str, Market] = {}
        self.token_to_coin: dict[str, tuple[str, str]] = {}  # token -> (coin, side)
        self.arb_threshold = 0.995  # alert when combined < 99.5c
        self.session = None
        self.arb_count = 0

    async def discover_markets(self):
        """find active 15m markets"""
        url = f"{GAMMA_API}/events?tag_id=102467&closed=false&limit=20"
        async with self.session.get(url) as resp:
            events = await resp.json()

        now = int(time.time())

        for event in events:
            slug = event.get('slug', '')
            match = re.search(r'(btc|eth|sol|xrp).*15m-(\d+)', slug.lower())
            if not match:
                continue

            coin = match.group(1)
            window_ts = int(match.group(2))

            # only active windows
            if not (window_ts <= now <= window_ts + 900):
                continue

            markets = event.get('markets', [])
            if not markets:
                continue

            tokens = markets[0].get('clobTokenIds', '[]')
            if isinstance(tokens, str):
                tokens = json.loads(tokens)
            if len(tokens) < 2:
                continue

            up_token, down_token = tokens[0], tokens[1]

            if coin not in self.markets or self.markets[coin].window_ts != window_ts:
                self.markets[coin] = Market(
                    coin=coin,
                    window_ts=window_ts,
                    up_token=up_token,
                    down_token=down_token,
                )
                self.token_to_coin[up_token] = (coin, 'up')
                self.token_to_coin[down_token] = (coin, 'down')
                print(f"[{coin.upper()}] tracking: {self.markets[coin].time_left:.1f}m left")

    def process_message(self, data: dict):
        """process CLOB websocket message"""
        event_type = data.get('event_type')

        if event_type == 'price_change':
            for pc in data.get('price_changes', []):
                asset_id = pc.get('asset_id')
                if asset_id not in self.token_to_coin:
                    continue

                coin, side = self.token_to_coin[asset_id]
                market = self.markets.get(coin)
                if not market:
                    continue

                best_bid = float(pc.get('best_bid', 0))
                best_ask = float(pc.get('best_ask', 1))

                if side == 'up':
                    market.up_bid = best_bid
                    market.up_ask = best_ask
                else:
                    market.down_bid = best_bid
                    market.down_ask = best_ask

                market.last_update = time.time()

                # check for arb
                if market.combined_ask < self.arb_threshold:
                    self.arb_count += 1
                    edge = 1 - market.combined_ask
                    print(f"\n{'='*50}")
                    print(f"ARB #{self.arb_count} | {coin.upper()} | {market.time_left:.1f}m left")
                    print(f"  UP ask:   ${market.up_ask:.3f}")
                    print(f"  DOWN ask: ${market.down_ask:.3f}")
                    print(f"  Combined: ${market.combined_ask:.4f}")
                    print(f"  EDGE:     {edge:.2%}")
                    print(f"{'='*50}\n")

        elif event_type == 'book':
            asset_id = data.get('asset_id')
            if asset_id not in self.token_to_coin:
                return

            coin, side = self.token_to_coin[asset_id]
            market = self.markets.get(coin)
            if not market:
                return

            bids = data.get('bids', [])
            asks = data.get('asks', [])

            best_bid = max((float(b['price']) for b in bids), default=0)
            best_ask = min((float(a['price']) for a in asks), default=1)

            if side == 'up':
                market.up_bid = best_bid
                market.up_ask = best_ask
            else:
                market.down_bid = best_bid
                market.down_ask = best_ask

            market.last_update = time.time()

    async def websocket_loop(self):
        """connect to CLOB websocket and process messages"""
        while True:
            if not self.markets:
                await asyncio.sleep(1)
                continue

            # collect all tokens to subscribe
            all_tokens = []
            for market in self.markets.values():
                all_tokens.extend([market.up_token, market.down_token])

            if not all_tokens:
                await asyncio.sleep(1)
                continue

            try:
                async with websockets.connect(CLOB_WS, ping_interval=30) as ws:
                    # subscribe
                    sub = {
                        "type": "subscribe",
                        "channel": "market",
                        "assets_ids": all_tokens
                    }
                    await ws.send(json.dumps(sub))
                    print(f"subscribed to {len(all_tokens)} tokens")

                    async for msg in ws:
                        try:
                            data = json.loads(msg)
                            if isinstance(data, list):
                                for item in data:
                                    self.process_message(item)
                            else:
                                self.process_message(data)
                        except json.JSONDecodeError:
                            pass

            except Exception as e:
                print(f"ws error: {e}")
                await asyncio.sleep(2)

    async def market_refresh_loop(self):
        """refresh markets every 30s"""
        while True:
            await self.discover_markets()
            await asyncio.sleep(30)

    async def status_loop(self):
        """print status every 5s"""
        await asyncio.sleep(3)

        while True:
            await asyncio.sleep(5)

            if not self.markets:
                print("no active markets")
                continue

            ts = datetime.now().strftime("%H:%M:%S")
            lines = []
            for coin, m in sorted(self.markets.items()):
                if m.last_update == 0:
                    lines.append(f"{coin.upper()}: waiting...")
                else:
                    combined = m.combined_ask
                    indicator = "**" if combined < self.arb_threshold else ""
                    lines.append(f"{coin.upper()}: {combined:.3f}{indicator} ({m.time_left:.1f}m)")

            print(f"[{ts}] {' | '.join(lines)} | arbs={self.arb_count}")

    async def run(self):
        print("=" * 60)
        print("ARB MONITOR - watching for combined < 1.00")
        print("=" * 60)

        self.session = aiohttp.ClientSession()

        try:
            await asyncio.gather(
                self.market_refresh_loop(),
                self.websocket_loop(),
                self.status_loop(),
            )
        except KeyboardInterrupt:
            print("\nshutting down...")
        finally:
            await self.session.close()


if __name__ == "__main__":
    monitor = ArbMonitor()
    asyncio.run(monitor.run())
