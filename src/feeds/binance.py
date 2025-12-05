import asyncio
import json
import logging
from typing import Callable

import websockets

from src.config import config

log = logging.getLogger(__name__)


class BinanceWS:
    """binance websocket client for real-time price feeds"""

    def __init__(self, on_price: Callable[[str, float], None]):
        self.on_price = on_price
        self.ws = None
        self._running = False
        self._prices: dict[str, float] = {}

    def get_price(self, coin: str) -> float | None:
        return self._prices.get(coin)

    async def run(self):
        self._running = True
        streams = "/".join(f"{s}@trade" for s in config.binance_symbols.values())
        url = f"{config.binance_ws}/{streams}"

        while self._running:
            try:
                log.info(f"connecting to binance ws: {url}")
                async with websockets.connect(url) as ws:
                    self.ws = ws
                    log.info("binance ws connected")
                    async for msg in ws:
                        await self._handle_message(msg)
            except websockets.ConnectionClosed as e:
                log.warning(f"binance ws disconnected: {e}, reconnecting...")
                await asyncio.sleep(1)
            except Exception as e:
                log.error(f"binance ws error: {e}, reconnecting...")
                await asyncio.sleep(5)

    async def _handle_message(self, msg: str):
        try:
            data = json.loads(msg)
            symbol = data.get("s", "").lower()
            price = float(data.get("p", 0))

            # map symbol back to coin
            for coin, sym in config.binance_symbols.items():
                if sym == symbol:
                    self._prices[coin] = price
                    if self.on_price:
                        self.on_price(coin, price)
                    break
        except Exception as e:
            log.error(f"error parsing binance message: {e}")

    def stop(self):
        self._running = False
        if self.ws:
            asyncio.create_task(self.ws.close())
