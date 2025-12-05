"""
high frequency momentum - buy/sell within window, don't hold to expiry
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
            tokens = m.get("clobTokenIds", "[]")
            if isinstance(tokens, str):
                tokens = json.loads(tokens)
            if tokens:
                markets.append({
                    "slug": slug, "up_token": tokens[0],
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


def simulate_momentum_scalp(prices, entry_threshold, take_profit, stop_loss, max_hold):
    """
    momentum scalping strategy:
    - entry: price moves > entry_threshold in last 2 points
    - exit: take_profit, stop_loss, or max_hold points
    """
    trades = []
    i = 2
    while i < len(prices) - 1:
        # check for momentum entry signal
        move = prices[i]["p"] - prices[i-2]["p"]

        if abs(move) >= entry_threshold:
            # enter trade
            direction = "UP" if move > 0 else "DOWN"
            entry_price = prices[i]["p"]

            # simulate holding and exit
            for j in range(i + 1, min(i + max_hold + 1, len(prices))):
                current = prices[j]["p"]

                if direction == "UP":
                    pnl = current - entry_price
                else:
                    pnl = entry_price - current

                # check exit conditions
                if pnl >= take_profit:
                    trades.append({"dir": direction, "entry": entry_price, "exit": current,
                                  "pnl": pnl, "reason": "TP", "hold": j - i})
                    i = j + 1
                    break
                elif pnl <= -stop_loss:
                    trades.append({"dir": direction, "entry": entry_price, "exit": current,
                                  "pnl": pnl, "reason": "SL", "hold": j - i})
                    i = j + 1
                    break
            else:
                # max hold reached, exit at last price
                final = prices[min(i + max_hold, len(prices) - 1)]["p"]
                if direction == "UP":
                    pnl = final - entry_price
                else:
                    pnl = entry_price - final
                trades.append({"dir": direction, "entry": entry_price, "exit": final,
                              "pnl": pnl, "reason": "MAX", "hold": max_hold})
                i = i + max_hold + 1
        else:
            i += 1

    return trades


async def main():
    print("=" * 70)
    print("HIGH FREQUENCY MOMENTUM SCALPING")
    print("=" * 70)

    async with aiohttp.ClientSession() as session:
        markets = await get_markets(session, 200)
        print(f"Found {len(markets)} markets")

        # collect all price paths
        all_prices = []
        for i, m in enumerate(markets[:100]):
            hist = await get_prices(session, m["up_token"], m["start_ts"], m["end_ts"])
            if hist and len(hist) >= 10:
                all_prices.append(hist)
            if (i + 1) % 25 == 0:
                print(f"Loaded {i+1}...")
            await asyncio.sleep(0.05)

        print(f"\nLoaded {len(all_prices)} price paths")
        print()

        # test different parameter combinations
        print("=" * 70)
        print("PARAMETER SWEEP")
        print("=" * 70)

        results = []

        for entry_thresh in [0.05, 0.08, 0.10, 0.15]:
            for tp in [0.03, 0.05, 0.08, 0.10]:
                for sl in [0.03, 0.05, 0.08]:
                    for max_hold in [3, 5, 8]:
                        all_trades = []
                        for prices in all_prices:
                            trades = simulate_momentum_scalp(prices, entry_thresh, tp, sl, max_hold)
                            all_trades.extend(trades)

                        if len(all_trades) >= 20:
                            total_pnl = sum(t["pnl"] for t in all_trades)
                            wins = sum(1 for t in all_trades if t["pnl"] > 0)
                            avg_pnl = total_pnl / len(all_trades)

                            # account for spread/slippage (2c each way = 4c round trip)
                            net_pnl = total_pnl - len(all_trades) * 0.04

                            results.append({
                                "entry": entry_thresh,
                                "tp": tp,
                                "sl": sl,
                                "max_hold": max_hold,
                                "n": len(all_trades),
                                "wins": wins,
                                "win_pct": wins / len(all_trades) * 100,
                                "gross_pnl": total_pnl,
                                "net_pnl": net_pnl,
                                "avg_gross": avg_pnl * 100,  # in cents
                            })

        # sort by net P&L
        results.sort(key=lambda x: x["net_pnl"], reverse=True)

        print(f"\n{'Entry':<8} {'TP':<6} {'SL':<6} {'Hold':<6} {'N':<6} {'Win%':<8} {'Gross':<10} {'Net':<10}")
        print("-" * 70)
        for r in results[:20]:
            print(f"{r['entry']:.2f}     {r['tp']:.2f}   {r['sl']:.2f}   {r['max_hold']:<6} {r['n']:<6} {r['win_pct']:.0f}%     ${r['gross_pnl']:+.2f}    ${r['net_pnl']:+.2f}")

        print()
        print("=" * 70)
        print("BEST STRATEGY DETAILS")
        print("=" * 70)

        if results:
            best = results[0]
            print(f"Entry threshold: {best['entry']:.2f} (price move in 2 points)")
            print(f"Take profit: {best['tp']:.2f}")
            print(f"Stop loss: {best['sl']:.2f}")
            print(f"Max hold: {best['max_hold']} points")
            print()
            print(f"Trades: {best['n']}")
            print(f"Win rate: {best['win_pct']:.0f}%")
            print(f"Gross P&L: ${best['gross_pnl']:.2f}")
            print(f"Net P&L (after 4c spread): ${best['net_pnl']:.2f}")
            print(f"Avg gross per trade: {best['avg_gross']:.1f}c")

            # run best strategy again and analyze
            print()
            print("Trade breakdown:")
            all_trades = []
            for prices in all_prices:
                trades = simulate_momentum_scalp(prices, best["entry"], best["tp"], best["sl"], best["max_hold"])
                all_trades.extend(trades)

            tp_trades = [t for t in all_trades if t["reason"] == "TP"]
            sl_trades = [t for t in all_trades if t["reason"] == "SL"]
            max_trades = [t for t in all_trades if t["reason"] == "MAX"]

            print(f"  Take profit exits: {len(tp_trades)} (avg +{sum(t['pnl'] for t in tp_trades)/len(tp_trades)*100:.1f}c)" if tp_trades else "  Take profit exits: 0")
            print(f"  Stop loss exits: {len(sl_trades)} (avg {sum(t['pnl'] for t in sl_trades)/len(sl_trades)*100:.1f}c)" if sl_trades else "  Stop loss exits: 0")
            print(f"  Max hold exits: {len(max_trades)} (avg {sum(t['pnl'] for t in max_trades)/len(max_trades)*100:.1f}c)" if max_trades else "  Max hold exits: 0")

            # direction analysis
            up_trades = [t for t in all_trades if t["dir"] == "UP"]
            down_trades = [t for t in all_trades if t["dir"] == "DOWN"]
            print()
            print(f"  UP momentum: {len(up_trades)} trades, {sum(1 for t in up_trades if t['pnl'] > 0)/len(up_trades)*100:.0f}% win" if up_trades else "")
            print(f"  DOWN momentum: {len(down_trades)} trades, {sum(1 for t in down_trades if t['pnl'] > 0)/len(down_trades)*100:.0f}% win" if down_trades else "")

        # compare to hold-to-expiry
        print()
        print("=" * 70)
        print("COMPARISON: SCALP vs HOLD-TO-EXPIRY")
        print("=" * 70)

        # simulate simple hold strategy on same signals
        hold_trades = []
        for prices in all_prices:
            for i in range(2, len(prices) - 1):
                move = prices[i]["p"] - prices[i-2]["p"]
                if abs(move) >= 0.10:
                    entry = prices[i]["p"]
                    final = prices[-1]["p"]
                    if move > 0:  # momentum up, buy UP
                        pnl = final - entry
                    else:  # momentum down, buy DOWN
                        pnl = entry - final
                    hold_trades.append(pnl)
                    break

        if hold_trades:
            gross = sum(hold_trades)
            net = gross - len(hold_trades) * 0.04
            wins = sum(1 for p in hold_trades if p > 0)
            print(f"Hold to expiry on momentum signal:")
            print(f"  Trades: {len(hold_trades)}")
            print(f"  Win rate: {wins/len(hold_trades)*100:.0f}%")
            print(f"  Net P&L: ${net:.2f}")


if __name__ == "__main__":
    asyncio.run(main())
