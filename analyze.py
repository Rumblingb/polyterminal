"""
quant analysis - find actual edges in the data
"""
import asyncio
import json
import re
from datetime import datetime
from collections import defaultdict
import aiohttp

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"


async def get_resolved_markets(session, coin="btc", limit=200):
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
                    "down_token": tokens[1] if len(tokens) > 1 else None,
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


async def get_price_history(session, token_id, start_ts, end_ts):
    url = f"{CLOB_API}/prices-history?market={token_id}&startTs={start_ts}&endTs={end_ts}&fidelity=1"
    try:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("history", [])
    except:
        pass
    return []


def analyze_price_path(history, window_end_ts, winner_is_up):
    """analyze full price path, not just late window"""
    if len(history) < 10:
        return None

    prices = [h["p"] for h in history]
    times = [h["t"] for h in history]

    # basic stats
    first_price = prices[0]
    last_price = prices[-1]
    max_price = max(prices)
    min_price = min(prices)

    # time to reach extremes
    max_idx = prices.index(max_price)
    min_idx = prices.index(min_price)

    time_at_max = (window_end_ts - times[max_idx]) / 60  # mins left
    time_at_min = (window_end_ts - times[min_idx]) / 60

    # volatility
    returns = [(prices[i] - prices[i-1]) for i in range(1, len(prices))]
    volatility = sum(abs(r) for r in returns) / len(returns) if returns else 0

    # trend
    mid_idx = len(prices) // 2
    first_half_avg = sum(prices[:mid_idx]) / mid_idx if mid_idx > 0 else 0
    second_half_avg = sum(prices[mid_idx:]) / (len(prices) - mid_idx) if len(prices) > mid_idx else 0
    trend = second_half_avg - first_half_avg

    # reversals (price crosses 0.5)
    crosses = 0
    for i in range(1, len(prices)):
        if (prices[i-1] < 0.5 and prices[i] >= 0.5) or (prices[i-1] >= 0.5 and prices[i] < 0.5):
            crosses += 1

    return {
        "first": first_price,
        "last": last_price,
        "max": max_price,
        "min": min_price,
        "range": max_price - min_price,
        "time_at_max": time_at_max,
        "time_at_min": time_at_min,
        "volatility": volatility,
        "trend": trend,
        "crosses": crosses,
        "n_points": len(prices),
        "winner_is_up": winner_is_up,
    }


