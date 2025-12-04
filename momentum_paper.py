#!/usr/bin/env python3
"""
15-Min BTC Momentum Paper Trading Bot

Strategy: When BTC moves >0.20% in first 5 min of 15-min window,
bet continuation. Backtested 92% win rate.
"""

import requests
import time
import json
import os
from datetime import datetime, timezone
from pathlib import Path

# config
THRESHOLD = 0.20  # minimum move % to trigger trade
BET_SIZE = 50     # paper dollars per trade
LOG_DIR = Path(__file__).parent / "logs"

# state
paper_balance = 500  # starting bankroll
total_trades = 0
total_wins = 0
session_pnl = 0

def get_btc_price():
    """get current BTC price from binance"""
    r = requests.get("https://api.binance.com/api/v3/ticker/price",
                     params={"symbol": "BTCUSDT"}, timeout=5)
    return float(r.json()['price'])

def get_next_window_start():
    """calculate seconds until next 15-min window starts"""
    now = datetime.now(timezone.utc)
    mins = now.minute
    secs = now.second

    # next window at :00, :15, :30, :45
    next_window_min = ((mins // 15) + 1) * 15
    if next_window_min >= 60:
        next_window_min = 0
        wait_mins = 60 - mins - 1
    else:
        wait_mins = next_window_min - mins - 1

    wait_secs = (60 - secs) + (wait_mins * 60)
    return wait_secs

def log(msg):
    """print with timestamp"""
    now = datetime.now(timezone.utc)
    ts = now.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")

def save_trade(trade):
    """save trade to log file"""
    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / "trades.json"

    trades = []
    if log_file.exists():
        with open(log_file) as f:
            trades = json.load(f)

    trades.append(trade)

    with open(log_file, 'w') as f:
        json.dump(trades, f, indent=2)

def run():
    global paper_balance, total_trades, total_wins, session_pnl

    print("\n" + "="*60)
    print("  15-MIN BTC MOMENTUM PAPER TRADING BOT")
    print("="*60)
    print(f"  Threshold: {THRESHOLD}%")
    print(f"  Bet size: ${BET_SIZE}")
    print(f"  Starting balance: ${paper_balance}")
    print("="*60 + "\n")

    while True:
        try:
            # wait for next window
            wait = get_next_window_start()
            log(f"Waiting {wait}s for next 15-min window...")
            time.sleep(wait + 1)  # +1 to ensure we're in new window

            # record opening price
            open_price = get_btc_price()
            window_start = datetime.now(timezone.utc)
            log(f"WINDOW START | BTC: ${open_price:,.2f}")

            # wait 5 minutes
            time.sleep(300)

            # check momentum
            price_5min = get_btc_price()
            momentum = (price_5min - open_price) / open_price * 100

            log(f"5-MIN CHECK | BTC: ${price_5min:,.2f} | Move: {momentum:+.3f}%")

            # decide if we trade
            if abs(momentum) >= THRESHOLD:
                direction = "UP" if momentum > 0 else "DOWN"
                log(f">>> SIGNAL: BUY {direction} (momentum: {momentum:+.3f}%)")

                # wait for resolution (10 more minutes)
                time.sleep(600)

                # check result
                final_price = get_btc_price()
                final_move = (final_price - open_price) / open_price * 100

                # determine win/loss
                actual_up = final_price >= open_price
                predicted_up = direction == "UP"
                win = actual_up == predicted_up

                # calculate p&l
                if win:
                    pnl = BET_SIZE * 0.96  # win ~$0.49 per $0.51 bet, minus fees
                    total_wins += 1
                else:
                    pnl = -BET_SIZE

                total_trades += 1
                session_pnl += pnl
                paper_balance += pnl

                result = "WIN" if win else "LOSS"
                log(f"RESULT: {result} | Final: ${final_price:,.2f} ({final_move:+.3f}%)")
                log(f"P&L: ${pnl:+.2f} | Session: ${session_pnl:+.2f} | Balance: ${paper_balance:,.2f}")
                log(f"Stats: {total_wins}/{total_trades} wins ({total_wins/total_trades*100:.1f}%)")

                # save trade
                save_trade({
                    "timestamp": window_start.isoformat(),
                    "open_price": open_price,
                    "price_5min": price_5min,
                    "final_price": final_price,
                    "momentum": momentum,
                    "direction": direction,
                    "result": result,
                    "pnl": pnl
                })

            else:
                log(f"NO TRADE | Momentum {momentum:+.3f}% below threshold {THRESHOLD}%")
                # wait out the window
                time.sleep(600)

            print()  # blank line between windows

        except KeyboardInterrupt:
            print("\n\n" + "="*60)
            print("  SESSION SUMMARY")
            print("="*60)
            print(f"  Total trades: {total_trades}")
            print(f"  Wins: {total_wins} ({total_wins/total_trades*100:.1f}%)" if total_trades > 0 else "  Wins: 0")
            print(f"  Session P&L: ${session_pnl:+.2f}")
            print(f"  Final balance: ${paper_balance:,.2f}")
            print("="*60 + "\n")
            break
        except Exception as e:
            log(f"ERROR: {e}")
            time.sleep(60)

if __name__ == "__main__":
    run()
