"""
Backtest Late Window Momentum Strategy using REAL price history

Strategy: Buy the winning side when priced at 0.85+ with <5 minutes left
"""
import asyncio
import json
import re
from datetime import datetime, timedelta
from dataclasses import dataclass
import aiohttp

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

SLIPPAGE = 0.03  # 3 cents


@dataclass
class BacktestResult:
    market_slug: str
    winner: str
    entry_price: float
    entry_side: str
    time_left_mins: float
    pnl: float
    won: bool


async def get_resolved_markets(session, limit=100):
    """get resolved 15m btc markets"""
    url = f"{GAMMA_API}/events?tag_id=102467&closed=true&limit={limit}"
    async with session.get(url) as resp:
        data = await resp.json()

    markets = []
    for e in data:
        slug = e.get("slug", "")
        if "eth" not in slug.lower():
            continue

        # extract timestamp from slug (e.g., btc-updown-15m-1733382000)
        match = re.search(r'15m-(\d+)', slug)
        if not match:
            continue
        window_ts = int(match.group(1))
        window_start = datetime.utcfromtimestamp(window_ts)
        window_end = window_start + timedelta(minutes=15)

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

            if tokens and winner and "Up" in str(outcomes):
                markets.append({
                    "slug": slug,
                    "winner": winner,
                    "up_token": tokens[0],
                    "down_token": tokens[1] if len(tokens) > 1 else None,
                    "start_ts": window_ts,
                    "end_ts": window_ts + 900,  # 15 min
                    "start": window_start,
                    "end": window_end,
                })

    # dedupe
    seen = set()
    unique = []
    for m in markets:
        if m["up_token"] not in seen:
            seen.add(m["up_token"])
            unique.append(m)

    return unique


async def get_price_history(session, token_id, start_ts, end_ts):
    """get price history using /prices-history endpoint"""
    url = f"{CLOB_API}/prices-history?market={token_id}&startTs={start_ts}&endTs={end_ts}&fidelity=1"
    try:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("history", [])
    except:
        pass
    return []


def find_late_window_entry(price_history, window_end_ts, min_price=0.85, max_time_left=5, min_time_left=1):
    """
    Find first price point where price >= min_price with time_left in window
    Returns (entry_price, time_left_mins) or (None, None)
    """
    if not price_history:
        return None, None

    for point in price_history:
        ts = point.get("t", 0)
        price = point.get("p", 0)

        time_left_secs = window_end_ts - ts
        time_left_mins = time_left_secs / 60

        if min_time_left <= time_left_mins <= max_time_left and price >= min_price:
            return price, time_left_mins

    return None, None


async def backtest():
    """run backtest on real price history"""
    print("=" * 60)
    print("LATE WINDOW MOMENTUM BACKTEST (REAL DATA)")
    print("=" * 60)
    print()
    print(f"Strategy: Buy winning side when price >= 0.85 with 1-5 min left")
    print(f"Slippage: {SLIPPAGE*100:.0f} cents")
    print()

    async with aiohttp.ClientSession() as session:
        print("Fetching resolved BTC markets...")
        markets = await get_resolved_markets(session, limit=200)
        print(f"Found {len(markets)} unique BTC 15m markets")
        print()

        results = []
        markets_with_data = 0

        for i, m in enumerate(markets):
            # get price history for UP token
            up_history = await get_price_history(
                session, m["up_token"], m["start_ts"], m["end_ts"]
            )

            # get price history for DOWN token
            down_history = []
            if m["down_token"]:
                down_history = await get_price_history(
                    session, m["down_token"], m["start_ts"], m["end_ts"]
                )

            if up_history or down_history:
                markets_with_data += 1

            # check for late window entry on UP
            up_entry, up_time = find_late_window_entry(up_history, m["end_ts"])
            down_entry, down_time = find_late_window_entry(down_history, m["end_ts"])

            # simulate trades with slippage
            if up_entry is not None:
                actual_entry = up_entry + SLIPPAGE
                if actual_entry < 1.0:  # still valid entry
                    won = m["winner"] == "Up"
                    pnl = (1.0 - actual_entry) if won else -actual_entry
                    results.append(BacktestResult(
                        market_slug=m["slug"],
                        winner=m["winner"],
                        entry_price=actual_entry,
                        entry_side="UP",
                        time_left_mins=up_time,
                        pnl=pnl,
                        won=won
                    ))

            if down_entry is not None:
                actual_entry = down_entry + SLIPPAGE
                if actual_entry < 1.0:
                    won = m["winner"] == "Down"
                    pnl = (1.0 - actual_entry) if won else -actual_entry
                    results.append(BacktestResult(
                        market_slug=m["slug"],
                        winner=m["winner"],
                        entry_price=actual_entry,
                        entry_side="DOWN",
                        time_left_mins=down_time,
                        pnl=pnl,
                        won=won
                    ))

            if (i + 1) % 20 == 0:
                print(f"Processed {i + 1}/{len(markets)} markets...")

            # small delay to avoid rate limiting
            await asyncio.sleep(0.1)

        print()
        print("=" * 60)
        print("RESULTS")
        print("=" * 60)
        print()
        print(f"Markets analyzed: {len(markets)}")
        print(f"Markets with price data: {markets_with_data}")
        print(f"Entry opportunities found: {len(results)}")
        print()

        if results:
            wins = sum(1 for r in results if r.won)
            losses = len(results) - wins
            total_pnl = sum(r.pnl for r in results)
            avg_win = sum(r.pnl for r in results if r.won) / wins if wins > 0 else 0
            avg_loss = sum(r.pnl for r in results if not r.won) / losses if losses > 0 else 0

            print(f"Win Rate: {wins}/{len(results)} ({100*wins/len(results):.1f}%)")
            print(f"Avg Win: ${avg_win:.2f}")
            print(f"Avg Loss: ${avg_loss:.2f}")
            print(f"Total P&L (per $1 bet): ${total_pnl:.2f}")
            print(f"ROI: {100*total_pnl/len(results):.1f}%")
            print()

            print("Sample trades:")
            for r in results[:15]:
                status = "WIN" if r.won else "LOSS"
                print(f"  {r.entry_side} @ {r.entry_price:.2f} ({r.time_left_mins:.1f}m left) -> {status} ${r.pnl:+.2f}")

            if len(results) > 15:
                print(f"  ... and {len(results) - 15} more trades")
        else:
            print("No entry opportunities found in historical data.")
            print("This could mean:")
            print("1. Markets rarely hit 0.85+ in the late window")
            print("2. Price history data is sparse")
            print("3. Need to adjust entry parameters")


if __name__ == "__main__":
    asyncio.run(backtest())