async def main():
    print("=" * 70)
    print("QUANTITATIVE ANALYSIS")
    print("=" * 70)

    async with aiohttp.ClientSession() as session:
        print("\nFetching BTC markets...")
        markets = await get_resolved_markets(session, "btc", 200)
        print(f"Found {len(markets)} markets")

        all_stats = []

        for i, m in enumerate(markets[:100]):
            history = await get_price_history(session, m["up_token"], m["start_ts"], m["end_ts"])

            if history:
                stats = analyze_price_path(history, m["end_ts"], m["winner"] == "Up")
                if stats:
                    stats["slug"] = m["slug"]
                    all_stats.append(stats)

            if (i + 1) % 20 == 0:
                print(f"Processed {i + 1}...")

            await asyncio.sleep(0.05)

        print(f"\nAnalyzed {len(all_stats)} markets with price data")
        print()

        # 1. price range distribution
        print("=" * 70)
        print("1. PRICE RANGE (max - min within window)")
        print("=" * 70)
        ranges = [s["range"] for s in all_stats]
        ranges.sort()
        print(f"   Min: {min(ranges):.2f}")
        print(f"   25%: {ranges[len(ranges)//4]:.2f}")
        print(f"   50%: {ranges[len(ranges)//2]:.2f}")
        print(f"   75%: {ranges[3*len(ranges)//4]:.2f}")
        print(f"   Max: {max(ranges):.2f}")
        print()

        # 2. when does max/min occur?
        print("=" * 70)
        print("2. WHEN DOES EXTREME PRICE OCCUR? (mins before end)")
        print("=" * 70)
        up_wins = [s for s in all_stats if s["winner_is_up"]]
        down_wins = [s for s in all_stats if not s["winner_is_up"]]

        print(f"   UP wins ({len(up_wins)}): max occurs at avg {sum(s['time_at_max'] for s in up_wins)/len(up_wins):.1f}m before end")
        print(f"   DOWN wins ({len(down_wins)}): min occurs at avg {sum(s['time_at_min'] for s in down_wins)/len(down_wins):.1f}m before end")
        print()

        # 3. reversals
        print("=" * 70)
        print("3. REVERSALS (crosses 0.50 threshold)")
        print("=" * 70)
        crosses = [s["crosses"] for s in all_stats]
        print(f"   0 crosses: {sum(1 for c in crosses if c == 0)} markets")
        print(f"   1 cross:   {sum(1 for c in crosses if c == 1)} markets")
        print(f"   2 crosses: {sum(1 for c in crosses if c == 2)} markets")
        print(f"   3+ crosses:{sum(1 for c in crosses if c >= 3)} markets")
        print()

        # 4. predictive signals
        print("=" * 70)
        print("4. EARLY PRICE AS PREDICTOR")
        print("=" * 70)

        # if UP > 0.6 at any point, what's win rate?
        thresholds = [0.55, 0.60, 0.65, 0.70, 0.75, 0.80]
        for thresh in thresholds:
            hit_thresh = [s for s in all_stats if s["max"] >= thresh]
            if hit_thresh:
                correct = sum(1 for s in hit_thresh if s["winner_is_up"])
                print(f"   UP hits {thresh:.2f}: {len(hit_thresh)} times, UP wins {correct}/{len(hit_thresh)} ({100*correct/len(hit_thresh):.0f}%)")
        print()

        # 5. mean reversion analysis
        print("=" * 70)
        print("5. MEAN REVERSION: extreme -> reversal?")
        print("=" * 70)

        # count markets where price hit extreme then reversed
        reversions = 0
        for s in all_stats:
            if s["max"] >= 0.75 and s["last"] < s["max"] - 0.10:
                reversions += 1
            if s["min"] <= 0.25 and s["last"] > s["min"] + 0.10:
                reversions += 1
        print(f"   Hit 0.75+ then dropped 10c+: {sum(1 for s in all_stats if s['max'] >= 0.75 and s['last'] < s['max'] - 0.10)}")
        print(f"   Hit 0.25- then rose 10c+:   {sum(1 for s in all_stats if s['min'] <= 0.25 and s['last'] > s['min'] + 0.10)}")
        print()

        # 6. volatility analysis
        print("=" * 70)
        print("6. VOLATILITY DISTRIBUTION")
        print("=" * 70)
        vols = sorted([s["volatility"] for s in all_stats])
        print(f"   Min: {min(vols):.4f}")
        print(f"   25%: {vols[len(vols)//4]:.4f}")
        print(f"   50%: {vols[len(vols)//2]:.4f}")
        print(f"   75%: {vols[3*len(vols)//4]:.4f}")
        print(f"   Max: {max(vols):.4f}")
        print()

        # 7. strategy ideas from data
        print("=" * 70)
        print("7. STRATEGY BACKTESTS")
        print("=" * 70)

        # Strategy A: buy UP when it first hits 0.60, sell at 0.70 or hold to end
        print("\n   A) Buy UP at 0.60, target 0.70:")
        # Strategy B: fade extremes - buy DOWN when UP > 0.75
        print("\n   B) Fade extreme: buy DOWN when UP hits 0.75:")
        fade_trades = []
        for s in all_stats:
            if s["max"] >= 0.75:
                # bought DOWN at (1 - 0.75) = 0.25
                entry = 1 - s["max"]
                # exit at end: DOWN price = 1 - last_UP
                exit_price = 1 - s["last"]
                pnl = exit_price - entry if not s["winner_is_up"] else -entry
                fade_trades.append({"entry": entry, "pnl": pnl, "won": not s["winner_is_up"]})

        if fade_trades:
            wins = sum(1 for t in fade_trades if t["won"])
            total_pnl = sum(t["pnl"] for t in fade_trades)
            print(f"      Trades: {len(fade_trades)}")
            print(f"      Win rate: {wins}/{len(fade_trades)} ({100*wins/len(fade_trades):.0f}%)")
            print(f"      Total P&L: ${total_pnl:.2f}")
            print(f"      Avg entry: {sum(t['entry'] for t in fade_trades)/len(fade_trades):.2f}")

        # Strategy C: momentum - buy UP when it crosses 0.55 from below
        print("\n   C) Momentum: buy winning side at 0.55:")
        # Strategy D: late confirmation at 0.70 with 5min left
        print("\n   D) Late confirmation at 0.70 (vs 0.85):")


if __name__ == "__main__":
    asyncio.run(main())
