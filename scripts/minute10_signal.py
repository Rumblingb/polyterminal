"""Minute-10 style signal calculator for Polymarket 15m windows."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional, Dict
import argparse
import json

WINDOW_SECONDS = 900  # 15 minutes
CONF_THRESHOLD = 0.05


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class Minute10Signal:
    start_price: float
    current_price: float
    elapsed_seconds: float
    predicted_up: float
    confidence: float
    recommendation: Optional[str]

    @property
    def spot_change_pct(self) -> float:
        return (self.current_price - self.start_price) / self.start_price * 100

    @property
    def predicted_down(self) -> float:
        return 1 - self.predicted_up

    @property
    def time_left_seconds(self) -> float:
        return WINDOW_SECONDS - self.elapsed_seconds

    def to_dict(self) -> Dict[str, float | str | None]:
        data = asdict(self)
        data["spot_change_pct"] = self.spot_change_pct
        data["predicted_down"] = self.predicted_down
        data["time_left_seconds"] = self.time_left_seconds
        return data


def compute_signal(start_price: float, current_price: float, elapsed_seconds: float) -> Minute10Signal:
    if start_price <= 0:
        raise ValueError("start_price must be positive")

    elapsed_clamped = clamp(elapsed_seconds, 0, WINDOW_SECONDS)
    spot_chg = (current_price - start_price) / start_price * 100
    time_norm = elapsed_clamped / WINDOW_SECONDS

    predicted_up = 0.50 + 1.5 * spot_chg + 1.5 * spot_chg * time_norm
    predicted_up = clamp(predicted_up, 0.02, 0.98)
    confidence = abs(predicted_up - 0.5)

    recommendation: Optional[str] = None
    if confidence >= CONF_THRESHOLD:
        recommendation = "UP" if predicted_up >= 0.5 else "DOWN"

    return Minute10Signal(
        start_price=start_price,
        current_price=current_price,
        elapsed_seconds=elapsed_clamped,
        predicted_up=predicted_up,
        confidence=confidence,
        recommendation=recommendation,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compute Polymarket minute-10 style signal")
    parser.add_argument("start_price", type=float, help="Window start price (USD)")
    parser.add_argument("current_price", type=float, help="Current price (USD)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--elapsed", type=float, help="Seconds elapsed in window")
    group.add_argument("--time-left", type=float, dest="time_left", help="Seconds remaining in window")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    elapsed = args.elapsed if args.elapsed is not None else WINDOW_SECONDS - args.time_left
    signal = compute_signal(args.start_price, args.current_price, elapsed)

    if args.json:
        print(json.dumps(signal.to_dict(), indent=2))
        return

    print("=== Minute-10 Signal ===")
    print(f"Start Price : ${signal.start_price:,.2f}")
    print(f"Current Price: ${signal.current_price:,.2f}")
    print(f"Spot Change : {signal.spot_change_pct:+.4f}%")
    print(f"Elapsed     : {signal.elapsed_seconds:.0f}s ({signal.time_left_seconds:.0f}s left)")
    print(f"Predicted UP: {signal.predicted_up:.3f}")
    print(f"Predicted DN: {signal.predicted_down:.3f}")
    print(f"Confidence  : {signal.confidence:.3f}")
    print(f"Recommendation: {signal.recommendation or 'SKIP (confidence < threshold)'}")


if __name__ == "__main__":
    main()
