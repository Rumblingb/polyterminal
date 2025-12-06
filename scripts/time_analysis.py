"""
analyze price thresholds at different times in window
"""
import asyncio
import json
import re
import aiohttp

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"


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
                    "slug": slug,
                    "winner": winner,
                    "up_token": tokens[0],
                    "start_ts": window_ts,
                    "end_ts": window_ts + 900,
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


async def main():
    print("=" * 70)
    print("TIME + PRICE ANALYSIS")
    print("=" * 70)

    async with aiohttp.ClientSession() as session:
        markets = await get_markets(session, 200)
        print(f"Found {len(markets)} BTC markets")

        all_points = []  # (time_left_mins, price, winner_is_up)

        for i, m in enumerate(markets[:100]):
            poly = await get_poly_prices(session, m["up_token"], m["start_ts"], m["end_ts"])

            if not poly or len(poly) < 5:
                continue

            winner_is_up = m["winner"] == "Up"

            for p in poly:
                time_left = (m["end_ts"] - p["t"]) / 60  # minutes
                all_points.append({
                    "time_left": time_left,
                    "up_price": p["p"],
                    "winner_is_up": winner_is_up,
                })

            if (i + 1) % 20 == 0:
                print(f"Processed {i + 1}...")

            await asyncio.sleep(0.05)

        print(f"\nCollected {len(all_points)} price points")
        print()

        # analyze by time bucket + price threshold
        print("=" * 70)
        print("WIN RATE BY TIME LEFT + PRICE THRESHOLD")
        print("(buy UP when price >= threshold)")
        print("=" * 70)
        print()

        time_buckets = [
            (12, 15, "12-15m (early)"),
            (9, 12, "9-12m"),
            (6, 9, "6-9m"),
            (3, 6, "3-6m"),
            (1, 3, "1-3m (late)"),
        ]

        price_thresholds = [0.60, 0.70, 0.80, 0.85, 0.90, 0.95]

        print(f"{'Time Left':<15}", end="")
        for thresh in price_thresholds:
            print(f"{thresh:.2f}    ", end="")
        print()
        print("-" * 70)

        for t_min, t_max, label in time_buckets:
            print(f"{label:<15}", end="")

            for thresh in price_thresholds:
                # find points in this time bucket where UP >= threshold
                matches = [p for p in all_points
                          if t_min <= p["time_left"] < t_max and p["up_price"] >= thresh]

                if matches:
                    wins = sum(1 for p in matches if p["winner_is_up"])
                    pct = 100 * wins / len(matches)
                    print(f"{pct:3.0f}%({len(matches):2d}) ", end="")
                else:
                    print(f"  -     ", end="")
            print()

        print()
        print("=" * 70)
        print("P&L BY TIME LEFT + PRICE THRESHOLD (3c slippage)")
        print("=" * 70)
        print()

        print(f"{'Time Left':<15}", end="")
        for thresh in price_thresholds:
            print(f"{thresh:.2f}    ", end="")
        print()
        print("-" * 70)

        for t_min, t_max, label in time_buckets:
            print(f"{label:<15}", end="")

            for thresh in price_thresholds:
                matches = [p for p in all_points
                          if t_min <= p["time_left"] < t_max and p["up_price"] >= thresh]

                if matches:
                    entry = thresh + 0.03  # slippage
                    total_pnl = 0
                    for p in matches:
                        if p["winner_is_up"]:
                            total_pnl += (1.0 - entry)
                        else:
                            total_pnl += -entry
                    print(f"${total_pnl:+5.1f}   ", end="")
                else:
                    print(f"   -    ", end="")
            print()

        print()

        # find optimal combination
        print("=" * 70)
        print("OPTIMAL COMBINATIONS (sorted by P&L)")
        print("=" * 70)
        print()

        combos = []
        for t_min, t_max, label in time_buckets:
            for thresh in price_thresholds:
                matches = [p for p in all_points
                          if t_min <= p["time_left"] < t_max and p["up_price"] >= thresh]

                if len(matches) >= 5:  # minimum sample
                    entry = thresh + 0.03
                    wins = sum(1 for p in matches if p["winner_is_up"])
                    total_pnl = sum((1.0 - entry) if p["winner_is_up"] else -entry for p in matches)
                    combos.append({
                        "time": label,
                        "thresh": thresh,
                        "n": len(matches),
                        "wins": wins,
                        "pnl": total_pnl,
                        "win_pct": 100 * wins / len(matches),
                    })

        combos.sort(key=lambda x: x["pnl"], reverse=True)

        print(f"{'Time':<15} {'Thresh':<8} {'Trades':<8} {'Win%':<8} {'P&L':<8}")
        print("-" * 50)
        for c in combos[:15]:
            print(f"{c['time']:<15} {c['thresh']:.2f}     {c['n']:<8} {c['win_pct']:.0f}%     ${c['pnl']:+.2f}")


if __name__ == "__main__":
    asyncio.run(main())
