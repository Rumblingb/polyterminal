#!/usr/bin/env python3
"""
paper trading copybot
copies trades from a target wallet in real-time (paper only)
usage: python copybot.py [--target WALLET] [--size USD]
"""
import asyncio
import aiohttp
import argparse
import json
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path

# config
DATA_API = "https://data-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
SHARKY = "0x751a2b86cab503496efd325c8344e10159349ea1"
POLL_INTERVAL = 2.0  # seconds
DEFAULT_SIZE = 100  # usd per copy trade

@dataclass
class CopyTrade:
    ts: str
    original_tx: str
    market: str
    outcome: str
    side: str
    target_price: float
    target_size: float
    our_price: float  # price we'd get (current ask/bid)
    our_size: float
    our_cost: float
    status: str = "open"
    exit_price: float = 0
    pnl: float = 0

@dataclass
class CopyBot:
    target: str
    size_usd: float
    trades: list = field(default_factory=list)
    seen_txs: set = field(default_factory=set)
    last_poll: str = ""

    # stats
    total_copies: int = 0
    total_pnl: float = 0

class PaperCopyBot:
    def __init__(self, target: str, size_usd: float = DEFAULT_SIZE):
        self.target = target.lower()
        self.size_usd = size_usd
        self.seen_txs: set[str] = set()
        self.trades: list[CopyTrade] = []
        self.running = True

        # output
        self.output_dir = Path("data")
        self.output_dir.mkdir(exist_ok=True)
        self.trades_file = self.output_dir / "copybot_trades.json"
        self.load_state()

    def load_state(self):
        """load previous trades"""
        if self.trades_file.exists():
            data = json.load(open(self.trades_file))
            self.seen_txs = set(data.get("seen_txs", []))
            self.trades = [CopyTrade(**t) for t in data.get("trades", [])]
            print(f"loaded {len(self.trades)} previous trades, {len(self.seen_txs)} seen txs")

    def save_state(self):
        """persist state"""
        data = {
            "seen_txs": list(self.seen_txs),
            "trades": [asdict(t) for t in self.trades]
        }
        with open(self.trades_file, "w") as f:
            json.dump(data, f, indent=2)

    async def fetch_recent_trades(self, session: aiohttp.ClientSession) -> list[dict]:
        """fetch target's recent trades"""
        url = f"{DATA_API}/trades"
        params = {"user": self.target, "limit": 20}

        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            print(f"  fetch error: {e}")
        return []

    async def fetch_current_price(self, session: aiohttp.ClientSession, token_id: str) -> tuple[float, float]:
        """fetch current bid/ask for token"""
        url = f"{CLOB_API}/book"
        params = {"token_id": token_id}

        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    book = await resp.json()
                    best_bid = float(book["bids"][0]["price"]) if book.get("bids") else 0
                    best_ask = float(book["asks"][0]["price"]) if book.get("asks") else 1
                    return best_bid, best_ask
        except Exception as e:
            print(f"  price fetch error: {e}")
        return 0, 1

    async def process_trade(self, session: aiohttp.ClientSession, trade: dict):
        """process a new trade from target"""
        tx = trade.get("transactionHash", "")
        if tx in self.seen_txs:
            return

        self.seen_txs.add(tx)

        side = trade.get("side", "")
        if side != "BUY":
            print(f"  skipping {side} trade")
            return

        # extract info
        asset = trade.get("asset", "")
        price = float(trade.get("price", 0))
        size = float(trade.get("size", 0))
        market = trade.get("title", "")[:60]
        outcome = trade.get("outcome", "")
        ts = trade.get("timestamp", "")

        # get current price we'd pay
        bid, ask = await self.fetch_current_price(session, asset)

        # calculate our position
        our_shares = self.size_usd / ask if ask > 0 else 0
        our_cost = our_shares * ask
        slippage = ask - price

        copy = CopyTrade(
            ts=ts,
            original_tx=tx[:16],
            market=market,
            outcome=outcome,
            side=side,
            target_price=price,
            target_size=size,
            our_price=ask,
            our_size=round(our_shares, 2),
            our_cost=round(our_cost, 2)
        )

        self.trades.append(copy)
        self.save_state()

        # log it
        print(f"\n{'='*60}")
        print(f"🎯 NEW COPY TRADE @ {datetime.now().strftime('%H:%M:%S')}")
        print(f"{'='*60}")
        print(f"Market: {market}")
        print(f"Outcome: {outcome}")
        print(f"Target bought @ ${price:.3f} ({size:.0f} shares)")
        print(f"We'd buy @ ${ask:.3f} ({our_shares:.0f} shares, ${our_cost:.2f})")
        print(f"Slippage: ${slippage:.3f} ({slippage/price*100:.1f}%)" if price > 0 else "")
        print(f"Bid/Ask: ${bid:.3f} / ${ask:.3f}")
        print(f"{'='*60}\n")

    async def check_resolutions(self, session: aiohttp.ClientSession):
        """check if any open trades have resolved"""
        open_trades = [t for t in self.trades if t.status == "open"]

        for trade in open_trades:
            # for 15-min markets, check if time has passed
            # simplified: just mark based on timestamp age
            try:
                trade_time = datetime.fromisoformat(trade.ts.replace("Z", "+00:00"))
                age_mins = (datetime.now(trade_time.tzinfo) - trade_time).total_seconds() / 60

                if age_mins > 20:  # assume resolved after 20 mins
                    # in real impl, check actual resolution
                    trade.status = "resolved"
                    # assume win at $1 for now (optimistic)
                    trade.exit_price = 1.0
                    trade.pnl = (trade.exit_price - trade.our_price) * trade.our_size
                    print(f"  resolved: {trade.market[:30]}... PnL: ${trade.pnl:.2f}")
            except:
                pass

    def print_status(self):
        """print current status"""
        open_trades = [t for t in self.trades if t.status == "open"]
        resolved = [t for t in self.trades if t.status == "resolved"]
        total_pnl = sum(t.pnl for t in resolved)

        print(f"\r[{datetime.now().strftime('%H:%M:%S')}] "
              f"Watching {self.target[:10]}... | "
              f"Copies: {len(self.trades)} | "
              f"Open: {len(open_trades)} | "
              f"PnL: ${total_pnl:.2f}", end="", flush=True)

    async def run(self):
        """main loop"""
        print(f"\n{'='*60}")
        print(f"PAPER COPYBOT")
        print(f"{'='*60}")
        print(f"Target: {self.target}")
        print(f"Copy size: ${self.size_usd}")
        print(f"Poll interval: {POLL_INTERVAL}s")
        print(f"{'='*60}\n")

        connector = aiohttp.TCPConnector(limit=10)
        async with aiohttp.ClientSession(connector=connector) as session:
            # initial fetch to populate seen_txs
            print("fetching initial trades...")
            initial = await self.fetch_recent_trades(session)
            for t in initial:
                tx = t.get("transactionHash", "")
                if tx:
                    self.seen_txs.add(tx)
            print(f"tracking {len(self.seen_txs)} existing trades\n")

            while self.running:
                try:
                    trades = await self.fetch_recent_trades(session)

                    # process new trades (newest first, so reverse)
                    for trade in reversed(trades):
                        await self.process_trade(session, trade)

                    # check resolutions
                    await self.check_resolutions(session)

                    self.print_status()
                    await asyncio.sleep(POLL_INTERVAL)

                except KeyboardInterrupt:
                    print("\n\nshutting down...")
                    self.running = False
                except Exception as e:
                    print(f"\nerror: {e}")
                    await asyncio.sleep(5)

        self.print_summary()

    def print_summary(self):
        """print final summary"""
        print(f"\n\n{'='*60}")
        print("SESSION SUMMARY")
        print(f"{'='*60}")

        resolved = [t for t in self.trades if t.status == "resolved"]
        open_trades = [t for t in self.trades if t.status == "open"]

        print(f"Total copies: {len(self.trades)}")
        print(f"Resolved: {len(resolved)}")
        print(f"Still open: {len(open_trades)}")

        if resolved:
            wins = [t for t in resolved if t.pnl > 0]
            losses = [t for t in resolved if t.pnl <= 0]
            total_pnl = sum(t.pnl for t in resolved)

            print(f"\nWins: {len(wins)} | Losses: {len(losses)}")
            print(f"Total PnL: ${total_pnl:.2f}")

        print(f"\nTrades saved to: {self.trades_file}")
        print(f"{'='*60}\n")

def main():
    parser = argparse.ArgumentParser(description="Paper copy trading bot")
    parser.add_argument("--target", "-t", default=SHARKY, help="Wallet to copy")
    parser.add_argument("--size", "-s", type=float, default=DEFAULT_SIZE, help="USD per trade")
    args = parser.parse_args()

    bot = PaperCopyBot(args.target, args.size)

    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        print("\nexiting...")

if __name__ == "__main__":
    main()
