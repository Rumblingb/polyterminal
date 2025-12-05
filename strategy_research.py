"""
10 strategies inspired by stocks, options, crypto
"""
import asyncio
import json
import re
import aiohttp
from collections import defaultdict

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
                    "slug": slug, "winner": winner, "up_token": tokens[0],
                    "start_ts": window_ts, "end_ts": window_ts + 900,
                })
                break
    return markets


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


def calc_returns(prices):
    """calculate price changes between points"""
    if len(prices) < 2:
        return []
    return [prices[i] - prices[i-1] for i in range(1, len(prices))]


async def main():
    print("=" * 70)
    print("10 STRATEGIES FROM STOCKS/OPTIONS/CRYPTO")
    print("=" * 70)

    async with aiohttp.ClientSession() as session:
        markets = await get_markets(session, 200)
        print(f"Found {len(markets)} markets")

        # collect full price paths
        windows = []
        for i, m in enumerate(markets[:100]):
            hist = await get_prices(session, m["up_token"], m["start_ts"], m["end_ts"])
            if hist and len(hist) >= 10:
                prices = [h["p"] for h in hist]
                times = [h["t"] for h in hist]
                windows.append({
                    "prices": prices,
                    "times": times,
                    "winner_up": m["winner"] == "Up",
                    "start_ts": m["start_ts"],
                    "end_ts": m["end_ts"],
                })
            if (i + 1) % 25 == 0:
                print(f"Loaded {i+1}...")
            await asyncio.sleep(0.05)

        print(f"\nAnalyzing {len(windows)} windows with data")
        print()

        SLIP = 0.03

        # ============================================================
        # 1. OPENING RANGE BREAKOUT (stocks)
        # first 3 mins establish range, trade breakout
        # ============================================================
        print("=" * 70)
        print("1. OPENING RANGE BREAKOUT")
        print("   first 3 points set high/low, trade breakout direction")
        print("=" * 70)

        orb_trades = []
        for w in windows:
            if len(w["prices"]) < 5:
                continue
            # opening range = first 3 points
            opening = w["prices"][:3]
            orb_high = max(opening)
            orb_low = min(opening)

            # check for breakout in remaining points
            for i, p in enumerate(w["prices"][3:], 3):
                if p > orb_high + 0.05:  # breakout up
                    entry = p + SLIP
                    won = w["winner_up"]
                    orb_trades.append({"won": won, "pnl": (1-entry) if won else -entry})
                    break
                elif p < orb_low - 0.05:  # breakout down
                    entry = (1 - p) + SLIP  # buy DOWN
                    won = not w["winner_up"]
                    orb_trades.append({"won": won, "pnl": (1-entry) if won else -entry})
                    break

        if orb_trades:
            wins = sum(1 for t in orb_trades if t["won"])
            pnl = sum(t["pnl"] for t in orb_trades)
            print(f"   Trades: {len(orb_trades)}, Wins: {wins} ({100*wins/len(orb_trades):.0f}%), P&L: ${pnl:.2f}")
        print()

        # ============================================================
        # 2. MOMENTUM (stocks/crypto)
        # if price moved X in last N points, continue
        # ============================================================
        print("=" * 70)
        print("2. MOMENTUM")
        print("   if price up >10c in last 3 points, buy UP")
        print("=" * 70)

        mom_trades = []
        for w in windows:
            prices = w["prices"]
            for i in range(3, len(prices) - 2):  # need history and future
                recent_move = prices[i] - prices[i-3]
                time_left = (w["end_ts"] - w["times"][i]) / 60

                if time_left > 3:  # not too late
                    if recent_move > 0.10:  # momentum up
                        entry = prices[i] + SLIP
                        won = w["winner_up"]
                        mom_trades.append({"won": won, "pnl": (1-entry) if won else -entry})
                        break
                    elif recent_move < -0.10:  # momentum down
                        entry = (1 - prices[i]) + SLIP
                        won = not w["winner_up"]
                        mom_trades.append({"won": won, "pnl": (1-entry) if won else -entry})
                        break

        if mom_trades:
            wins = sum(1 for t in mom_trades if t["won"])
            pnl = sum(t["pnl"] for t in mom_trades)
            print(f"   Trades: {len(mom_trades)}, Wins: {wins} ({100*wins/len(mom_trades):.0f}%), P&L: ${pnl:.2f}")
        print()

        # ============================================================
        # 3. MEAN REVERSION (stocks)
        # extreme moves revert - fade spikes
        # ============================================================
        print("=" * 70)
        print("3. MEAN REVERSION")
        print("   if price spikes >15c in 2 points, fade it")
        print("=" * 70)

        mr_trades = []
        for w in windows:
            prices = w["prices"]
            for i in range(2, len(prices) - 2):
                spike = prices[i] - prices[i-2]
                time_left = (w["end_ts"] - w["times"][i]) / 60

                if time_left > 5:  # early enough to revert
                    if spike > 0.15:  # spiked up, fade by buying DOWN
                        entry = (1 - prices[i]) + SLIP
                        won = not w["winner_up"]
                        mr_trades.append({"won": won, "pnl": (1-entry) if won else -entry})
                        break
                    elif spike < -0.15:  # spiked down, fade by buying UP
                        entry = prices[i] + SLIP
                        won = w["winner_up"]
                        mr_trades.append({"won": won, "pnl": (1-entry) if won else -entry})
                        break

        if mr_trades:
            wins = sum(1 for t in mr_trades if t["won"])
            pnl = sum(t["pnl"] for t in mr_trades)
            print(f"   Trades: {len(mr_trades)}, Wins: {wins} ({100*wins/len(mr_trades):.0f}%), P&L: ${pnl:.2f}")
        print()

        # ============================================================
        # 4. THETA DECAY (options)
        # price should accelerate toward 0 or 1 as expiry nears
        # buy when price is trending and time is running out
        # ============================================================
        print("=" * 70)
        print("4. THETA DECAY / TIME ACCELERATION")
        print("   late window, price > 0.55, bet it goes to 1.0")
        print("=" * 70)

        theta_trades = []
        for w in windows:
            prices = w["prices"]
            times = w["times"]
            for i in range(len(prices)):
                time_left = (w["end_ts"] - times[i]) / 60
                p = prices[i]

                if 2 < time_left < 5:  # sweet spot
                    if p > 0.55:  # leaning UP
                        entry = p + SLIP
                        won = w["winner_up"]
                        theta_trades.append({"won": won, "pnl": (1-entry) if won else -entry, "entry": entry})
                        break
                    elif p < 0.45:  # leaning DOWN
                        entry = (1 - p) + SLIP
                        won = not w["winner_up"]
                        theta_trades.append({"won": won, "pnl": (1-entry) if won else -entry, "entry": entry})
                        break

        if theta_trades:
            wins = sum(1 for t in theta_trades if t["won"])
            pnl = sum(t["pnl"] for t in theta_trades)
            avg_entry = sum(t["entry"] for t in theta_trades) / len(theta_trades)
            print(f"   Trades: {len(theta_trades)}, Wins: {wins} ({100*wins/len(theta_trades):.0f}%), P&L: ${pnl:.2f}")
            print(f"   Avg entry: {avg_entry:.2f}")
        print()

        # ============================================================
        # 5. GAMMA SCALPING (options)
        # volatility clusters - high vol predicts more vol
        # ============================================================
        print("=" * 70)
        print("5. VOLATILITY CLUSTERING")
        print("   high recent volatility = bet on continuation to extreme")
        print("=" * 70)

        vol_trades = []
        for w in windows:
            prices = w["prices"]
            if len(prices) < 8:
                continue

            # calc volatility of first half
            mid = len(prices) // 2
            first_half = prices[:mid]
            vol = sum(abs(first_half[i] - first_half[i-1]) for i in range(1, len(first_half)))

            if vol > 0.20:  # high volatility
                # bet on direction at midpoint
                mid_price = prices[mid]
                if mid_price > 0.50:
                    entry = mid_price + SLIP
                    won = w["winner_up"]
                else:
                    entry = (1 - mid_price) + SLIP
                    won = not w["winner_up"]
                vol_trades.append({"won": won, "pnl": (1-entry) if won else -entry})

        if vol_trades:
            wins = sum(1 for t in vol_trades if t["won"])
            pnl = sum(t["pnl"] for t in vol_trades)
            print(f"   Trades: {len(vol_trades)}, Wins: {wins} ({100*wins/len(vol_trades):.0f}%), P&L: ${pnl:.2f}")
        print()

        # ============================================================
        # 6. PIN RISK / MAGNET EFFECT (options)
        # prices gravitate to round numbers near expiry
        # ============================================================
        print("=" * 70)
        print("6. ROUND NUMBER GRAVITY")
        print("   check if prices cluster at 0.50, 0.60, etc")
        print("=" * 70)

        # analyze price distribution
        round_counts = defaultdict(int)
        for w in windows:
            for p in w["prices"]:
                rounded = round(p, 1)
                round_counts[rounded] += 1

        total = sum(round_counts.values())
        print("   Price distribution:")
        for r in sorted(round_counts.keys()):
            pct = 100 * round_counts[r] / total
            bar = "#" * int(pct / 2)
            print(f"   {r:.1f}: {bar} ({pct:.1f}%)")
        print()

        # ============================================================
        # 7. LIQUIDATION CASCADE (crypto)
        # sharp move triggers more moves in same direction
        # ============================================================
        print("=" * 70)
        print("7. CASCADE / SNOWBALL EFFECT")
        print("   sharp move (>20c) triggers continuation")
        print("=" * 70)

        cascade_trades = []
        for w in windows:
            prices = w["prices"]
            for i in range(1, len(prices) - 3):
                move = prices[i] - prices[i-1]
                time_left = (w["end_ts"] - w["times"][i]) / 60

                if time_left > 3 and abs(move) > 0.20:  # big single-period move
                    if move > 0:  # cascade up
                        entry = prices[i] + SLIP
                        won = w["winner_up"]
                    else:  # cascade down
                        entry = (1 - prices[i]) + SLIP
                        won = not w["winner_up"]
                    cascade_trades.append({"won": won, "pnl": (1-entry) if won else -entry})
                    break

        if cascade_trades:
            wins = sum(1 for t in cascade_trades if t["won"])
            pnl = sum(t["pnl"] for t in cascade_trades)
            print(f"   Trades: {len(cascade_trades)}, Wins: {wins} ({100*wins/len(cascade_trades):.0f}%), P&L: ${pnl:.2f}")
        print()

        # ============================================================
        # 8. CONTRARIAN LATE (stocks)
        # fade the crowd in final minutes
        # ============================================================
        print("=" * 70)
        print("8. CONTRARIAN LATE")
        print("   if price at 0.60-0.75 with <3min left, fade it")
        print("=" * 70)

        contra_trades = []
        for w in windows:
            prices = w["prices"]
            times = w["times"]
            for i in range(len(prices)):
                time_left = (w["end_ts"] - times[i]) / 60
                p = prices[i]

                if time_left < 3:
                    if 0.60 < p < 0.75:  # moderate UP favorite, fade
                        entry = (1 - p) + SLIP
                        won = not w["winner_up"]
                        contra_trades.append({"won": won, "pnl": (1-entry) if won else -entry})
                        break
                    elif 0.25 < p < 0.40:  # moderate DOWN favorite, fade
                        entry = p + SLIP
                        won = w["winner_up"]
                        contra_trades.append({"won": won, "pnl": (1-entry) if won else -entry})
                        break

        if contra_trades:
            wins = sum(1 for t in contra_trades if t["won"])
            pnl = sum(t["pnl"] for t in contra_trades)
            print(f"   Trades: {len(contra_trades)}, Wins: {wins} ({100*wins/len(contra_trades):.0f}%), P&L: ${pnl:.2f}")
        print()

        # ============================================================
        # 9. TREND + TIME (combo)
        # consistent trend direction + time running out
        # ============================================================
        print("=" * 70)
        print("9. TREND CONFIRMATION")
        print("   5+ consecutive moves in same direction, ride it")
        print("=" * 70)

        trend_trades = []
        for w in windows:
            prices = w["prices"]
            times = w["times"]
            for i in range(5, len(prices)):
                time_left = (w["end_ts"] - times[i]) / 60
                if time_left < 2:
                    break

                # check last 5 moves
                recent = prices[i-5:i+1]
                moves = [recent[j] - recent[j-1] for j in range(1, len(recent))]

                if all(m > 0 for m in moves):  # 5 up moves
                    entry = prices[i] + SLIP
                    won = w["winner_up"]
                    trend_trades.append({"won": won, "pnl": (1-entry) if won else -entry})
                    break
                elif all(m < 0 for m in moves):  # 5 down moves
                    entry = (1 - prices[i]) + SLIP
                    won = not w["winner_up"]
                    trend_trades.append({"won": won, "pnl": (1-entry) if won else -entry})
                    break

        if trend_trades:
            wins = sum(1 for t in trend_trades if t["won"])
            pnl = sum(t["pnl"] for t in trend_trades)
            print(f"   Trades: {len(trend_trades)}, Wins: {wins} ({100*wins/len(trend_trades):.0f}%), P&L: ${pnl:.2f}")
        print()

        # ============================================================
        # 10. DELTA NEUTRAL FLIP (options)
        # when price crosses 0.50, side has changed
        # ============================================================
        print("=" * 70)
        print("10. MIDPOINT CROSS")
        print("    when price crosses 0.50, bet on new direction")
        print("=" * 70)

        cross_trades = []
        for w in windows:
            prices = w["prices"]
            times = w["times"]
            for i in range(1, len(prices)):
                time_left = (w["end_ts"] - times[i]) / 60
                if time_left < 3:
                    break

                # check for 0.50 cross
                prev, curr = prices[i-1], prices[i]
                if prev < 0.50 and curr >= 0.50:  # crossed up
                    entry = curr + SLIP
                    won = w["winner_up"]
                    cross_trades.append({"won": won, "pnl": (1-entry) if won else -entry})
                    break
                elif prev >= 0.50 and curr < 0.50:  # crossed down
                    entry = (1 - curr) + SLIP
                    won = not w["winner_up"]
                    cross_trades.append({"won": won, "pnl": (1-entry) if won else -entry})
                    break

        if cross_trades:
            wins = sum(1 for t in cross_trades if t["won"])
            pnl = sum(t["pnl"] for t in cross_trades)
            print(f"   Trades: {len(cross_trades)}, Wins: {wins} ({100*wins/len(cross_trades):.0f}%), P&L: ${pnl:.2f}")
        print()

        # ============================================================
        # SUMMARY
        # ============================================================
        print("=" * 70)
        print("SUMMARY: ALL STRATEGIES")
        print("=" * 70)

        all_strats = [
            ("Opening Range Breakout", orb_trades),
            ("Momentum", mom_trades),
            ("Mean Reversion", mr_trades),
            ("Theta Decay", theta_trades),
            ("Volatility Clustering", vol_trades),
            ("Cascade Effect", cascade_trades),
            ("Contrarian Late", contra_trades),
            ("Trend Confirmation", trend_trades),
            ("Midpoint Cross", cross_trades),
        ]

        print(f"{'Strategy':<25} {'Trades':<8} {'Win%':<8} {'P&L':<10}")
        print("-" * 55)
        for name, trades in sorted(all_strats, key=lambda x: sum(t["pnl"] for t in x[1]) if x[1] else -999, reverse=True):
            if trades:
                wins = sum(1 for t in trades if t["won"])
                pnl = sum(t["pnl"] for t in trades)
                print(f"{name:<25} {len(trades):<8} {100*wins/len(trades):.0f}%      ${pnl:+.2f}")
            else:
                print(f"{name:<25} 0        -        -")


if __name__ == "__main__":
    asyncio.run(main())
