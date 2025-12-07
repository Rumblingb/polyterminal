"""Window analyzer for Polymarket 15m BTC up/down markets (sync version)."""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import urllib.parse
import urllib.request
import urllib.error

from minute10_signal import compute_signal

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
BINANCE_API = "https://api.binance.com"
WINDOW_SECONDS = 900
USER_AGENT = "polyterminal-window-analyzer/1.0"


@dataclass
class BookSnapshot:
    bid: float
    ask: float
    bid_size: float
    ask_size: float
    spread: float


class HttpError(RuntimeError):
    pass


def http_get_json(url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if params:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{url}?{query}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
    except urllib.error.HTTPError as exc:
        raise HttpError(f"HTTP {exc.code} for {url}") from exc
    except urllib.error.URLError as exc:
        raise HttpError(f"Network error for {url}: {exc}") from exc

    if not data:
        return {}
    return json.loads(data.decode("utf-8"))


def find_event(coin: str, window_ts: int, limit: int = 100) -> Optional[Dict[str, Any]]:
    ts_fragment = str(window_ts)
    coin_lower = coin.lower()
    for closed in ("false", "true"):
        params = {
            "tag_id": 102467,
            "closed": closed,
            "limit": limit,
        }
        try:
            events = http_get_json(f"{GAMMA_API}/events", params)
        except HttpError:
            continue
        for event in events or []:
            slug = str(event.get("slug", "")).lower()
            if coin_lower in slug and ts_fragment in slug:
                return event
    return None


def fetch_book(token_id: str) -> BookSnapshot:
    url = f"{CLOB_API}/book"
    data = http_get_json(url, {"token_id": token_id})
    bids = data.get("bids", [])
    asks = data.get("asks", [])
    best_bid = max((float(b["price"]) for b in bids), default=0.0)
    best_ask = min((float(a["price"]) for a in asks), default=1.0)
    top_bid_size = float(bids[-1]["size"]) if bids else 0.0
    top_ask_size = float(asks[0]["size"]) if asks else 0.0
    spread = (best_ask - best_bid) if best_ask and best_bid else 0.0
    return BookSnapshot(best_bid, best_ask, top_bid_size, top_ask_size, spread)


def fetch_price_history(token_id: str, start_ts: int, end_ts: int) -> list[Dict[str, Any]]:
    url = f"{CLOB_API}/prices-history"
    data = http_get_json(url, {
        "market": token_id,
        "startTs": start_ts,
        "endTs": end_ts,
        "fidelity": 1,
    })
    return data.get("history", [])


def fetch_binance_open(symbol: str, start_ts: int) -> Optional[float]:
    url = f"{BINANCE_API}/api/v3/klines"
    data = http_get_json(url, {
        "symbol": symbol,
        "interval": "1m",
        "startTime": start_ts * 1000,
        "limit": 1,
    })
    return float(data[0][1]) if data else None


def fetch_binance_price(symbol: str) -> Optional[float]:
    url = f"{BINANCE_API}/api/v3/ticker/price"
    data = http_get_json(url, {"symbol": symbol})
    return float(data.get("price")) if data else None


def derive_slug(coin: str, window_ts: int) -> str:
    return f"{coin}-updown-15m-{window_ts}"


def summarize_history(history: list[Dict[str, Any]]) -> Dict[str, Any]:
    if not history:
        return {"high": None, "low": None, "last": None, "samples": 0}
    prices = [float(entry["p"]) for entry in history]
    return {
        "high": max(prices),
        "low": min(prices),
        "last": prices[-1],
        "samples": len(prices),
    }


def parse_datetime(value: str) -> int:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return int(dt.timestamp())


def analyze_window(coin: str, window_ts: int, as_of: datetime, symbol: str, json_mode: bool) -> None:
    slug = derive_slug(coin, window_ts)
    event = find_event(coin, window_ts)
    if not event:
        raise RuntimeError(f"Unable to find event for slug {slug}")

    markets = event.get("markets", [])
    if not markets:
        raise RuntimeError("Event payload missing markets array")

    tokens_raw = markets[0].get("clobTokenIds", "[]")
    tokens = json.loads(tokens_raw) if isinstance(tokens_raw, str) else tokens_raw
    if len(tokens) < 2:
        raise RuntimeError("Market missing token IDs")
    up_token, down_token = tokens[:2]

    start_price = fetch_binance_open(symbol, window_ts)
    if start_price is None:
        raise RuntimeError("Failed to fetch Binance start price")

    current_price = fetch_binance_price(symbol)
    if current_price is None:
        raise RuntimeError("Failed to fetch Binance current price")

    elapsed_seconds = (as_of - datetime.fromtimestamp(window_ts, tz=timezone.utc)).total_seconds()
    signal = compute_signal(start_price, current_price, elapsed_seconds)

    up_book = fetch_book(up_token)
    down_book = fetch_book(down_token)
    history = fetch_price_history(up_token, window_ts, int(as_of.timestamp()))
    history_summary = summarize_history(history)

    report = {
        "slug": slug,
        "as_of": as_of.isoformat(),
        "start_price": start_price,
        "current_price": current_price,
        "spot_change_pct": signal.spot_change_pct,
        "signal": signal.to_dict(),
        "books": {
            "up": up_book.__dict__,
            "down": down_book.__dict__,
        },
        "price_history": history_summary,
    }

    if json_mode:
        print(json.dumps(report, indent=2))
        return

    print("=== Window Analysis ===")
    print(f"Slug         : {slug}")
    print(f"As Of        : {as_of.isoformat()}")
    print(f"Start Price  : ${start_price:,.2f}")
    print(f"Current Price: ${current_price:,.2f}")
    print(f"Spot Change  : {signal.spot_change_pct:+.4f}%")
    print(f"Predicted UP : {signal.predicted_up:.3f}")
    print(f"Confidence   : {signal.confidence:.3f}")
    print(f"Recommendation: {signal.recommendation or 'SKIP'}")
    print("--- Order Books ---")
    print(f"UP   bid/ask: {up_book.bid:.3f}/{up_book.ask:.3f} (spread {up_book.spread:.3f})")
    print(f"      depth : bid {up_book.bid_size:.2f} | ask {up_book.ask_size:.2f}")
    print(f"DOWN bid/ask: {down_book.bid:.3f}/{down_book.ask:.3f} (spread {down_book.spread:.3f})")
    print(f"      depth : bid {down_book.bid_size:.2f} | ask {down_book.ask_size:.2f}")
    print("--- Price History (UP token) ---")
    if history_summary["samples"]:
        print(f"Samples: {history_summary['samples']}  High: {history_summary['high']:.3f}  Low: {history_summary['low']:.3f}  Last: {history_summary['last']:.3f}")
    else:
        print("No history samples returned")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze a Polymarket 15m window")
    parser.add_argument("coin", choices=["btc", "eth", "sol", "xrp"], help="Coin symbol")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--window-ts", type=int, help="Window start timestamp (unix seconds)")
    group.add_argument("--start", type=str, help="ISO datetime for window start (e.g. 2025-12-07T05:15:00Z)")
    parser.add_argument("--as-of", type=str, help="ISO datetime override for analysis time (default: now UTC)")
    parser.add_argument("--symbol", type=str, default="BTCUSDT", help="Binance symbol, default BTCUSDT")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    window_ts = args.window_ts if args.window_ts else parse_datetime(args.start)
    as_of = datetime.now(timezone.utc) if not args.as_of else datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))

    analyze_window(args.coin, window_ts, as_of, args.symbol.upper(), args.json)


if __name__ == "__main__":
    main()
