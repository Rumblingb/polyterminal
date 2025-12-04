#!/usr/bin/env python3
"""
Backtest the 15-min momentum strategy on historical data
"""

import requests
import json
from datetime import datetime, timezone

# config
THRESHOLD = 0.20
BET_SIZE = 50

def backtest(days=7):
    print(f"\n{'='*60}")
    print(f"  MOMENTUM STRATEGY BACKTEST ({days} days)")
    print(f"{'='*60}")
    print(f"  Threshold: {THRESHOLD}%")
    print(f"  Bet size: ${BET_SIZE}")
    print(f"{'='*60}\n")

    # fetch historical data
    print("Fetching historical data...")
    all_windows = []

    for d in range(days):
        try:
            end_time = int((datetime.now(timezone.utc).timestamp() - d * 86400) * 1000)
            r = requests.get("https://api.binance.com/api/v3/klines",
                            params={
                                "symbol": "BTCUSDT",
                                "interval": "1m",
                                "limit": 1000,
                                "endTime": end_time
                            }, timeout=30)
            klines = r.json()

            # group into 15-min windows
            for i in range(0, len(klines) - 15, 15):
                window = klines[i:i+15]
                open_price = float(window[0][1])
                close_price = float(window[-1][4])

                # first 5 min
                first5_close = float(window[4][4])
                momentum = (first5_close - open_price) / open_price * 100

                # full 15 min
                final_move = (close_price - open_price) / open_price * 100

                all_windows.append({
                    'time': datetime.fromtimestamp(int(window[0][0])/1000, tz=timezone.utc),
                    'open': open_price,
                    'close': close_price,
                    'momentum': momentum,
                    'final_move': final_move,
                    'up': close_price >= open_price
                })
        except Exception as e:
            print(f"Error fetching day {d}: {e}")

    print(f"Analyzed {len(all_windows)} 15-min windows\n")

    # run backtest
    trades = []
    balance = 500

    for w in all_windows:
        if abs(w['momentum']) >= THRESHOLD:
            direction = "UP" if w['momentum'] > 0 else "DOWN"
            predicted_up = direction == "UP"
            win = predicted_up == w['up']

            pnl = BET_SIZE * 0.96 if win else -BET_SIZE
            balance += pnl

            trades.append({
                'time': w['time'],
                'momentum': w['momentum'],
                'direction': direction,
                'final_move': w['final_move'],
                'win': win,
                'pnl': pnl,
                'balance': balance
            })

    # results
    wins = sum(1 for t in trades if t['win'])
    losses = len(trades) - wins
    total_pnl = sum(t['pnl'] for t in trades)
    win_rate = wins / len(trades) * 100 if trades else 0

    print(f"{'='*60}")
    print(f"  RESULTS")
    print(f"{'='*60}")
    print(f"  Total windows: {len(all_windows)}")
    print(f"  Trades taken: {len(trades)}")
    print(f"  Wins: {wins} ({win_rate:.1f}%)")
    print(f"  Losses: {losses}")
    print(f"  Total P&L: ${total_pnl:+.2f}")
    print(f"  Final balance: ${balance:,.2f}")
    print(f"  ROI: {(balance - 500) / 500 * 100:+.1f}%")
    print(f"{'='*60}\n")

    # show sample trades
    print("Sample trades:")
    for t in trades[:10]:
        result = "WIN " if t['win'] else "LOSS"
        print(f"  {t['time'].strftime('%m/%d %H:%M')} | {t['direction']:4} | mom: {t['momentum']:+.2f}% | final: {t['final_move']:+.2f}% | {result} | ${t['pnl']:+.0f}")

    # by threshold analysis
    print(f"\n{'='*60}")
    print(f"  THRESHOLD ANALYSIS")
    print(f"{'='*60}")

    for thresh in [0.10, 0.15, 0.20, 0.25, 0.30]:
        t_trades = [w for w in all_windows if abs(w['momentum']) >= thresh]
        if t_trades:
            t_wins = sum(1 for t in t_trades if (t['momentum'] > 0) == t['up'])
            t_wr = t_wins / len(t_trades) * 100
            t_pnl = t_wins * (BET_SIZE * 0.96) - (len(t_trades) - t_wins) * BET_SIZE
            print(f"  >{thresh:.2f}%: {len(t_trades):>3} trades | {t_wr:>5.1f}% win | ${t_pnl:>+8.2f}")

    return trades

if __name__ == "__main__":
    backtest(7)
