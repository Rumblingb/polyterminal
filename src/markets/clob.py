import logging
from dataclasses import dataclass

import aiohttp

from src.config import config

log = logging.getLogger(__name__)


@dataclass
class OrderBook:
    """order book snapshot"""

    token_id: str
    best_bid: float
    best_ask: float
    bid_size: float
    ask_size: float
    spread: float

    @property
    def mid_price(self) -> float:
        return (self.best_bid + self.best_ask) / 2


class ClobClient:
    """client for polymarket clob api (order books)"""

    def __init__(self):
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def fetch_book(self, token_id: str) -> OrderBook | None:
        """fetch order book for a token"""
        url = f"{config.clob_api}/book"
        params = {"token_id": token_id}

        try:
            session = await self._get_session()
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    log.error(f"clob book error: {resp.status}")
                    return None

                data = await resp.json()
                return self._parse_book(token_id, data)
        except Exception as e:
            log.error(f"clob fetch error: {e}")
            return None

    def _parse_book(self, token_id: str, data: dict) -> OrderBook | None:
        """parse order book response"""
        try:
            bids = data.get("bids", [])
            asks = data.get("asks", [])

            if not bids or not asks:
                return None

            # bids sorted low to high, best bid is last
            # asks sorted high to low, best ask is last
            best_bid = float(bids[-1]["price"]) if bids else 0
            best_ask = float(asks[-1]["price"]) if asks else 1
            bid_size = float(bids[-1]["size"]) if bids else 0
            ask_size = float(asks[-1]["size"]) if asks else 0

            return OrderBook(
                token_id=token_id,
                best_bid=best_bid,
                best_ask=best_ask,
                bid_size=bid_size,
                ask_size=ask_size,
                spread=best_ask - best_bid,
            )
        except Exception as e:
            log.error(f"error parsing book: {e}")
            return None

    async def get_executable_price(
        self, token_id: str, side: str, size: float
    ) -> tuple[float, float]:
        """
        calculate executable price for a given size
        returns (avg_price, total_cost)
        """
        url = f"{config.clob_api}/book"
        params = {"token_id": token_id}

        try:
            session = await self._get_session()
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return 0, 0

                data = await resp.json()

                if side == "BUY":
                    # we take from asks (sorted high to low, best is last)
                    levels = sorted(
                        data.get("asks", []), key=lambda x: float(x["price"])
                    )
                else:
                    # we take from bids (sorted low to high, best is last)
                    levels = sorted(
                        data.get("bids", []),
                        key=lambda x: float(x["price"]),
                        reverse=True,
                    )

                remaining = size
                total_cost = 0

                for level in levels:
                    price = float(level["price"])
                    available = float(level["size"])
                    take = min(remaining, available)

                    if side == "BUY":
                        total_cost += take * price
                    else:
                        total_cost += take * price

                    remaining -= take
                    if remaining <= 0:
                        break

                filled = size - remaining
                avg_price = total_cost / filled if filled > 0 else 0

                return avg_price, total_cost
        except Exception as e:
            log.error(f"executable price error: {e}")
            return 0, 0

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
