"""
real analysis on windows with actual trading activity
"""
import asyncio
import json
import re
import aiohttp

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


async def get_btc_klines(session, start_ts, end_ts):
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
                return [{"t": int(k[0] / 1000), "o": float(k[1]), "c": float(k[4])} for k in data]
    except:
        pass
    return []


async def main():
    print("=" * 70)
    print("REAL ANALYSIS: BTC vs POLY CORRELATION")
    print("=" * 70)

    async with aiohttp.ClientSession() as session:
        markets = await get_markets(session, 200)
        print(f"Found {len(markets)} BTC markets")

        results = []

        for i, m in enumerate(markets[:100]):
            poly = await get_poly_prices(session, m["up_token"], m["start_ts"], m["end_ts"])
            btc = await get_btc_klines(session, m["start_ts"], m["end_ts"])

            if not poly or not btc or len(poly) < 5:
                continue

            poly_prices = [p["p"] for p in poly]
            poly_range = max(poly_prices) - min(poly_prices)

            # skip dead markets
            if poly_range < 0.10:
                continue

            start_btc = btc[0]["o"]
            end_btc = btc[-1]["c"]
            btc_pct = (end_btc - start_btc) / start_btc * 100

            results.append({
                "slug": m["slug"],
                "winner": m["winner"],
                "winner_is_up": m["winner"] == "Up",
                "btc_pct": btc_pct,
                "poly_first": poly_prices[0],
                "poly_last": poly_prices[-1],
                "poly_min": min(poly_prices),
                "poly_max": max(poly_prices),
                "poly_range": poly_range,
            })

            if (i + 1) % 20 == 0:
                print(f"Processed {i + 1}...")

            await asyncio.sleep(0.05)

        print(f"\nAnalyzed {len(results)} active windows")
        print()

        # 1. BTC direction accuracy
        print("=" * 70)
        print("1. BTC DIRECTION PREDICTS OUTCOME")
        print("=" * 70)
        correct = sum(1 for r in results if (r["btc_pct"] > 0) == r["winner_is_up"])
        print(f"   Accuracy: {correct}/{len(results)} ({100*correct/len(results):.1f}%)")
        print()

        # breakdown by magnitude
        small = [r for r in results if abs(r["btc_pct"]) < 0.05]
        medium = [r for r in results if 0.05 <= abs(r["btc_pct"]) < 0.10]
        large = [r for r in results if abs(r["btc_pct"]) >= 0.10]

        if small:
            c = sum(1 for r in small if (r["btc_pct"] > 0) == r["winner_is_up"])
            print(f"   |BTC| < 0.05%: {c}/{len(small)} ({100*c/len(small):.0f}%)")
        if medium:
            c = sum(1 for r in medium if (r["btc_pct"] > 0) == r["winner_is_up"])
            print(f"   0.05% <= |BTC| < 0.10%: {c}/{len(medium)} ({100*c/len(medium):.0f}%)")
        if large:
            c = sum(1 for r in large if (r["btc_pct"] > 0) == r["winner_is_up"])
            print(f"   |BTC| >= 0.10%: {c}/{len(large)} ({100*c/len(large):.0f}%)")
        print()

        # 2. strategy backtest: buy side with higher poly price late in window
        print("=" * 70)
        print("2. STRATEGY: BUY WINNING SIDE AT DIFFERENT THRESHOLDS")
        print("=" * 70)

        for threshold in [0.60, 0.70, 0.80, 0.85, 0.90]:
            trades = []
            for r in results:
                # check if poly_max hit threshold (someone was winning)
                if r["poly_max"] >= threshold:
                    # buy UP at threshold
                    entry = threshold + 0.03  # slippage
                    won = r["winner_is_up"]
                    pnl = (1.0 - entry) if won else -entry
                    trades.append({"pnl": pnl, "won": won})

                if r["poly_min"] <= (1 - threshold):
                    # buy DOWN at threshold (1 - min = down price)
                    entry = threshold + 0.03
                    won = not r["winner_is_up"]
                    pnl = (1.0 - entry) if won else -entry
                    trades.append({"pnl": pnl, "won": won})

            if trades:
                wins = sum(1 for t in trades if t["won"])
                total_pnl = sum(t["pnl"] for t in trades)
                print(f"   Threshold {threshold:.2f}: {len(trades)} trades, {wins}/{len(trades)} wins ({100*wins/len(trades):.0f}%), P&L: ${total_pnl:.2f}")
        print()

        # 3. check for mispricing patterns
        print("=" * 70)
        print("3. EARLY SIGNAL: POLY PRICE AT START vs OUTCOME")
        print("=" * 70)

        # if poly starts > 0.55, does UP win more?
        for start_thresh in [0.45, 0.50, 0.55, 0.60]:
            above = [r for r in results if r["poly_first"] > start_thresh]
            below = [r for r in results if r["poly_first"] <= start_thresh]

            if above:
                up_wins = sum(1 for r in above if r["winner_is_up"])
                print(f"   Poly start > {start_thresh:.2f}: UP wins {up_wins}/{len(above)} ({100*up_wins/len(above):.0f}%)")
        print()

        # 4. mean reversion
        print("=" * 70)
        print("4. MEAN REVERSION: FADE EXTREMES")
        print("=" * 70)

        fade_trades = []
        for r in results:
            # if poly hit 0.80+, fade by buying DOWN
            if r["poly_max"] >= 0.80:
                entry = 1 - r["poly_max"] + 0.03  # buy DOWN when UP is at max
                won = not r["winner_is_up"]
                pnl = (1.0 - entry) if won else -entry
                fade_trades.append({"side": "fade UP", "pnl": pnl, "won": won, "entry": entry})

            # if poly hit 0.20-, fade by buying UP
            if r["poly_min"] <= 0.20:
                entry = r["poly_min"] + 0.03  # buy UP when cheap
                won = r["winner_is_up"]
                pnl = (1.0 - entry) if won else -entry
                fade_trades.append({"side": "fade DOWN", "pnl": pnl, "won": won, "entry": entry})

        if fade_trades:
            wins = sum(1 for t in fade_trades if t["won"])
            total_pnl = sum(t["pnl"] for t in fade_trades)
            print(f"   Fade extremes: {len(fade_trades)} trades")
            print(f"   Win rate: {wins}/{len(fade_trades)} ({100*wins/len(fade_trades):.0f}%)")
            print(f"   Total P&L: ${total_pnl:.2f}")
            print(f"   Avg entry: {sum(t['entry'] for t in fade_trades)/len(fade_trades):.2f}")

        # save results
        with open("data/real_analysis.json", "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nSaved {len(results)} results to data/real_analysis.json")


if __name__ == "__main__":
    asyncio.run(main())
