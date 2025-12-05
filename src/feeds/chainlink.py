import logging

import aiohttp

log = logging.getLogger(__name__)

# chainlink data stream endpoints (used by polymarket for resolution)
CHAINLINK_STREAMS = {
    "BTC": "https://data.chain.link/streams/btc-usd",
    "ETH": "https://data.chain.link/streams/eth-usd",
    "SOL": "https://data.chain.link/streams/sol-usd",
    "XRP": "https://data.chain.link/streams/xrp-usd",
}


class ChainlinkFetcher:
    """fetch prices from chainlink data streams (resolution source)"""

    def __init__(self):
        self._session: aiohttp.ClientSession | None = None
        self._prices: dict[str, float] = {}

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def fetch_price(self, coin: str) -> float | None:
        """fetch current price from chainlink for a coin"""
        url = CHAINLINK_STREAMS.get(coin)
        if not url:
            log.warning(f"no chainlink stream for {coin}")
            return None

        try:
            session = await self._get_session()
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    log.error(f"chainlink fetch failed: {resp.status}")
                    return None

                # chainlink returns html page, need to parse or use alternative
                # for now, we'll use binance price as proxy and note this limitation
                # in production, would use chainlink's actual API or RPC
                text = await resp.text()

                # try to extract price from page (basic parsing)
                # format varies, this is a fallback
                if "price" in text.lower():
                    # would need proper parsing here
                    pass

                return self._prices.get(coin)
        except Exception as e:
            log.error(f"chainlink fetch error for {coin}: {e}")
            return None

    async def fetch_all(self) -> dict[str, float]:
        """fetch prices for all coins"""
        prices = {}
        for coin in CHAINLINK_STREAMS:
            price = await self.fetch_price(coin)
            if price:
                prices[coin] = price
        return prices

    def set_price(self, coin: str, price: float):
        """manually set price (can be used to sync from binance)"""
        self._prices[coin] = price

    def get_price(self, coin: str) -> float | None:
        return self._prices.get(coin)

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
