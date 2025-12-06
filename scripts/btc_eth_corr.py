"""
compare btc 15m vs eth 15m poly prices - find correlation/lag
"""
import asyncio
import json
import re
import aiohttp

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"


async def get_paired_markets(session, limit=100):
    """get btc and eth markets for same windows"""
    url = f"{GAMMA_API}/events?tag_id=102467&closed=true&limit={limit}"
    async with session.get(url) as resp:
        data = await resp.json()

    # group by window timestamp
    windows = {}
    for e in data:
        slug = e.get("slug", "")
        match = re.search(r'(btc|eth)-up(?:down|-or-down)-15m-(\d+)', slug)
        if not match:
            continue

        coin = match.group(1)
        window_ts = int(match.group(2))

        for m in e.get("markets", []):
            tokens = m.get("clobTokenIds", "[]")
            if isinstance(tokens, str):
                tokens = json.loads(tokens)
            if tokens:
                if window_ts not in windows:
                    windows[window_ts] = {}
                windows[window_ts][coin] = {
                    "token": tokens[0],
                    "slug": slug
                }
                break

    # return only windows with both btc and eth
    paired = []
    for ts, coins in windows.items():
        if "btc" in coins and "eth" in coins:
            paired.append({
                "ts": ts,
                "btc_token": coins["btc"]["token"],
                "eth_token": coins["eth"]["token"]
            })

    return sorted(paired, key=lambda x: x["ts"], reverse=True)


async def get_prices(session, token_id, start_ts, end_ts):
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
    print("BTC vs ETH 15m POLY PRICE CORRELATION")
    print("=" * 70)

    async with aiohttp.ClientSession() as session:
        pairs = await get_paired_markets(session, 200)
        print(f"Found {len(pairs)} paired windows")

        all_diffs = []
        corr_data = []

        for i, p in enumerate(pairs[:50]):
            btc_hist = await get_prices(session, p["btc_token"], p["ts"], p["ts"] + 900)
            eth_hist = await get_prices(session, p["eth_token"], p["ts"], p["ts"] + 900)

            if not btc_hist or not eth_hist or len(btc_hist) < 5 or len(eth_hist) < 5:
                continue

            # align by timestamp
            btc_prices = {h["t"]: h["p"] for h in btc_hist}
            eth_prices = {h["t"]: h["p"] for h in eth_hist}

            common_ts = set(btc_prices.keys()) & set(eth_prices.keys())
            if len(common_ts) < 5:
                continue

            for ts in sorted(common_ts):
                diff = btc_prices[ts] - eth_prices[ts]
                time_in_window = (ts - p["ts"]) / 60
                all_diffs.append({
                    "time": time_in_window,
                    "btc": btc_prices[ts],
                    "eth": eth_prices[ts],
                    "diff": diff
                })
                corr_data.append((btc_prices[ts], eth_prices[ts]))

            if (i + 1) % 10 == 0:
                print(f"Processed {i+1}...")

            await asyncio.sleep(0.05)

        print(f"\nCollected {len(all_diffs)} paired price points")
        print()

        # correlation
        if corr_data:
            btc_vals = [x[0] for x in corr_data]
            eth_vals = [x[1] for x in corr_data]

            n = len(corr_data)
            mean_btc = sum(btc_vals) / n
            mean_eth = sum(eth_vals) / n

            cov = sum((b - mean_btc) * (e - mean_eth) for b, e in corr_data) / n
            std_btc = (sum((b - mean_btc)**2 for b in btc_vals) / n) ** 0.5
            std_eth = (sum((e - mean_eth)**2 for e in eth_vals) / n) ** 0.5

            corr = cov / (std_btc * std_eth) if std_btc and std_eth else 0

            print("=" * 70)
            print("CORRELATION ANALYSIS")
            print("=" * 70)
            print(f"Correlation: {corr:.3f}")
            print(f"Mean BTC UP: {mean_btc:.3f}")
            print(f"Mean ETH UP: {mean_eth:.3f}")
            print()

        # price difference analysis
        print("=" * 70)
        print("PRICE DIFFERENCE (BTC - ETH)")
        print("=" * 70)

        diffs = [d["diff"] for d in all_diffs]
        avg_diff = sum(diffs) / len(diffs)
        abs_diffs = [abs(d) for d in diffs]
        avg_abs_diff = sum(abs_diffs) / len(abs_diffs)

        print(f"Avg difference: {avg_diff:+.3f}")
        print(f"Avg absolute diff: {avg_abs_diff:.3f}")
        print()

        # by time bucket
        print("=" * 70)
        print("DIFFERENCE BY TIME IN WINDOW")
        print("=" * 70)

        time_buckets = [(0, 3), (3, 6), (6, 9), (9, 12), (12, 15)]
        for t_min, t_max in time_buckets:
            bucket = [d for d in all_diffs if t_min <= d["time"] < t_max]
            if bucket:
                avg = sum(d["diff"] for d in bucket) / len(bucket)
                avg_abs = sum(abs(d["diff"]) for d in bucket) / len(bucket)
                print(f"{t_min:2d}-{t_max:2d}m: avg diff {avg:+.3f}, abs {avg_abs:.3f} (n={len(bucket)})")
        print()

        # find divergences
        print("=" * 70)
        print("LARGE DIVERGENCES (|diff| > 0.10)")
        print("=" * 70)

        divergences = [d for d in all_diffs if abs(d["diff"]) > 0.10]
        print(f"Found {len(divergences)} ({100*len(divergences)/len(all_diffs):.1f}%)")

        if divergences:
            btc_higher = [d for d in divergences if d["diff"] > 0]
            eth_higher = [d for d in divergences if d["diff"] < 0]
            print(f"  BTC higher: {len(btc_higher)}")
            print(f"  ETH higher: {len(eth_higher)}")

            # show some examples
            print("\nExamples:")
            for d in divergences[:10]:
                print(f"  t={d['time']:.1f}m: BTC={d['btc']:.2f} ETH={d['eth']:.2f} diff={d['diff']:+.2f}")
        print()

        # arbitrage opportunity
        print("=" * 70)
        print("ARBITRAGE CHECK")
        print("  if BTC and ETH move together, can we predict one from other?")
        print("=" * 70)

        # when btc > 0.60 but eth < 0.55
        arb1 = [d for d in all_diffs if d["btc"] > 0.60 and d["eth"] < 0.55]
        arb2 = [d for d in all_diffs if d["eth"] > 0.60 and d["btc"] < 0.55]

        print(f"BTC > 0.60 but ETH < 0.55: {len(arb1)} points ({100*len(arb1)/len(all_diffs):.1f}%)")
        print(f"ETH > 0.60 but BTC < 0.55: {len(arb2)} points ({100*len(arb2)/len(all_diffs):.1f}%)")

        # extreme cases
        print()
        print("Extreme divergence (one > 0.70, other < 0.40):")
        extreme = [d for d in all_diffs if (d["btc"] > 0.70 and d["eth"] < 0.40) or (d["eth"] > 0.70 and d["btc"] < 0.40)]
        print(f"  Found: {len(extreme)}")
        for d in extreme[:5]:
            print(f"    t={d['time']:.1f}m: BTC={d['btc']:.2f} ETH={d['eth']:.2f}")

        # run strategy backtests
        await backtest_strategies(session, pairs)


