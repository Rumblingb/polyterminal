"""
zero assumptions analysis
collect all data, find patterns empirically
"""
import asyncio
import json
import re
import aiohttp
from collections import defaultdict

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
BINANCE_API = "https://api.binance.com"


async def get_markets(session, limit=200):
    url = f"{GAMMA_API}/events?tag_id=102467&closed=true&limit={limit}"
    async with session.get(url) as resp:
        data = await resp.json()

    markets = []
    for e in data:
        slug = e.get("slug", "")
        if "btc" not in slug.lower():
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
                    "slug": slug, "winner": winner, "up_token": tokens[0],
                    "start_ts": window_ts, "end_ts": window_ts + 900,
                })
                break
    return markets


async def get_poly_prices(session, token_id, start_ts, end_ts):
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
    url = f"{BINANCE_API}/api/v3/klines"
    params = {"symbol": "BTCUSDT", "interval": "1m",
              "startTime": start_ts * 1000, "endTime": end_ts * 1000, "limit": 20}
    try:
        async with session.get(url, params=params) as resp:
            if resp.status == 200:
                data = await resp.json()
                return {int(k[0]/1000): {"o": float(k[1]), "h": float(k[2]),
                        "l": float(k[3]), "c": float(k[4])} for k in data}
    except:
        pass
    return {}


