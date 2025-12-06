#!/usr/bin/env python3
"""
fast copybot - watches on-chain events directly
~2-5s latency vs 10-30s from data API
"""
import asyncio
import json
import aiohttp
import websockets
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from eth_abi import decode

# contracts
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEG_RISK_CTF = "0xC5d563A36AE78145C45a50134d48A1215220f80a"

# OrderFilled event topic
# event OrderFilled(bytes32 indexed orderHash, address indexed maker, address indexed taker,
#                   uint256 makerAssetId, uint256 takerAssetId, uint256 makerAmountFilled,
#                   uint256 takerAmountFilled, uint256 fee)
ORDER_FILLED_TOPIC = "0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6"

# polygon websocket rpcs (fallback chain)
WS_RPCS = [
    "wss://polygon-bor-rpc.publicnode.com",
    "wss://polygon.drpc.org",
    "wss://polygon-mainnet.public.blastapi.io",
]

# apis
CLOB_API = "https://clob.polymarket.com"
DATA_API = "https://data-api.polymarket.com"

# target
SHARKY = "0x751a2b86cab503496efd325c8344e10159349ea1"

@dataclass
class CopyTrade:
    ts: str
    tx_hash: str
    block: int
    token_id: str
    side: str
    shares: float
    usdc: float
    price: float
    market: str = ""
    outcome: str = ""
    our_price: float = 0
    our_cost: float = 0
    latency_ms: int = 0
    status: str = "pending"
    pnl: float = 0

