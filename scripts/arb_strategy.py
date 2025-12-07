"""
COMBINED PRICE ARBITRAGE BOT

Strategy: Post limit buys on BOTH UP and DOWN when combined ask < threshold.
When both sides fill, profit is locked regardless of outcome.

Based on reverse-engineering gabagool22's $191k strategy.
"""

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Optional
import aiohttp

# config
MIN_EDGE = 0.02  # minimum 2% edge (combined < 0.98)
ORDER_SIZE_USD = 5  # dollars per order
MAX_POSITION_USD = 500  # max exposure per market
REPRICE_INTERVAL = 2  # seconds between repricing

@dataclass
class Market:
    condition_id: str
    up_token: str
    down_token: str
    slug: str

@dataclass
class Position:
    up_shares: float = 0
    down_shares: float = 0
    up_cost: float = 0
    down_cost: float = 0

    @property
    def matched(self) -> float:
        return min(self.up_shares, self.down_shares)

    @property
    def combined_price(self) -> float:
        if self.up_shares == 0 or self.down_shares == 0:
            return 1.0
        up_avg = self.up_cost / self.up_shares
        down_avg = self.down_cost / self.down_shares
        return up_avg + down_avg

    @property
    def locked_profit(self) -> float:
        if self.matched == 0:
            return 0
        return self.matched * (1 - self.combined_price)

class ArbBot:
    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.positions: dict[str, Position] = {}
        self.active_orders: dict[str, list] = {}  # market -> [order_ids]

    async def get_orderbook(self, token_id: str) -> dict:
        """fetch current orderbook for a token"""
        async with aiohttp.ClientSession() as session:
            url = f"https://clob.polymarket.com/book?token_id={token_id}"
            async with session.get(url) as resp:
                return await resp.json()

    async def get_best_prices(self, market: Market) -> tuple[float, float, float, float]:
        """get best bid/ask for both sides"""
        up_book, down_book = await asyncio.gather(
            self.get_orderbook(market.up_token),
            self.get_orderbook(market.down_token)
        )

        up_bid = float(up_book['bids'][0]['price']) if up_book.get('bids') else 0
        up_ask = float(up_book['asks'][0]['price']) if up_book.get('asks') else 1
        down_bid = float(down_book['bids'][0]['price']) if down_book.get('bids') else 0
        down_ask = float(down_book['asks'][0]['price']) if down_book.get('asks') else 1

        return up_bid, up_ask, down_bid, down_ask

    def calculate_arb_prices(self, up_ask: float, down_ask: float) -> tuple[float, float, float]:
        """
        calculate where to post bids to capture arb

        if up_ask=0.45 and down_ask=0.54, combined=0.99
        we want to bid slightly below ask to get filled
        """
        combined = up_ask + down_ask
        edge = 1 - combined

        # bid 1 tick below ask (0.01 tick size)
        up_bid_price = up_ask - 0.01
        down_bid_price = down_ask - 0.01

        return up_bid_price, down_bid_price, edge

    async def post_order(self, token_id: str, side: str, price: float, size: float) -> Optional[str]:
        """post a limit order - returns order_id if successful"""
        # TODO: implement actual order posting via CLOB API
        # requires signing with py_clob_client
        print(f"POST {side} {size:.1f} shares @ {price:.3f} on {token_id[:16]}...")
        return None  # placeholder

    async def cancel_order(self, order_id: str):
        """cancel an existing order"""
        # TODO: implement
        pass

    async def run_market(self, market: Market):
        """main loop for a single market"""
        position = Position()
        self.positions[market.condition_id] = position

        print(f"\n=== STARTING {market.slug} ===")

        while True:
            try:
                # get current prices
                up_bid, up_ask, down_bid, down_ask = await self.get_best_prices(market)
                combined_ask = up_ask + down_ask
                edge = 1 - combined_ask

                print(f"UP: {up_bid:.3f}/{up_ask:.3f} | DOWN: {down_bid:.3f}/{down_ask:.3f} | Combined: {combined_ask:.3f} | Edge: {edge:+.2%}")

                # check if arb exists
                if edge >= MIN_EDGE:
                    # calculate position value
                    current_exposure = position.up_cost + position.down_cost

                    if current_exposure < MAX_POSITION_USD:
                        # calculate order sizes
                        up_price, down_price, _ = self.calculate_arb_prices(up_ask, down_ask)
                        up_shares = ORDER_SIZE_USD / up_price
                        down_shares = ORDER_SIZE_USD / down_price

                        print(f">>> ARB OPPORTUNITY: {edge:.2%} edge")
                        print(f"    POST BUY {up_shares:.1f} UP @ {up_price:.3f}")
                        print(f"    POST BUY {down_shares:.1f} DOWN @ {down_price:.3f}")

                        # post orders (both sides)
                        # await self.post_order(market.up_token, 'BUY', up_price, up_shares)
                        # await self.post_order(market.down_token, 'BUY', down_price, down_shares)
                else:
                    print(f"    No arb (need {MIN_EDGE:.0%}, have {edge:.2%})")

                # show position
                if position.matched > 0:
                    print(f"    Position: {position.matched:.0f} matched, {position.locked_profit:.2f} locked profit")

                await asyncio.sleep(REPRICE_INTERVAL)

            except Exception as e:
                print(f"Error: {e}")
                await asyncio.sleep(5)

async def main():
    # example usage
    bot = ArbBot(api_key="", api_secret="")

    # get active 15m markets
    async with aiohttp.ClientSession() as session:
        url = "https://gamma-api.polymarket.com/events?tag_id=102467&active=true"
        async with session.get(url) as resp:
            events = await resp.json()

    # find BTC 15m markets
    for event in events[:5]:
        slug = event.get('slug', '')
        if 'bitcoin' in slug.lower() and ('15m' in slug or 'up-or-down' in slug.lower()):
            markets = event.get('markets', [])
            if len(markets) >= 2:
                # assume first is UP, second is DOWN (verify in practice)
                market = Market(
                    condition_id=event.get('conditionId', ''),
                    up_token=markets[0].get('clobTokenIds', [''])[0],
                    down_token=markets[1].get('clobTokenIds', [''])[0] if len(markets) > 1 else '',
                    slug=slug
                )
                print(f"Found market: {slug}")
                print(f"  UP token: {market.up_token[:20]}...")
                print(f"  DOWN token: {market.down_token[:20]}...")

                # run bot on this market
                await bot.run_market(market)
                break

if __name__ == "__main__":
    asyncio.run(main())
