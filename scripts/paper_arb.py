"""
Paper trading for passive limit order arb strategy.
Run this during active 15m windows to see real opportunities.
"""

import asyncio
import aiohttp
import json
from datetime import datetime
from dataclasses import dataclass, field

@dataclass
class Position:
    up_shares: float = 0
    down_shares: float = 0
    up_cost: float = 0
    down_cost: float = 0

    @property
    def matched(self):
        return min(self.up_shares, self.down_shares)

    @property
    def combined(self):
        if self.up_shares == 0 or self.down_shares == 0:
            return 1.0
        return (self.up_cost / self.up_shares) + (self.down_cost / self.down_shares)

    @property
    def locked_profit(self):
        return self.matched * (1 - self.combined)

async def get_markets():
    async with aiohttp.ClientSession() as session:
        url = "https://gamma-api.polymarket.com/events?tag_id=102467&closed=false&limit=10"
        async with session.get(url) as resp:
            events = await resp.json()

    markets = []
    for e in events:
        mkts = e.get('markets', [])
        if mkts:
            m = mkts[0]
            if m.get('acceptingOrders'):
                tokens = json.loads(m.get('clobTokenIds', '[]'))
                if len(tokens) >= 2:
                    markets.append({
                        'slug': e.get('slug', ''),
                        'up_token': tokens[0],
                        'down_token': tokens[1]
                    })
    return markets

async def get_book(session, token_id):
    url = f"https://clob.polymarket.com/book?token_id={token_id}"
    async with session.get(url) as resp:
        return await resp.json()

def has_liquidity(up_bid, up_ask, down_bid, down_ask):
    """check if orderbook has real liquidity (not just 0.01/0.99)"""
    # real liquidity means spread < 0.90
    up_spread = up_ask - up_bid
    down_spread = down_ask - down_bid
    return up_spread < 0.90 and down_spread < 0.90

async def run():
    print("=" * 60)
    print("PAPER ARB MONITOR")
    print("Waiting for markets with real liquidity...")
    print("=" * 60)

    positions = {}
    trades = []
    ORDER_SIZE = 10
    MIN_EDGE = 0.02  # 2%

    async with aiohttp.ClientSession() as session:
        tick = 0
        while True:
            tick += 1
            markets = await get_markets()
            now = datetime.now().strftime('%H:%M:%S')

            for market in markets:
                slug = market['slug']
                coin = slug.split('-')[0].upper()

                if slug not in positions:
                    positions[slug] = Position()
                pos = positions[slug]

                try:
                    up_book, down_book = await asyncio.gather(
                        get_book(session, market['up_token']),
                        get_book(session, market['down_token'])
                    )

                    up_bids = up_book.get('bids', [])
                    up_asks = up_book.get('asks', [])
                    down_bids = down_book.get('bids', [])
                    down_asks = down_book.get('asks', [])

                    up_bid = float(up_bids[0]['price']) if up_bids else 0
                    up_ask = float(up_asks[0]['price']) if up_asks else 1
                    down_bid = float(down_bids[0]['price']) if down_bids else 0
                    down_ask = float(down_asks[0]['price']) if down_asks else 1

                    # skip if no real liquidity
                    if not has_liquidity(up_bid, up_ask, down_bid, down_ask):
                        continue

                    combined_bid = up_bid + down_bid
                    bid_edge = 1 - combined_bid

                    # display
                    status = "ARB!" if bid_edge >= MIN_EDGE else "    "
                    print(f"{now} {status} {coin}: UP {up_bid:.2f}/{up_ask:.2f} DN {down_bid:.2f}/{down_ask:.2f} comb={combined_bid:.3f} edge={bid_edge:+.1%}")

                    # simulate fill
                    if bid_edge >= MIN_EDGE:
                        pos.up_shares += ORDER_SIZE
                        pos.down_shares += ORDER_SIZE
                        pos.up_cost += ORDER_SIZE * up_bid
                        pos.down_cost += ORDER_SIZE * down_bid

                        trade = {
                            'ts': now,
                            'slug': slug,
                            'up_bid': up_bid,
                            'down_bid': down_bid,
                            'combined': combined_bid,
                            'edge': bid_edge,
                            'shares': ORDER_SIZE
                        }
                        trades.append(trade)
                        print(f"         >>> FILL +{ORDER_SIZE} each side, locked P&L: ${pos.locked_profit:.2f}")

                except Exception as e:
                    pass

            # summary every 30 ticks
            if tick % 30 == 0:
                total = sum(p.locked_profit for p in positions.values())
                active = sum(1 for p in positions.values() if p.matched > 0)
                print(f"\n--- {tick} ticks | {active} positions | ${total:.2f} locked ---\n")

            await asyncio.sleep(2)

if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n\nFINAL SUMMARY")
        print("=" * 60)