async def main():
    print("=" * 70)
    print("ZERO ASSUMPTIONS ANALYSIS")
    print("=" * 70)

    async with aiohttp.ClientSession() as session:
        markets = await get_markets(session, 200)
        print(f"Found {len(markets)} markets")

        # collect every data point
        all_points = []

        for i, m in enumerate(markets[:100]):
            poly = await get_poly_prices(session, m["up_token"], m["start_ts"], m["end_ts"])
            btc = await get_btc_klines(session, m["start_ts"], m["end_ts"])

            if not poly or not btc or len(poly) < 5:
                continue

            winner_up = m["winner"] == "Up"
            start_btc = list(btc.values())[0]["o"]

            for p in poly:
                ts = p["t"]
                minute_ts = (ts // 60) * 60

                # find btc data for this minute
                btc_data = btc.get(minute_ts)
                if not btc_data:
                    continue

                time_elapsed = (ts - m["start_ts"]) / 60  # minutes from start
                time_left = (m["end_ts"] - ts) / 60  # minutes to end

                btc_pct = (btc_data["c"] - start_btc) / start_btc * 100
                btc_range = (btc_data["h"] - btc_data["l"]) / start_btc * 100  # 1min volatility

                all_points.append({
                    "time_elapsed": time_elapsed,
                    "time_left": time_left,
                    "up_price": p["p"],
                    "down_price": 1 - p["p"],
                    "btc_pct": btc_pct,
                    "btc_range": btc_range,
                    "winner_up": winner_up,
                })

            if (i + 1) % 25 == 0:
                print(f"Processed {i+1}...")

            await asyncio.sleep(0.05)

        print(f"\nCollected {len(all_points)} data points")
        print()

        # ============================================================
        # EMPIRICAL ANALYSIS - NO ASSUMPTIONS
        # ============================================================

        # 1. RAW CORRELATION: BTC% vs UP_PRICE
        print("=" * 70)
        print("1. BTC % CHANGE vs POLY UP PRICE (correlation)")
        print("=" * 70)

        btc_pcts = [p["btc_pct"] for p in all_points]
        up_prices = [p["up_price"] for p in all_points]

        # bucket by btc_pct
        btc_buckets = defaultdict(list)
        for p in all_points:
            bucket = round(p["btc_pct"] * 10) / 10  # round to 0.1%
            btc_buckets[bucket].append(p["up_price"])

        print(f"{'BTC %':<10} {'Avg UP':<10} {'Count':<10}")
        print("-" * 30)
        for bucket in sorted(btc_buckets.keys()):
            if len(btc_buckets[bucket]) >= 5:
                avg = sum(btc_buckets[bucket]) / len(btc_buckets[bucket])
                print(f"{bucket:+.1f}%      {avg:.2f}       {len(btc_buckets[bucket])}")
        print()

        # 2. BTC% vs OUTCOME (does btc direction predict winner?)
        print("=" * 70)
        print("2. BTC % CHANGE vs WINNER (by time bucket)")
        print("=" * 70)

        time_buckets = [(0, 5), (5, 10), (10, 15)]
        for t_min, t_max in time_buckets:
            points = [p for p in all_points if t_min <= p["time_elapsed"] < t_max]
            if not points:
                continue

            # group by btc direction
            btc_up = [p for p in points if p["btc_pct"] > 0]
            btc_down = [p for p in points if p["btc_pct"] <= 0]

            btc_up_correct = sum(1 for p in btc_up if p["winner_up"]) / len(btc_up) * 100 if btc_up else 0
            btc_down_correct = sum(1 for p in btc_down if not p["winner_up"]) / len(btc_down) * 100 if btc_down else 0

            print(f"Time {t_min}-{t_max}m:")
            print(f"   BTC up -> UP wins: {btc_up_correct:.0f}% (n={len(btc_up)})")
            print(f"   BTC down -> DOWN wins: {btc_down_correct:.0f}% (n={len(btc_down)})")
        print()

        # 3. UP PRICE vs OUTCOME (is the poly price well-calibrated?)
        print("=" * 70)
        print("3. POLY PRICE CALIBRATION (does 0.70 = 70% win rate?)")
        print("=" * 70)

        price_buckets = defaultdict(list)
        for p in all_points:
            bucket = round(p["up_price"] * 10) / 10
            price_buckets[bucket].append(p["winner_up"])

        print(f"{'UP Price':<12} {'Actual Win%':<12} {'Expected':<12} {'Edge':<10} {'N':<6}")
        print("-" * 55)
        for bucket in sorted(price_buckets.keys()):
            outcomes = price_buckets[bucket]
            if len(outcomes) >= 10:
                actual = sum(outcomes) / len(outcomes) * 100
                expected = bucket * 100
                edge = actual - expected
                print(f"{bucket:.1f}          {actual:.0f}%          {expected:.0f}%          {edge:+.0f}%       {len(outcomes)}")
        print()

        # 4. TIME vs CALIBRATION (does edge change over time?)
        print("=" * 70)
        print("4. EDGE BY TIME (UP price 0.6-0.7 bucket)")
        print("=" * 70)

        for t_min, t_max in [(0, 3), (3, 6), (6, 9), (9, 12), (12, 15)]:
            points = [p for p in all_points
                     if t_min <= p["time_elapsed"] < t_max and 0.55 <= p["up_price"] <= 0.75]
            if len(points) >= 10:
                actual = sum(p["winner_up"] for p in points) / len(points) * 100
                avg_price = sum(p["up_price"] for p in points) / len(points)
                expected = avg_price * 100
                edge = actual - expected
                print(f"Time {t_min:2d}-{t_max:2d}m: actual={actual:.0f}%, expected={expected:.0f}%, edge={edge:+.0f}% (n={len(points)})")
        print()

        # 5. BTC VOLATILITY vs OUTCOME
        print("=" * 70)
        print("5. BTC 1-MIN RANGE vs OUTCOME PREDICTABILITY")
        print("=" * 70)

        low_vol = [p for p in all_points if p["btc_range"] < 0.02]
        high_vol = [p for p in all_points if p["btc_range"] >= 0.02]

        for label, points in [("Low vol (<0.02%)", low_vol), ("High vol (>=0.02%)", high_vol)]:
            if not points:
                continue
            btc_correct = sum(1 for p in points if (p["btc_pct"] > 0) == p["winner_up"])
            accuracy = btc_correct / len(points) * 100
            print(f"{label}: BTC predicts outcome {accuracy:.0f}% (n={len(points)})")
        print()

        # 6. OPTIMAL ENTRY GRID
        print("=" * 70)
        print("6. OPTIMAL ENTRY: P&L BY (TIME, PRICE) GRID")
        print("   Entry: buy UP when up_price >= threshold")
        print("   3c slippage included")
        print("=" * 70)

        SLIP = 0.03
        grid = {}

        time_ranges = [(0, 5, "0-5m"), (5, 9, "5-9m"), (9, 12, "9-12m"), (12, 15, "12-15m")]
        price_ranges = [(0.55, 0.65, "0.55-0.65"), (0.65, 0.75, "0.65-0.75"),
                       (0.75, 0.85, "0.75-0.85"), (0.85, 0.95, "0.85-0.95")]

        print(f"\n{'Time':<10}", end="")
        for _, _, p_label in price_ranges:
            print(f"{p_label:<12}", end="")
        print()
        print("-" * 60)

        for t_min, t_max, t_label in time_ranges:
            print(f"{t_label:<10}", end="")
            for p_min, p_max, p_label in price_ranges:
                points = [p for p in all_points
                         if t_min <= p["time_elapsed"] < t_max
                         and p_min <= p["up_price"] < p_max]

                if len(points) >= 5:
                    # simulate: buy UP at avg price + slippage
                    avg_entry = sum(p["up_price"] for p in points) / len(points) + SLIP
                    wins = sum(1 for p in points if p["winner_up"])
                    losses = len(points) - wins
                    pnl = wins * (1 - avg_entry) - losses * avg_entry
                    win_pct = wins / len(points) * 100
                    print(f"${pnl:+.1f}({win_pct:.0f}%) ", end="")
                else:
                    print(f"{'n/a':<12}", end="")
            print()
        print()

        # 7. FIND THE ACTUAL EDGE
        print("=" * 70)
        print("7. WHERE IS THE EDGE? (sorted by P&L)")
        print("=" * 70)

        combos = []
        for t_min in range(0, 14, 2):
            t_max = t_min + 3
            for p_thresh in [0.55, 0.60, 0.65, 0.70, 0.75, 0.80]:
                points = [p for p in all_points
                         if t_min <= p["time_elapsed"] < t_max
                         and p["up_price"] >= p_thresh]

                if len(points) >= 10:
                    entry = p_thresh + SLIP
                    pnl = sum((1 - entry) if p["winner_up"] else -entry for p in points)
                    wins = sum(1 for p in points if p["winner_up"])
                    combos.append({
                        "time": f"{t_min}-{t_max}m",
                        "thresh": p_thresh,
                        "n": len(points),
                        "wins": wins,
                        "pnl": pnl,
                        "win_pct": wins / len(points) * 100,
                        "roi": pnl / len(points) * 100,
                    })

        combos.sort(key=lambda x: x["pnl"], reverse=True)

        print(f"{'Time':<10} {'Thresh':<8} {'N':<6} {'Win%':<8} {'P&L':<10} {'ROI':<8}")
        print("-" * 55)
        for c in combos[:15]:
            print(f"{c['time']:<10} {c['thresh']:.2f}     {c['n']:<6} {c['win_pct']:.0f}%     ${c['pnl']:+.2f}     {c['roi']:+.1f}%")

        # save raw data
        with open("data/zero_assumptions.json", "w") as f:
            json.dump(all_points[:1000], f)  # sample
        print(f"\nSaved sample to data/zero_assumptions.json")


if __name__ == "__main__":
    asyncio.run(main())