class FastCopyBot:
    def __init__(self, target: str, size_usd: float = 100):
        self.target = target.lower()
        self.size_usd = size_usd
        self.trades: list[CopyTrade] = []
        self.seen_txs: set[str] = set()
        self.running = True
        self.ws = None
        self.http_session = None
        self.current_rpc = 0

        # market cache
        self.market_cache: dict[str, dict] = {}

        # output
        self.output_dir = Path("data")
        self.output_dir.mkdir(exist_ok=True)
        self.trades_file = self.output_dir / "copybot_fast_trades.json"
        self.load_state()

        # stats
        self.events_seen = 0
        self.reconnects = 0

    def load_state(self):
        if self.trades_file.exists():
            try:
                data = json.load(open(self.trades_file))
                self.seen_txs = set(data.get("seen_txs", []))
                self.trades = [CopyTrade(**t) for t in data.get("trades", [])]
                print(f"loaded {len(self.trades)} trades, {len(self.seen_txs)} seen txs")
            except:
                pass

    def save_state(self):
        data = {
            "seen_txs": list(self.seen_txs)[-1000:],  # keep last 1000
            "trades": [asdict(t) for t in self.trades[-100:]]  # keep last 100
        }
        with open(self.trades_file, "w") as f:
            json.dump(data, f, indent=2)

    def decode_order_filled(self, log: dict) -> dict | None:
        """decode OrderFilled event from raw log"""
        try:
            topics = log.get("topics", [])
            data = log.get("data", "0x")

            if len(topics) < 4:
                return None

            # indexed params from topics
            order_hash = topics[1]
            maker = "0x" + topics[2][-40:]
            taker = "0x" + topics[3][-40:]

            # non-indexed from data
            data_bytes = bytes.fromhex(data[2:])
            decoded = decode(
                ["uint256", "uint256", "uint256", "uint256", "uint256"],
                data_bytes
            )

            maker_asset_id, taker_asset_id, maker_amount, taker_amount, fee = decoded

            return {
                "order_hash": order_hash,
                "maker": maker.lower(),
                "taker": taker.lower(),
                "maker_asset_id": str(maker_asset_id),
                "taker_asset_id": str(taker_asset_id),
                "maker_amount": maker_amount / 1e6,  # USDC decimals
                "taker_amount": taker_amount / 1e6,
                "fee": fee / 1e6,
                "tx_hash": log.get("transactionHash", ""),
                "block": int(log.get("blockNumber", "0x0"), 16),
            }
        except Exception as e:
            print(f"decode error: {e}")
            return None

    def parse_trade(self, event: dict) -> CopyTrade | None:
        """parse decoded event into trade for our target"""
        maker = event["maker"]
        taker = event["taker"]

        if maker != self.target and taker != self.target:
            return None

        is_maker = maker == self.target

        # figure out side and amounts
        # if target is maker and maker_asset_id is 0 (USDC), they're buying
        # asset_id 0 = USDC, non-zero = conditional token
        maker_asset = event["maker_asset_id"]
        taker_asset = event["taker_asset_id"]
        maker_amount = event["maker_amount"]
        taker_amount = event["taker_amount"]

        if is_maker:
            if maker_asset == "0":
                # maker gave USDC, got tokens = BUY
                side, usdc, shares, token = "BUY", maker_amount, taker_amount, taker_asset
            else:
                # maker gave tokens, got USDC = SELL
                side, shares, usdc, token = "SELL", maker_amount, taker_amount, maker_asset
        else:
            if taker_asset == "0":
                # taker gave USDC, got tokens = BUY
                side, usdc, shares, token = "BUY", taker_amount, maker_amount, maker_asset
            else:
                # taker gave tokens, got USDC = SELL
                side, shares, usdc, token = "SELL", taker_amount, maker_amount, taker_asset

        price = usdc / shares if shares > 0 else 0

        return CopyTrade(
            ts=datetime.now(timezone.utc).isoformat(),
            tx_hash=event["tx_hash"],
            block=event["block"],
            token_id=token,
            side=side,
            shares=round(shares, 2),
            usdc=round(usdc, 2),
            price=round(price, 4),
        )

    async def fetch_market_info(self, token_id: str) -> dict:
        """fetch market info for token"""
        if token_id in self.market_cache:
            return self.market_cache[token_id]

        try:
            async with self.http_session.get(
                f"{CLOB_API}/markets/{token_id}",
                timeout=aiohttp.ClientTimeout(total=3)
            ) as resp:
                if resp.status == 200:
                    m = await resp.json()
                    info = {
                        "question": m.get("question", "")[:80],
                        "outcome": m.get("outcome", ""),
                    }
                    self.market_cache[token_id] = info
                    return info
        except:
            pass
        return {}

    async def fetch_current_price(self, token_id: str) -> tuple[float, float]:
        """fetch bid/ask"""
        try:
            async with self.http_session.get(
                f"{CLOB_API}/book",
                params={"token_id": token_id},
                timeout=aiohttp.ClientTimeout(total=2)
            ) as resp:
                if resp.status == 200:
                    book = await resp.json()
                    bid = float(book["bids"][0]["price"]) if book.get("bids") else 0
                    ask = float(book["asks"][0]["price"]) if book.get("asks") else 1
                    return bid, ask
        except:
            pass
        return 0, 1

    async def process_trade(self, trade: CopyTrade):
        """process and paper execute copy trade"""
        if trade.tx_hash in self.seen_txs:
            return

        self.seen_txs.add(trade.tx_hash)

        # skip sells for now
        if trade.side != "BUY":
            print(f"\n  [skip] {trade.side} trade")
            return

        start = datetime.now(timezone.utc)

        # fetch market info and current price concurrently
        market_info, (bid, ask) = await asyncio.gather(
            self.fetch_market_info(trade.token_id),
            self.fetch_current_price(trade.token_id)
        )

        trade.market = market_info.get("question", "unknown")
        trade.outcome = market_info.get("outcome", "")
        trade.our_price = ask
        trade.our_cost = round(self.size_usd, 2)
        trade.latency_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        trade.status = "paper_filled"

        our_shares = self.size_usd / ask if ask > 0 else 0
        slippage = ask - trade.price

        self.trades.append(trade)
        self.save_state()

        # log it
        print(f"\n{'='*70}")
        print(f"🎯 COPY TRADE @ {datetime.now().strftime('%H:%M:%S.%f')[:-3]}")
        print(f"{'='*70}")
        print(f"Block: {trade.block} | Tx: {trade.tx_hash[:16]}...")
        print(f"Market: {trade.market}")
        print(f"Outcome: {trade.outcome}")
        print(f"")
        print(f"Target: {trade.side} @ ${trade.price:.4f} ({trade.shares:.0f} shares, ${trade.usdc:.2f})")
        print(f"Us:     {trade.side} @ ${ask:.4f} ({our_shares:.0f} shares, ${self.size_usd:.2f})")
        print(f"")
        print(f"Slippage: ${slippage:.4f} ({slippage/trade.price*100:.2f}%)" if trade.price > 0 else "")
        print(f"Spread: ${bid:.3f} / ${ask:.3f}")
        print(f"Fetch latency: {trade.latency_ms}ms")
        print(f"{'='*70}\n")

    async def subscribe(self):
        """subscribe to OrderFilled events"""
        # eth_subscribe to logs
        sub_msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_subscribe",
            "params": [
                "logs",
                {
                    "address": [CTF_EXCHANGE, NEG_RISK_CTF],
                    "topics": [ORDER_FILLED_TOPIC]
                }
            ]
        }
        await self.ws.send(json.dumps(sub_msg))
        resp = await self.ws.recv()
        data = json.loads(resp)

        if "result" in data:
            print(f"subscribed: {data['result']}")
            return True
        else:
            print(f"subscribe failed: {data}")
            return False

    async def listen(self):
        """listen for events"""
        while self.running:
            try:
                msg = await asyncio.wait_for(self.ws.recv(), timeout=30)
                data = json.loads(msg)

                if data.get("method") == "eth_subscription":
                    self.events_seen += 1
                    log = data.get("params", {}).get("result", {})

                    event = self.decode_order_filled(log)
                    if event:
                        trade = self.parse_trade(event)
                        if trade:
                            await self.process_trade(trade)

                    self.print_status()

            except asyncio.TimeoutError:
                # send ping to keep alive
                await self.ws.ping()
            except websockets.ConnectionClosed:
                print("\nconnection closed, reconnecting...")
                break
            except Exception as e:
                print(f"\nlisten error: {e}")
                await asyncio.sleep(1)

    def print_status(self):
        """status line"""
        print(f"\r[{datetime.now().strftime('%H:%M:%S')}] "
              f"events: {self.events_seen} | "
              f"copies: {len(self.trades)} | "
              f"reconnects: {self.reconnects}",
              end="", flush=True)

    async def connect_ws(self) -> bool:
        """connect to websocket rpc"""
        for i, rpc in enumerate(WS_RPCS):
            try:
                print(f"connecting to {rpc}...")
                self.ws = await asyncio.wait_for(
                    websockets.connect(rpc, ping_interval=20, ping_timeout=10),
                    timeout=10
                )
                self.current_rpc = i
                return True
            except Exception as e:
                print(f"  failed: {e}")
        return False

    async def run(self):
        """main loop with reconnection"""
        print(f"\n{'='*70}")
        print(f"FAST COPYBOT - On-Chain Events")
        print(f"{'='*70}")
        print(f"Target: {self.target}")
        print(f"Copy size: ${self.size_usd}")
        print(f"Watching: CTF Exchange + NegRisk CTF")
        print(f"{'='*70}\n")

        self.http_session = aiohttp.ClientSession()

        try:
            while self.running:
                if not await self.connect_ws():
                    print("all RPCs failed, retrying in 10s...")
                    await asyncio.sleep(10)
                    continue

                if not await self.subscribe():
                    await asyncio.sleep(5)
                    continue

                print(f"\nlistening for {self.target[:10]}... trades\n")
                await self.listen()

                self.reconnects += 1
                await asyncio.sleep(2)

        except KeyboardInterrupt:
            print("\n\nshutting down...")
        finally:
            if self.ws:
                await self.ws.close()
            if self.http_session:
                await self.http_session.close()
            self.print_summary()

    def print_summary(self):
        print(f"\n\n{'='*70}")
        print("SESSION SUMMARY")
        print(f"{'='*70}")
        print(f"Events seen: {self.events_seen}")
        print(f"Trades copied: {len(self.trades)}")
        print(f"Reconnects: {self.reconnects}")

        if self.trades:
            avg_latency = sum(t.latency_ms for t in self.trades) / len(self.trades)
            print(f"Avg fetch latency: {avg_latency:.0f}ms")

        print(f"\nTrades saved to: {self.trades_file}")
        print(f"{'='*70}\n")

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", "-t", default=SHARKY)
    parser.add_argument("--size", "-s", type=float, default=100)
    args = parser.parse_args()

    bot = FastCopyBot(args.target, args.size)
    asyncio.run(bot.run())

if __name__ == "__main__":
    main()
