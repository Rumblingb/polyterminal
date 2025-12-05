"""
Record order book + binance price every second during a 15-min window
Run this to collect real data for backtesting
"""
import asyncio
import json
import time
from datetime import datetime
from pathlib import Path

import aiohttp
import websockets

BINANCE_WS = "wss://stream.binance.com:9443/ws/btcusdt@trade"
CLOB_API = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"


async def get_active_btc_market():
    """find active btc 15m market"""
    async with aiohttp.ClientSession() as session:
        url = f"{GAMMA_API}/events?tag_id=102467&closed=false&limit=10"
        async with session.get(url) as resp:
            data = await resp.json()
            for e in data:
                if "btc-updown-15m" in e.get("slug", ""):
                    start_str = e.get("startTime", "")
                    if start_str:
                        from datetime import timedelta
                        start = datetime.fromisoformat(start_str.replace("Z", "+00:00")).replace(tzinfo=None)
                        end = start + timedelta(minutes=15)
                        now = datetime.utcnow()
                        if start <= now <= end:
                            for m in e.get("markets", []):
                                tokens = json.loads(m.get("clobTokenIds", "[]"))
                                if tokens:
                                    return {
                                        "slug": e["slug"],
                                        "start": start,
                                        "end": end,
                                        "up_token": tokens[0],
                                        "down_token": tokens[1],
                                    }
    return None


async def get_book(session, token_id):
    """fetch order book"""
    url = f"{CLOB_API}/book?token_id={token_id}"
    try:
        async with session.get(url) as resp:
            if resp.status == 200:
                return await resp.json()
    except:
        pass
    return None


async def record_window():
    """record data for one 15-min window"""
    print("Looking for active BTC 15m market...")

    market = await get_active_btc_market()
    if not market:
        print("No active market found. Wait for next window.")
        return

    print(f"Found: {market['slug']}")
    print(f"Window: {market['start'].strftime('%H:%M')} - {market['end'].strftime('%H:%M')} UTC")

    records = []
    btc_price = [None]  # mutable container for ws callback

    async def binance_ws():
        async with websockets.connect(BINANCE_WS) as ws:
            async for msg in ws:
                data = json.loads(msg)
                btc_price[0] = float(data.get("p", 0))

    async def record_loop():
        async with aiohttp.ClientSession() as session:
            start_time = time.time()
            window_end = market["end"].timestamp()

            while time.time() < window_end:
                now = datetime.utcnow()

                # get order books
                up_book = await get_book(session, market["up_token"])
                down_book = await get_book(session, market["down_token"])

                if up_book and down_book:
                    up_bids = up_book.get("bids", [])
                    up_asks = up_book.get("asks", [])
                    down_bids = down_book.get("bids", [])
                    down_asks = down_book.get("asks", [])

                    record = {
                        "ts": now.isoformat(),
                        "btc": btc_price[0],
                        "up_bid": float(up_bids[-1]["price"]) if up_bids else 0,
                        "up_ask": float(up_asks[-1]["price"]) if up_asks else 1,
                        "up_bid_size": float(up_bids[-1]["size"]) if up_bids else 0,
                        "up_ask_size": float(up_asks[-1]["size"]) if up_asks else 0,
                        "down_bid": float(down_bids[-1]["price"]) if down_bids else 0,
                        "down_ask": float(down_asks[-1]["price"]) if down_asks else 1,
                    }
                    records.append(record)

                    elapsed = time.time() - start_time
                    btc_str = f"${btc_price[0]:,.0f}" if btc_price[0] else "waiting..."
                    print(
                        f"[{elapsed:5.1f}s] BTC: {btc_str} | "
                        f"UP: {record['up_bid']:.2f}/{record['up_ask']:.2f} | "
                        f"DOWN: {record['down_bid']:.2f}/{record['down_ask']:.2f}"
                    )

                await asyncio.sleep(1)  # record every second

    # run both
    print("\nRecording... (Ctrl+C to stop early)")
    try:
        await asyncio.gather(
            binance_ws(),
            record_loop(),
        )
    except asyncio.CancelledError:
        pass

    # save data
    output_file = Path(f"data/window_{market['slug']}.json")
    output_file.parent.mkdir(exist_ok=True)

    with open(output_file, "w") as f:
        json.dump({
            "market": market["slug"],
            "start": market["start"].isoformat(),
            "end": market["end"].isoformat(),
            "records": records,
        }, f, indent=2)

    print(f"\nSaved {len(records)} records to {output_file}")


if __name__ == "__main__":
    asyncio.run(record_window())
