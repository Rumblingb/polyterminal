"""
collect complete data for backtesting:
- polymarket UP price over time
- binance BTC price over time (aligned)
- outcome (who won)
"""
import asyncio
import json
import re
from datetime import datetime
import aiohttp

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
BINANCE_API = "https://api.binance.com"


async def get_resolved_markets(session, coin="btc", limit=100):
    url = f"{GAMMA_API}/events?tag_id=102467&closed=true&limit={limit}"
    async with session.get(url) as resp:
        data = await resp.json()

    markets = []
    for e in data:
        slug = e.get("slug", "")
        if coin not in slug.lower():
            continue

        match = re.search(r'15m-(\d+)', slug)
        if not match:
            continue

        window_ts = int(match.group(1))

        for m in e.get("markets", []):
            outcomes = m.get("outcomes", "[]")
            prices = m.get("outcomePrices", "[]")
            if isinstance(outcomes, str):
                outcomes = json.loads(outcomes)
            if isinstance(prices, str):
                prices = json.loads(prices)

            winner = None
            if prices:
                for i, p in enumerate(prices):
                    if float(p) > 0.99:
                        winner = outcomes[i] if i < len(outcomes) else None

            tokens = m.get("clobTokenIds", "[]")
            if isinstance(tokens, str):
                tokens = json.loads(tokens)

            if tokens and winner:
                markets.append({
                    "slug": slug,
                    "winner": winner,
                    "up_token": tokens[0],
                    "start_ts": window_ts,
                    "end_ts": window_ts + 900,
                })

    seen = set()
    unique = []
    for m in markets:
        if m["up_token"] not in seen:
            seen.add(m["up_token"])
            unique.append(m)
    return unique


async def get_poly_prices(session, token_id, start_ts, end_ts):
    """get polymarket price history"""
    url = f"{CLOB_API}/prices-history?market={token_id}&startTs={start_ts}&endTs={end_ts}&fidelity=1"
    try:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("history", [])
    except:
        pass
    return []


async def get_btc_klines(session, start_ts, end_ts):
    """get binance 1-second klines (approximated with 1m klines)"""
    # binance klines: 1m is smallest public interval
    url = f"{BINANCE_API}/api/v3/klines"
    params = {
        "symbol": "BTCUSDT",
        "interval": "1m",
        "startTime": start_ts * 1000,
        "endTime": end_ts * 1000,
        "limit": 20,
    }
    try:
        async with session.get(url, params=params) as resp:
            if resp.status == 200:
                data = await resp.json()
                # kline: [open_time, open, high, low, close, volume, ...]
                return [{"t": int(k[0] / 1000), "o": float(k[1]), "c": float(k[4])} for k in data]
    except:
        pass
    return []


async def collect_window_data(session, market):
    """collect complete data for one window"""
    poly = await get_poly_prices(session, market["up_token"], market["start_ts"], market["end_ts"])
    btc = await get_btc_klines(session, market["start_ts"], market["end_ts"])

    if not poly or not btc:
        return None

    # start price = first BTC candle open
    start_btc = btc[0]["o"]
    end_btc = btc[-1]["c"]
    btc_change_pct = (end_btc - start_btc) / start_btc * 100

    # align poly prices with BTC by minute
    poly_by_minute = {}
    for p in poly:
        minute = p["t"] // 60 * 60
        poly_by_minute[minute] = p["p"]

    # combine
    combined = []
    for b in btc:
        minute = b["t"]
        poly_price = poly_by_minute.get(minute)
        if poly_price is not None:
            btc_pct = (b["c"] - start_btc) / start_btc * 100
            combined.append({
                "t": minute,
                "btc": b["c"],
                "btc_pct": btc_pct,
                "up_price": poly_price,
            })

    return {
        "slug": market["slug"],
        "start_ts": market["start_ts"],
        "start_btc": start_btc,
        "end_btc": end_btc,
        "btc_change_pct": btc_change_pct,
        "winner": market["winner"],
        "winner_is_up": market["winner"] == "Up",
        "points": combined,
        "poly_first": poly[0]["p"] if poly else None,
        "poly_last": poly[-1]["p"] if poly else None,
    }


async def main():
    print("=" * 70)
    print("COLLECTING COMPLETE DATA (Poly + BTC aligned)")
    print("=" * 70)

    async with aiohttp.ClientSession() as session:
        markets = await get_resolved_markets(session, "btc", 100)
        print(f"Found {len(markets)} resolved BTC markets")

        all_data = []
        for i, m in enumerate(markets[:50]):
            data = await collect_window_data(session, m)
            if data and len(data["points"]) >= 5:
                all_data.append(data)

            if (i + 1) % 10 == 0:
                print(f"Processed {i + 1}...")

            await asyncio.sleep(0.1)

        print(f"\nCollected {len(all_data)} complete windows")
        print()

        # analysis with complete data
        print("=" * 70)
        print("ANALYSIS WITH COMPLETE DATA")
        print("=" * 70)

        # 1. does BTC direction predict outcome?
        print("\n1. BTC DIRECTION vs OUTCOME:")
        btc_up_correct = 0
        btc_down_correct = 0
        for d in all_data:
            btc_went_up = d["btc_change_pct"] > 0
            if btc_went_up and d["winner_is_up"]:
                btc_up_correct += 1
            elif not btc_went_up and not d["winner_is_up"]:
                btc_down_correct += 1

        btc_up = [d for d in all_data if d["btc_change_pct"] > 0]
        btc_down = [d for d in all_data if d["btc_change_pct"] <= 0]
        print(f"   BTC up ({len(btc_up)}): UP wins {btc_up_correct}/{len(btc_up)} ({100*btc_up_correct/len(btc_up):.0f}%)" if btc_up else "   No BTC up")
        print(f"   BTC down ({len(btc_down)}): DOWN wins {btc_down_correct}/{len(btc_down)} ({100*btc_down_correct/len(btc_down):.0f}%)" if btc_down else "   No BTC down")

        # 2. poly price vs btc change correlation
        print("\n2. POLY PRICE vs BTC % CHANGE:")
        for d in all_data[:5]:
            print(f"   BTC: {d['btc_change_pct']:+.3f}% | UP price: {d['poly_first']:.2f}->{d['poly_last']:.2f} | Winner: {d['winner']}")

        # 3. mispricing analysis
        print("\n3. MISPRICING ANALYSIS:")
        mispricings = []
        for d in all_data:
            for p in d["points"]:
                # theoretical fair value based on BTC position
                # simplified: if btc +0.05%, UP should be ~0.60
                btc_pct = p["btc_pct"]
                up_price = p["up_price"]

                # rough fair value model: 0.50 + btc_pct * 5
                fair = 0.50 + btc_pct * 5
                fair = max(0.05, min(0.95, fair))

                mispricing = up_price - fair
                mispricings.append({
                    "btc_pct": btc_pct,
                    "up_price": up_price,
                    "fair": fair,
                    "mispricing": mispricing,
                })

        if mispricings:
            avg_mispricing = sum(m["mispricing"] for m in mispricings) / len(mispricings)
            print(f"   Avg mispricing: {avg_mispricing:+.3f}")
            print(f"   (positive = UP overpriced, negative = UP underpriced)")

        # 4. save data for further analysis
        with open("data/complete_btc_data.json", "w") as f:
            json.dump(all_data, f, indent=2)
        print(f"\nSaved to data/complete_btc_data.json")


if __name__ == "__main__":
    asyncio.run(main())
