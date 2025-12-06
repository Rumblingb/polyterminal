"""
data collection pipeline for btc 15m markets
collects: window info, poly price history, btc prices, outcomes
"""
import asyncio
import json
import re
import time
import aiohttp
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
BINANCE_API = "https://api.binance.com/api/v3"

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)


@dataclass
class WindowData:
    slug: str
    start_ts: int
    end_ts: int
    up_token: str
    down_token: str
    btc_start: Optional[float] = None
    btc_end: Optional[float] = None
    outcome: Optional[str] = None  # "UP" or "DOWN"
    up_prices: list = None  # [{t, p}, ...]
    down_prices: list = None


async def get_btc_price_at(session, ts: int) -> Optional[float]:
    """get btc price at specific timestamp from binance klines"""
    url = f"{BINANCE_API}/klines"
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
    except Exception as e:
        print(f"  btc price error: {e}")
    return None


async def get_price_history(session, token_id: str, start_ts: int, end_ts: int) -> list:
    """get poly price history for a token"""
    url = f"{CLOB_API}/prices-history"
    params = {
        "market": token_id,
        "startTs": start_ts,
        "endTs": end_ts,
        "fidelity": 1
    }
    try:
        async with session.get(url, params=params) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("history", [])
    except Exception as e:
        print(f"  price history error: {e}")
    return []


async def get_markets(session, limit: int = 500, closed: bool = True) -> list:
    """get btc 15m markets from gamma api"""
    status = "closed=true" if closed else "closed=false"
    url = f"{GAMMA_API}/events?tag_id=102467&{status}&limit={limit}"

    async with session.get(url) as resp:
        data = await resp.json()

    markets = []
    for e in data:
        slug = e.get("slug", "")
        if "btc" not in slug.lower():
            continue

        # extract timestamp from slug
        match = re.search(r'15m-(\d+)', slug)
        if not match:
            continue

        start_ts = int(match.group(1))
        end_ts = start_ts + 900

        for m in e.get("markets", []):
            tokens = m.get("clobTokenIds", "[]")
            if isinstance(tokens, str):
                tokens = json.loads(tokens)
            if len(tokens) >= 2:
                markets.append(WindowData(
                    slug=slug,
                    start_ts=start_ts,
                    end_ts=end_ts,
                    up_token=tokens[0],
                    down_token=tokens[1],
                    up_prices=[],
                    down_prices=[]
                ))
                break

    return sorted(markets, key=lambda x: x.start_ts, reverse=True)


async def collect_window(session, window: WindowData, get_prices: bool = True) -> WindowData:
    """collect all data for a single window"""

    # get btc prices
    window.btc_start = await get_btc_price_at(session, window.start_ts)
    window.btc_end = await get_btc_price_at(session, window.end_ts)

    # determine outcome
    if window.btc_start and window.btc_end:
        window.outcome = "UP" if window.btc_end > window.btc_start else "DOWN"

    # get price histories (expensive - optional)
    if get_prices:
        window.up_prices = await get_price_history(
            session, window.up_token, window.start_ts, window.end_ts
        )
        await asyncio.sleep(0.05)  # rate limit
        window.down_prices = await get_price_history(
            session, window.down_token, window.start_ts, window.end_ts
        )

    return window


async def collect_all(limit: int = 200, get_prices: bool = True):
    """main collection routine"""
    print(f"Collecting BTC 15m market data (limit={limit})")
    print("=" * 60)

    async with aiohttp.ClientSession() as session:
        # get market list
        print("Fetching market list...")
        markets = await get_markets(session, limit=limit)
        print(f"Found {len(markets)} BTC 15m markets")
        print()

        # collect each window
        collected = []
        for i, window in enumerate(markets):
            print(f"[{i+1}/{len(markets)}] {window.slug[:50]}...")

            try:
                window = await collect_window(session, window, get_prices=get_prices)
                collected.append(window)

                if window.btc_start and window.btc_end:
                    btc_change = (window.btc_end - window.btc_start) / window.btc_start * 100
                    print(f"  BTC: ${window.btc_start:,.0f} -> ${window.btc_end:,.0f} ({btc_change:+.3f}%)")
                    print(f"  Outcome: {window.outcome}")
                    if window.up_prices:
                        print(f"  Price points: {len(window.up_prices)}")

            except Exception as e:
                print(f"  ERROR: {e}")

            await asyncio.sleep(0.1)  # rate limit

            # save periodically
            if (i + 1) % 50 == 0:
                save_data(collected, f"btc_15m_partial_{i+1}.json")

        # final save
        filename = f"btc_15m_{len(collected)}_{int(time.time())}.json"
        save_data(collected, filename)

        print()
        print("=" * 60)
        print(f"COLLECTION COMPLETE")
        print(f"Total windows: {len(collected)}")
        print(f"Saved to: data/{filename}")

        # quick stats
        with_prices = [w for w in collected if w.up_prices]
        with_outcome = [w for w in collected if w.outcome]
        up_wins = [w for w in with_outcome if w.outcome == "UP"]

        print()
        print("STATS:")
        print(f"  Windows with price data: {len(with_prices)}")
        print(f"  Windows with outcome: {len(with_outcome)}")
        if with_outcome:
            print(f"  UP win rate: {len(up_wins)/len(with_outcome)*100:.1f}%")

        return collected


def save_data(windows: list, filename: str):
    """save to json"""
    data = [asdict(w) for w in windows]
    filepath = DATA_DIR / filename
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  Saved {len(windows)} windows to {filepath}")


def load_data(filename: str) -> list:
    """load from json"""
    filepath = DATA_DIR / filename
    with open(filepath) as f:
        data = json.load(f)
    return [WindowData(**d) for d in data]


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=200, help="max markets to fetch")
    parser.add_argument("--no-prices", action="store_true", help="skip price history (faster)")
    args = parser.parse_args()

    await collect_all(limit=args.limit, get_prices=not args.no_prices)


if __name__ == "__main__":
    asyncio.run(main())
