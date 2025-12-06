"""
record tick-level data from binance and polymarket websockets
"""
import asyncio
import json
import time
import websockets
import aiohttp
from datetime import datetime

BINANCE_WS = "wss://stream.binance.com:9443/ws/btcusdt@trade"
POLY_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
GAMMA_API = "https://gamma-api.polymarket.com"

ticks = []
start_btc = None


async def get_active_token():
    async with aiohttp.ClientSession() as session:
        url = f"{GAMMA_API}/events?tag_id=102467&closed=false&limit=10"
        async with session.get(url) as resp:
            data = await resp.json()

        for e in data:
            slug = e.get("slug", "")
            if "btc" not in slug.lower() or "15m" not in slug:
                continue
            for m in e.get("markets", []):
                tokens = m.get("clobTokenIds", "[]")
                if isinstance(tokens, str):
                    tokens = json.loads(tokens)
                if tokens:
                    return tokens[0], slug
    return None, None


async def binance_stream():
    global start_btc

    async with websockets.connect(BINANCE_WS) as ws:
        print("Connected to Binance")
        async for msg in ws:
            data = json.loads(msg)
            price = float(data["p"])
            ts = time.time()

            if start_btc is None:
                start_btc = price

            pct = (price - start_btc) / start_btc * 100
            ticks.append({
                "ts": ts,
                "source": "binance",
                "price": price,
                "pct": pct
            })


async def poly_stream(token_id):
    sub_msg = {
        "auth": {},
        "markets": [],
        "assets_ids": [token_id],
        "type": "market"
    }

    async with websockets.connect(POLY_WS) as ws:
        await ws.send(json.dumps(sub_msg))
        print(f"Subscribed to Poly token {token_id[:20]}...")

        async for msg in ws:
            ts = time.time()
            data = json.loads(msg)

            # handle list (batch) or single message
            if isinstance(data, list):
                events = data
            else:
                events = [data]

            for event in events:
                if not isinstance(event, dict):
                    continue

                event_type = event.get("event_type", "")

                if event_type == "book":
                    bids = event.get("bids", [])
                    asks = event.get("asks", [])
                    best_bid = max([float(b["price"]) for b in bids]) if bids else 0
                    best_ask = min([float(a["price"]) for a in asks]) if asks else 1
                    mid = (best_bid + best_ask) / 2

                    ticks.append({
                        "ts": ts,
                        "source": "poly",
                        "bid": best_bid,
                        "ask": best_ask,
                        "mid": mid,
                        "event": "book"
                    })

                elif event_type == "price_change":
                    price = float(event.get("price", 0.5))
                    ticks.append({
                        "ts": ts,
                        "source": "poly",
                        "mid": price,
                        "event": "price_change"
                    })

                elif event_type == "last_trade_price":
                    price = float(event.get("price", 0.5))
                    ticks.append({
                        "ts": ts,
                        "source": "poly",
                        "mid": price,
                        "event": "trade"
                    })


async def reporter():
    await asyncio.sleep(2)

    last_binance = None
    last_poly = None

    while True:
        await asyncio.sleep(1)

        # get latest of each
        binance_ticks = [t for t in ticks[-100:] if t["source"] == "binance"]
        poly_ticks = [t for t in ticks[-100:] if t["source"] == "poly"]

        if binance_ticks:
            last_binance = binance_ticks[-1]
        if poly_ticks:
            last_poly = poly_ticks[-1]

        if last_binance and last_poly:
            now = datetime.utcnow().strftime("%H:%M:%S")
            btc_pct = last_binance["pct"]
            poly_mid = last_poly.get("mid", 0.5)

            # count events per second
            one_sec_ago = time.time() - 1
            binance_rate = len([t for t in binance_ticks if t["ts"] > one_sec_ago])
            poly_rate = len([t for t in poly_ticks if t["ts"] > one_sec_ago])

            print(f"{now} | BTC {btc_pct:+.3f}% | UP {poly_mid:.2f} | {binance_rate}/s bin, {poly_rate}/s poly | total {len(ticks)} ticks")


async def main():
    token, slug = await get_active_token()
    if not token:
        print("No active BTC 15m market found")
        return

    print(f"Recording: {slug}")
    print()

    # run for 20 seconds for testing
    try:
        await asyncio.wait_for(
            asyncio.gather(
                binance_stream(),
                poly_stream(token),
                reporter()
            ),
            timeout=20
        )
    except asyncio.TimeoutError:
        pass

    # save data
    filename = f"data/ticks_{int(time.time())}.json"
    with open(filename, "w") as f:
        json.dump(ticks, f)
    print(f"\nSaved {len(ticks)} ticks to {filename}")

    # analyze
    print("\n=== LAG ANALYSIS ===")

    # find significant BTC moves
    binance_ticks = [t for t in ticks if t["source"] == "binance"]
    poly_ticks = [t for t in ticks if t["source"] == "poly"]

    print(f"Binance ticks: {len(binance_ticks)}")
    print(f"Poly ticks: {len(poly_ticks)}")

    # detect BTC moves > 0.02% in 1 second
    moves = []
    for i in range(len(binance_ticks) - 1):
        t1 = binance_ticks[i]
        # find tick ~1 sec later
        for t2 in binance_ticks[i+1:]:
            if t2["ts"] - t1["ts"] > 1:
                break
            delta = abs(t2["pct"] - t1["pct"])
            if delta > 0.02:
                moves.append({
                    "ts": t1["ts"],
                    "delta": t2["pct"] - t1["pct"]
                })
                break

    print(f"\nSignificant BTC moves (>0.02%/sec): {len(moves)}")

    # for each move, find how quickly poly responded
    if moves and poly_ticks:
        lags = []
        for move in moves[:10]:
            # find first poly tick after the move
            poly_after = [t for t in poly_ticks if t["ts"] > move["ts"]]
            if poly_after:
                lag = (poly_after[0]["ts"] - move["ts"]) * 1000  # ms
                lags.append(lag)
                print(f"  BTC {move['delta']:+.3f}% → Poly response in {lag:.0f}ms")

        if lags:
            print(f"\nAverage lag: {sum(lags)/len(lags):.0f}ms")


if __name__ == "__main__":
    asyncio.run(main())
