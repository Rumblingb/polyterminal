#!/usr/bin/env python3
"""
polymarket wallet analyzer
fetches complete trade history, positions, and P&L for any wallet
usage: python fetch_wallet.py <wallet_address> [--output-dir data]
"""
import asyncio
import aiohttp
import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

# config
SUBGRAPH = "https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/orderbook-subgraph/0.0.1/gn"
CLOB = "https://clob.polymarket.com"
BATCH_SIZE = 1000
MARKET_BATCH = 100
CONCURRENCY = 30

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)

@dataclass
class Trade:
    ts: int
    hash: str
    side: str
    token: str
    shares: float
    usdc: float
    price: float
    fee: float
    is_maker: bool

@dataclass
class Position:
    token: str
    question: str
    outcome: str
    status: str
    shares: float
    cost: float
    revenue: float
    avg_entry: float
    current_price: Optional[float]
    pnl: float
    num_trades: int

class WalletAnalyzer:
    def __init__(self, wallet: str, output_dir: str = "data"):
        self.wallet = wallet.lower()
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)

        # state files for resume
        self.trades_file = self.output_dir / f"{self.wallet[:10]}_trades.json"
        self.cache_file = self.output_dir / f"{self.wallet[:10]}_markets.json"
        self.positions_file = self.output_dir / f"{self.wallet[:10]}_positions.json"
        self.summary_file = self.output_dir / f"{self.wallet[:10]}_summary.json"

        self.trades: list[Trade] = []
        self.market_cache: dict = {}
        self.positions: list[Position] = []

    async def fetch_trades_page(self, session: aiohttp.ClientSession, role: str, skip: int) -> list:
        """fetch one page of trades"""
        query = """
        {
          orderFilledEvents(
            where: { %s: "%s" }
            first: %d
            skip: %d
            orderBy: timestamp
            orderDirection: asc
          ) {
            id
            transactionHash
            timestamp
            maker
            taker
            makerAssetId
            takerAssetId
            makerAmountFilled
            takerAmountFilled
            fee
          }
        }
        """ % (role, self.wallet, BATCH_SIZE, skip)

        try:
            async with session.post(SUBGRAPH, json={"query": query}, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                data = await resp.json()
                return data.get("data", {}).get("orderFilledEvents", [])
        except Exception as e:
            log.warning(f"fetch error: {e}")
            return []

    async def fetch_all_trades(self, session: aiohttp.ClientSession) -> list[Trade]:
        """fetch all trades for wallet"""
        all_events = []

        for role in ["maker", "taker"]:
            skip = 0
            log.info(f"fetching as {role}...")

            while True:
                events = await self.fetch_trades_page(session, role, skip)
                if not events:
                    break

                all_events.extend(events)
                log.info(f"  {role}: {len(all_events):,} total")

                if len(events) < BATCH_SIZE:
                    break
                skip += BATCH_SIZE
                await asyncio.sleep(0.1)

        # dedupe
        seen = set()
        unique = []
        for e in all_events:
            if e["id"] not in seen:
                seen.add(e["id"])
                unique.append(e)

        log.info(f"unique events: {len(unique):,}")

        # decode
        trades = []
        for e in unique:
            trade = self._decode_trade(e)
            if trade:
                trades.append(trade)

        trades.sort(key=lambda x: x.ts)
        return trades

    def _decode_trade(self, event: dict) -> Optional[Trade]:
        """decode event into trade"""
        try:
            maker_asset = event["makerAssetId"]
            taker_asset = event["takerAssetId"]
            maker_amount = int(event["makerAmountFilled"]) / 1e6
            taker_amount = int(event["takerAmountFilled"]) / 1e6
            fee = int(event["fee"]) / 1e6 if event.get("fee") else 0

            is_maker = event["maker"].lower() == self.wallet

            if is_maker:
                if maker_asset == "0":
                    side, usdc, shares, token = "BUY", maker_amount, taker_amount, taker_asset
                else:
                    side, shares, usdc, token = "SELL", maker_amount, taker_amount, maker_asset
            else:
                if taker_asset == "0":
                    side, usdc, shares, token = "BUY", taker_amount, maker_amount, maker_asset
                else:
                    side, shares, usdc, token = "SELL", taker_amount, maker_amount, taker_asset

            price = usdc / shares if shares > 0 else 0

            return Trade(
                ts=int(event["timestamp"]),
                hash=event["transactionHash"],
                side=side,
                token=token,
                shares=round(shares, 6),
                usdc=round(usdc, 6),
                price=round(price, 6),
                fee=round(fee, 6),
                is_maker=is_maker
            )
        except Exception as e:
            log.warning(f"decode error: {e}")
            return None

    async def fetch_market(self, session: aiohttp.ClientSession, token: str, sem: asyncio.Semaphore) -> tuple:
        """fetch market info"""
        async with sem:
            try:
                async with session.get(f"{CLOB}/markets/{token}", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        m = await resp.json()
                        return token, {
                            "question": m.get("question", "")[:150],
                            "outcome": m.get("outcome", ""),
                            "closed": m.get("closed", False),
                        }
            except:
                pass
            return token, None

    async def fetch_price(self, session: aiohttp.ClientSession, token: str, sem: asyncio.Semaphore) -> tuple:
        """fetch current price"""
        async with sem:
            try:
                async with session.get(f"{CLOB}/price", params={"token_id": token}, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return token, float(data.get("price", 0))
            except:
                pass
            return token, None

    async def enrich_markets(self, session: aiohttp.ClientSession, tokens: list[str]):
        """fetch missing market info"""
        missing = [t for t in tokens if t not in self.market_cache]
        if not missing:
            return

        log.info(f"fetching {len(missing):,} markets...")
        sem = asyncio.Semaphore(CONCURRENCY)

        for i in range(0, len(missing), MARKET_BATCH):
            batch = missing[i:i+MARKET_BATCH]
            tasks = [self.fetch_market(session, t, sem) for t in batch]
            results = await asyncio.gather(*tasks)

            for tid, info in results:
                if info:
                    self.market_cache[tid] = info

            # save progress
            with open(self.cache_file, "w") as f:
                json.dump(self.market_cache, f)

            log.info(f"  markets: {len(self.market_cache):,}")

    async def fetch_current_prices(self, session: aiohttp.ClientSession, tokens: list[str]) -> dict:
        """fetch current prices for open positions"""
        log.info(f"fetching prices for {len(tokens):,} tokens...")
        sem = asyncio.Semaphore(CONCURRENCY)
        prices = {}

        for i in range(0, len(tokens), MARKET_BATCH):
            batch = tokens[i:i+MARKET_BATCH]
            tasks = [self.fetch_price(session, t, sem) for t in batch]
            results = await asyncio.gather(*tasks)

            for tid, price in results:
                if price is not None:
                    prices[tid] = price

        return prices

    def calculate_positions(self, current_prices: dict) -> list[Position]:
        """calculate positions from trades"""
        pos_data = defaultdict(lambda: {"cost": 0, "revenue": 0, "shares": 0, "trades": 0})

        for t in self.trades:
            p = pos_data[t.token]
            if t.side == "BUY":
                p["cost"] += t.usdc
                p["shares"] += t.shares
            else:
                p["revenue"] += t.usdc
                p["shares"] -= t.shares
            p["trades"] += 1

        positions = []
        for token, data in pos_data.items():
            info = self.market_cache.get(token, {})
            price = current_prices.get(token)
            avg_entry = data["cost"] / (data["shares"] + (data["revenue"] / (price or 1) if data["shares"] <= 0 else 0)) if data["cost"] > 0 else 0

            if data["shares"] <= 0:
                status = "closed"
                pnl = data["revenue"] - data["cost"]
            elif info.get("closed"):
                status = "resolved"
                pnl = data["shares"] + data["revenue"] - data["cost"]
            else:
                status = "open"
                current_value = data["shares"] * price if price else 0
                pnl = current_value + data["revenue"] - data["cost"]

            positions.append(Position(
                token=token,
                question=info.get("question", ""),
                outcome=info.get("outcome", ""),
                status=status,
                shares=round(data["shares"], 2),
                cost=round(data["cost"], 2),
                revenue=round(data["revenue"], 2),
                avg_entry=round(avg_entry, 4) if avg_entry < 2 else 0,
                current_price=price,
                pnl=round(pnl, 2),
                num_trades=data["trades"]
            ))

        positions.sort(key=lambda x: x.pnl, reverse=True)
        return positions

    def generate_summary(self) -> dict:
        """generate analysis summary"""
        buys = [t for t in self.trades if t.side == "BUY"]
        sells = [t for t in self.trades if t.side == "SELL"]

        # entry distribution
        price_ranges = [(0, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 0.95), (0.95, 0.99), (0.99, 1.01)]
        entry_dist = {}
        for lo, hi in price_ranges:
            in_range = [t for t in buys if lo <= t.price < hi]
            entry_dist[f"{int(lo*100)}-{int(hi*100)}%"] = {
                "trades": len(in_range),
                "volume": round(sum(t.usdc for t in in_range), 2)
            }

        # position stats
        closed = [p for p in self.positions if p.status == "closed"]
        resolved = [p for p in self.positions if p.status == "resolved"]
        open_pos = [p for p in self.positions if p.status == "open"]

        # time distribution
        by_month = defaultdict(lambda: {"trades": 0, "volume": 0})
        for t in self.trades:
            month = datetime.fromtimestamp(t.ts).strftime("%Y-%m")
            by_month[month]["trades"] += 1
            by_month[month]["volume"] += t.usdc

        return {
            "wallet": self.wallet,
            "generated_at": datetime.now().isoformat(),
            "trades": {
                "total": len(self.trades),
                "buys": len(buys),
                "sells": len(sells),
                "as_maker": len([t for t in self.trades if t.is_maker]),
                "as_taker": len([t for t in self.trades if not t.is_maker]),
            },
            "volume": {
                "total": round(sum(t.usdc for t in self.trades), 2),
                "buy": round(sum(t.usdc for t in buys), 2),
                "sell": round(sum(t.usdc for t in sells), 2),
            },
            "entry_distribution": entry_dist,
            "positions": {
                "total": len(self.positions),
                "closed": len(closed),
                "resolved": len(resolved),
                "open": len(open_pos),
            },
            "pnl": {
                "closed": round(sum(p.pnl for p in closed), 2),
                "resolved": round(sum(p.pnl for p in resolved), 2),
                "open_unrealized": round(sum(p.pnl for p in open_pos), 2),
                "total_realized": round(sum(p.pnl for p in closed + resolved), 2),
            },
            "monthly_activity": dict(sorted(by_month.items())),
            "top_winners": [asdict(p) for p in self.positions[:10]],
            "top_losers": [asdict(p) for p in sorted(self.positions, key=lambda x: x.pnl)[:10]],
        }

    async def run(self):
        """main execution"""
        log.info(f"analyzing wallet: {self.wallet}")

        # load cache
        if self.cache_file.exists():
            self.market_cache = json.load(open(self.cache_file))
            log.info(f"loaded {len(self.market_cache):,} cached markets")

        connector = aiohttp.TCPConnector(limit=50)
        async with aiohttp.ClientSession(connector=connector) as session:
            # fetch trades
            log.info("fetching trades...")
            self.trades = await self.fetch_all_trades(session)
            log.info(f"total trades: {len(self.trades):,}")

            # save trades
            with open(self.trades_file, "w") as f:
                json.dump([asdict(t) for t in self.trades], f)
            log.info(f"saved: {self.trades_file}")

            # get unique tokens
            tokens = list(set(t.token for t in self.trades))
            log.info(f"unique tokens: {len(tokens):,}")

            # enrich with market info
            await self.enrich_markets(session, tokens)

            # get open position tokens
            pos_data = defaultdict(float)
            for t in self.trades:
                pos_data[t.token] += t.shares if t.side == "BUY" else -t.shares
            open_tokens = [k for k, v in pos_data.items() if v > 0.5]

            # fetch current prices
            current_prices = await self.fetch_current_prices(session, open_tokens)

            # calculate positions
            log.info("calculating positions...")
            self.positions = self.calculate_positions(current_prices)

            # save positions
            with open(self.positions_file, "w") as f:
                json.dump([asdict(p) for p in self.positions], f, indent=2)
            log.info(f"saved: {self.positions_file}")

            # generate summary
            summary = self.generate_summary()
            with open(self.summary_file, "w") as f:
                json.dump(summary, f, indent=2)
            log.info(f"saved: {self.summary_file}")

            # print summary
            self._print_summary(summary)

    def _print_summary(self, s: dict):
        """print summary to console"""
        print("\n" + "="*60)
        print(f"WALLET ANALYSIS: {s['wallet'][:20]}...")
        print("="*60)

        print(f"\nTRADES: {s['trades']['total']:,}")
        print(f"  Buys: {s['trades']['buys']:,} | Sells: {s['trades']['sells']:,}")
        print(f"  As maker: {s['trades']['as_maker']:,} | As taker: {s['trades']['as_taker']:,}")

        print(f"\nVOLUME: ${s['volume']['total']:,.0f}")
        print(f"  Buy: ${s['volume']['buy']:,.0f} | Sell: ${s['volume']['sell']:,.0f}")

        print(f"\nENTRY DISTRIBUTION:")
        for k, v in s['entry_distribution'].items():
            print(f"  {k}: {v['trades']:,} trades, ${v['volume']:,.0f}")

        print(f"\nPOSITIONS: {s['positions']['total']:,}")
        print(f"  Closed: {s['positions']['closed']:,} | Resolved: {s['positions']['resolved']:,} | Open: {s['positions']['open']:,}")

        print(f"\nP&L:")
        print(f"  Closed: ${s['pnl']['closed']:,.0f}")
        print(f"  Resolved: ${s['pnl']['resolved']:,.0f}")
        print(f"  Total realized: ${s['pnl']['total_realized']:,.0f}")

        print("\n" + "="*60)

def main():
    parser = argparse.ArgumentParser(description="Analyze Polymarket wallet")
    parser.add_argument("wallet", help="Wallet address to analyze")
    parser.add_argument("--output-dir", "-o", default="data", help="Output directory")
    args = parser.parse_args()

    if not args.wallet.startswith("0x") or len(args.wallet) != 42:
        print("error: invalid wallet address")
        sys.exit(1)

    analyzer = WalletAnalyzer(args.wallet, args.output_dir)
    asyncio.run(analyzer.run())

if __name__ == "__main__":
    main()