async def backtest_strategies(session, pairs):
    """backtest pair trading strategies"""
    print()
    print("=" * 70)
    print("STRATEGY BACKTESTS")
    print("=" * 70)

    results = []
    SLIP = 0.03

    for p in pairs[:50]:
        btc_hist = await get_prices(session, p["btc_token"], p["ts"], p["ts"] + 900)
        eth_hist = await get_prices(session, p["eth_token"], p["ts"], p["ts"] + 900)

        if not btc_hist or not eth_hist:
            continue

        # get final prices (outcome)
        btc_final = btc_hist[-1]["p"] if btc_hist else 0.5
        eth_final = eth_hist[-1]["p"] if eth_hist else 0.5
        btc_won = btc_final > 0.9
        eth_won = eth_final > 0.9

        # align prices
        btc_prices = {h["t"]: h["p"] for h in btc_hist}
        eth_prices = {h["t"]: h["p"] for h in eth_hist}

        for ts in sorted(set(btc_prices.keys()) & set(eth_prices.keys())):
            time_left = (p["ts"] + 900 - ts) / 60
            if time_left < 3:  # skip last 3 min
                continue

            btc_p = btc_prices[ts]
            eth_p = eth_prices[ts]
            diff = btc_p - eth_p

            results.append({
                "time_left": time_left,
                "btc_p": btc_p,
                "eth_p": eth_p,
                "diff": diff,
                "btc_won": btc_won,
                "eth_won": eth_won
            })

        await asyncio.sleep(0.02)

    print(f"Collected {len(results)} entry points")
    print()

    # STRATEGY 1: Buy laggard when divergence > threshold
    print("STRATEGY 1: Buy laggard on divergence")
    print("-" * 50)

    for thresh in [0.15, 0.20, 0.25, 0.30]:
        trades = []
        for r in results:
            if abs(r["diff"]) < thresh:
                continue

            if r["diff"] > 0:  # BTC higher, buy ETH
                entry = r["eth_p"] + SLIP
                won = r["eth_won"]
                pnl = (1 - entry) if won else -entry
                trades.append({"side": "ETH", "entry": entry, "pnl": pnl})
            else:  # ETH higher, buy BTC
                entry = r["btc_p"] + SLIP
                won = r["btc_won"]
                pnl = (1 - entry) if won else -entry
                trades.append({"side": "BTC", "entry": entry, "pnl": pnl})

        if trades:
            total = sum(t["pnl"] for t in trades)
            wins = sum(1 for t in trades if t["pnl"] > 0)
            print(f"  thresh={thresh:.2f}: {len(trades)} trades, {100*wins/len(trades):.0f}% win, ${total:+.2f}")

    # STRATEGY 2: Fade the leader when one is extreme
    print()
    print("STRATEGY 2: Fade extreme prices")
    print("-" * 50)

    for high_thresh in [0.70, 0.75, 0.80]:
        trades = []
        for r in results:
            # fade BTC if extreme high
            if r["btc_p"] > high_thresh and r["eth_p"] < 0.50:
                entry = 1 - r["btc_p"] + SLIP  # buy DOWN
                won = not r["btc_won"]
                pnl = (1 - entry) if won else -entry
                trades.append({"side": "fade_BTC", "pnl": pnl})

            # fade ETH if extreme high
            if r["eth_p"] > high_thresh and r["btc_p"] < 0.50:
                entry = 1 - r["eth_p"] + SLIP
                won = not r["eth_won"]
                pnl = (1 - entry) if won else -entry
                trades.append({"side": "fade_ETH", "pnl": pnl})

        if trades:
            total = sum(t["pnl"] for t in trades)
            wins = sum(1 for t in trades if t["pnl"] > 0)
            print(f"  leader > {high_thresh:.2f}: {len(trades)} trades, {100*wins/len(trades):.0f}% win, ${total:+.2f}")

    # STRATEGY 3: Both should win/lose together
    print()
    print("STRATEGY 3: Correlation bet (both should align)")
    print("-" * 50)

    same_outcome = sum(1 for r in results if r["btc_won"] == r["eth_won"])
    print(f"Same outcome rate: {100*same_outcome/len(results):.1f}%")

    # when one is winning clearly, bet the other catches up
    for leader_thresh in [0.65, 0.70, 0.75]:
        trades = []
        for r in results:
            if r["btc_p"] > leader_thresh and r["eth_p"] < leader_thresh - 0.10:
                # BTC winning, ETH lagging - buy ETH UP
                entry = r["eth_p"] + SLIP
                won = r["eth_won"]
                pnl = (1 - entry) if won else -entry
                trades.append({"pnl": pnl})

            elif r["eth_p"] > leader_thresh and r["btc_p"] < leader_thresh - 0.10:
                # ETH winning, BTC lagging - buy BTC UP
                entry = r["btc_p"] + SLIP
                won = r["btc_won"]
                pnl = (1 - entry) if won else -entry
                trades.append({"pnl": pnl})

        if trades:
            total = sum(t["pnl"] for t in trades)
            wins = sum(1 for t in trades if t["pnl"] > 0)
            print(f"  leader > {leader_thresh:.2f}, lag > 0.10: {len(trades)} trades, {100*wins/len(trades):.0f}% win, ${total:+.2f}")

    # STRATEGY 4: Use BTC price to predict ETH outcome (cross-asset signal)
    print()
    print("STRATEGY 4: Cross-asset prediction")
    print("  (BTC poly price predicts ETH outcome)")
    print("-" * 50)

    for btc_thresh in [0.65, 0.70, 0.75, 0.80]:
        trades = []
        for r in results:
            # if BTC is winning clearly, bet ETH also wins
            if r["btc_p"] > btc_thresh:
                entry = r["eth_p"] + SLIP
                won = r["eth_won"]
                pnl = (1 - entry) if won else -entry
                trades.append({"entry": entry, "pnl": pnl, "eth_p": r["eth_p"]})

        if trades:
            total = sum(t["pnl"] for t in trades)
            wins = sum(1 for t in trades if t["pnl"] > 0)
            avg_entry = sum(t["eth_p"] for t in trades) / len(trades)
            print(f"  BTC > {btc_thresh:.2f} → buy ETH: {len(trades)} trades, {100*wins/len(trades):.0f}% win, ${total:+.2f} (avg ETH entry: {avg_entry:.2f})")

    # only buy ETH when it's cheap but BTC is winning
    print()
    print("STRATEGY 5: Cross-asset + cheap entry")
    print("  (BTC winning but ETH still cheap)")
    print("-" * 50)

    for btc_thresh, eth_max in [(0.65, 0.50), (0.70, 0.55), (0.75, 0.60), (0.70, 0.45)]:
        trades = []
        for r in results:
            if r["btc_p"] > btc_thresh and r["eth_p"] < eth_max:
                entry = r["eth_p"] + SLIP
                won = r["eth_won"]
                pnl = (1 - entry) if won else -entry
                trades.append({"pnl": pnl})

        if trades:
            total = sum(t["pnl"] for t in trades)
            wins = sum(1 for t in trades if t["pnl"] > 0)
            print(f"  BTC > {btc_thresh:.2f}, ETH < {eth_max:.2f}: {len(trades)} trades, {100*wins/len(trades):.0f}% win, ${total:+.2f}")


if __name__ == "__main__":
    asyncio.run(main())
