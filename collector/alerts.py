#!/usr/bin/env python3
"""
Window alerts - tracks stats during collection and sends Telegram summaries
"""

import os
import json
from dataclasses import dataclass, field
from datetime import datetime

import aiohttp

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')

# our price levels
PRICE_LEVELS = [0.44, 0.46, 0.48]


@dataclass
class CoinStats:
    coin: str
    up_bid: float = 0
    up_ask: float = 1
    down_bid: float = 0
    down_ask: float = 1
    up_volume: float = 0
    down_volume: float = 0
    up_trades: int = 0
    down_trades: int = 0
    up_fills: list = field(default_factory=list)  # (price, size) tuples
    down_fills: list = field(default_factory=list)
    edge_samples: list = field(default_factory=list)
    arb_ops: list = field(default_factory=list)

    @property
    def combined_bid(self):
        return self.up_bid + self.down_bid

    @property
    def combined_ask(self):
        return self.up_ask + self.down_ask

    @property
    def edge(self):
        return 1.0 - self.combined_bid if self.combined_bid < 1 else 0

    @property
    def spread(self):
        return self.combined_ask - self.combined_bid if self.combined_bid > 0 else 0

    @property
    def total_volume(self):
        return self.up_volume + self.down_volume

    @property
    def total_trades(self):
        return self.up_trades + self.down_trades

    def update_book(self, side: str, bid: float, ask: float, elapsed: float):
        if side == 'up':
            self.up_bid = bid
            self.up_ask = ask
        else:
            self.down_bid = bid
            self.down_ask = ask

        # sample edge
        if self.combined_bid > 0:
            self.edge_samples.append(self.edge)

        # check for arb (combined_ask < 1.0)
        if self.up_ask < 1 and self.down_ask < 1 and self.combined_ask < 1.0:
            profit = (1.0 - self.combined_ask) * 100
            self.arb_ops.append((elapsed, self.combined_ask, profit))

    def add_trade(self, side: str, price: float, size: float, trade_side: str):
        vol = price * size
        # track all volume
        if side == 'up':
            self.up_trades += 1
            self.up_volume += vol
        else:
            self.down_trades += 1
            self.down_volume += vol

        # only track SELL trades at our price levels
        if trade_side == 'SELL':
            for level in PRICE_LEVELS:
                if price <= level + 0.01:
                    if side == 'up':
                        self.up_fills.append((level, size))  # record at our level, not trade price
                    else:
                        self.down_fills.append((level, size))
                    break  # only count once at lowest matching level


class WindowAlerts:
    def __init__(self):
        self.coins = {}
        self.session_windows = 0

    def reset(self, coin_list: list):
        self.coins = {coin: CoinStats(coin=coin) for coin in coin_list}

    def update_book(self, coin: str, side: str, bid: float, ask: float, elapsed: float):
        if coin in self.coins:
            self.coins[coin].update_book(side, bid, ask, elapsed)

    def add_trade(self, coin: str, side: str, price: float, size: float, trade_side: str):
        if coin in self.coins:
            self.coins[coin].add_trade(side, price, size, trade_side)

    async def send_telegram(self, msg: str):
        if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
            return

        try:
            url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
            async with aiohttp.ClientSession() as session:
                await session.post(url, json={
                    'chat_id': TELEGRAM_CHAT_ID,
                    'text': msg,
                    'parse_mode': 'HTML'
                })
        except Exception as e:
            print(f'[tg] error: {e}')

    async def send_summary(self, window_ts: int, clob_count: int, rtds_count: int):
        dt = datetime.utcfromtimestamp(window_ts)
        self.session_windows += 1

        total_volume = 0
        total_trades = 0
        total_pnl = 0
        all_arbs = []
        lines = [f'<b>Window {dt.strftime("%H:%M")} UTC</b>']
        lines.append('')

        for coin, stats in sorted(self.coins.items()):
            total_volume += stats.total_volume
            total_trades += stats.total_trades
            all_arbs.extend([(coin, *arb) for arb in stats.arb_ops])

            # accumulate pnl per level
            for level in PRICE_LEVELS:
                up_at_level = sum(s for p, s in stats.up_fills if p == level)
                down_at_level = sum(s for p, s in stats.down_fills if p == level)
                matched = min(up_at_level, down_at_level)
                edge = 1 - level * 2
                total_pnl += matched * edge

            if stats.total_trades == 0:
                lines.append(f'<b>{coin.upper()}</b>: no activity')
                continue

            # edge stats
            avg_edge = sum(stats.edge_samples) / len(stats.edge_samples) * 100 if stats.edge_samples else 0
            min_edge = min(stats.edge_samples) * 100 if stats.edge_samples else 0
            max_edge = max(stats.edge_samples) * 100 if stats.edge_samples else 0

            # calc pnl per level
            up_shares = sum(s for _, s in stats.up_fills)
            down_shares = sum(s for _, s in stats.down_fills)

            pnl = 0
            level_details = []
            for level in PRICE_LEVELS:
                up_at = sum(s for p, s in stats.up_fills if p == level)
                down_at = sum(s for p, s in stats.down_fills if p == level)
                matched = min(up_at, down_at)
                if matched > 0:
                    edge = 1 - level * 2
                    pnl += matched * edge
                    level_details.append(f'{level}:{matched:.0f}')

            lines.append(f'<b>{coin.upper()}</b>')
            lines.append(f'  trades: {stats.up_trades} up / {stats.down_trades} dn')
            lines.append(f'  volume: ${stats.up_volume:.0f} up / ${stats.down_volume:.0f} dn')
            if up_shares > 0 or down_shares > 0:
                lines.append(f'  fills: {up_shares:.0f} up / {down_shares:.0f} dn | <b>${pnl:+.2f}</b>')
                if level_details:
                    lines.append(f'  levels: {" ".join(level_details)}')
            lines.append(f'  book edge: {avg_edge:+.1f}% avg ({min_edge:+.1f} to {max_edge:+.1f})')
            lines.append('')

        # arb opportunities
        if all_arbs:
            lines.append(f'<b>ARB ({len(all_arbs)})</b>')
            for coin, elapsed, combined, profit in sorted(all_arbs, key=lambda x: -x[3])[:5]:
                lines.append(f'  {coin.upper()} @{elapsed:.0f}s: {combined:.3f} (+{profit:.1f}%)')
            lines.append('')

        # totals
        lines.append('<b>TOTALS</b>')
        lines.append(f'  volume: ${total_volume:.0f}')
        lines.append(f'  trades: {total_trades}')
        lines.append(f'  pnl: <b>${total_pnl:+.2f}</b>')
        lines.append(f'  session: {self.session_windows} windows')

        msg = '\n'.join(lines)
        await self.send_telegram(msg)
        print(f'[alerts] sent summary: vol=${total_volume:.0f} trades={total_trades} arbs={len(all_arbs)}')
