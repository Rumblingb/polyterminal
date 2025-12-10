#!/usr/bin/env python3
"""
Realistic Paper Trader - simulates actual fill rates with fixed capital
Models competition with gabagool and other market makers
"""

import asyncio
import json
import os
import time
import random
from datetime import datetime
from dataclasses import dataclass, field

import aiohttp
import websockets

GAMMA_API = 'https://gamma-api.polymarket.com'
CLOB_WS = 'wss://ws-subscriptions-clob.polymarket.com/ws/market'

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')

COINS = ['btc']

# realistic params
CAPITAL_PER_SIDE = 50  # $50 bid on each side per coin
FILL_RATE = 0.10  # back of queue = best ROI per backtest
MIN_ORDER_SIZE = 5  # minimum $5 per order like gabagool


@dataclass
class CoinBook:
    coin: str
    capital_up: float = CAPITAL_PER_SIDE
    capital_down: float = CAPITAL_PER_SIDE
    up_bid: float = 0
    up_ask: float = 1
    down_bid: float = 0
    down_ask: float = 1
    # our simulated fills
    up_fills: list = field(default_factory=list)
    down_fills: list = field(default_factory=list)
    # market totals (for comparison)
    market_up_sells: float = 0
    market_down_sells: float = 0
    trade_count: int = 0
    edge_samples: list = field(default_factory=list)

    @property
    def combined_bid(self):
        return self.up_bid + self.down_bid

    @property
    def edge(self):
        return 1.0 - self.combined_bid if self.combined_bid < 1 else 0

    def try_fill(self, side: str, price: float, size: float) -> float:
        """try to fill from a market SELL, returns our fill size"""
        # skip if no edge (combined_bid >= 1.0)
        if self.combined_bid >= 1.0:
            return 0

        # we post at best_bid (back of queue = best ROI)
        if side == 'up':
            if self.capital_up < MIN_ORDER_SIZE:
                return 0
            if price > self.up_bid + 0.02:  # only fill near our bid
                return 0
            our_share = size * FILL_RATE
            max_shares = self.capital_up / price if price > 0 else 0
            fill_size = min(our_share, max_shares)
            if fill_size > 0:
                cost = fill_size * price
                self.capital_up -= cost
                self.up_fills.append((price, fill_size))
            return fill_size
        else:
            if self.capital_down < MIN_ORDER_SIZE:
                return 0
            if price > self.down_bid + 0.02:
                return 0
            our_share = size * FILL_RATE
            max_shares = self.capital_down / price if price > 0 else 0
            fill_size = min(our_share, max_shares)
            if fill_size > 0:
                cost = fill_size * price
                self.capital_down -= cost
                self.down_fills.append((price, fill_size))
            return fill_size

    def add_market_sell(self, side: str, size: float):
        """track total market SELL volume"""
        if side == 'up':
            self.market_up_sells += size
        else:
            self.market_down_sells += size

    def sample_edge(self):
        if self.combined_bid > 0:
            self.edge_samples.append(self.edge)

    def calc_pnl(self):
        up_shares = sum(s for _, s in self.up_fills)
        down_shares = sum(s for _, s in self.down_fills)
        up_cost = sum(p * s for p, s in self.up_fills)
        down_cost = sum(p * s for p, s in self.down_fills)

        if up_shares == 0 or down_shares == 0:
            return 0, up_shares, down_shares, 1.0, up_cost + down_cost

        matched = min(up_shares, down_shares)
        up_avg = up_cost / up_shares
        down_avg = down_cost / down_shares
        combined = up_avg + down_avg
        edge = 1.0 - combined

        matched_pnl = matched * edge
        # unmatched is a coin flip (EV = 0 at fair price)
        unmatched_up = max(0, up_shares - down_shares)
        unmatched_down = max(0, down_shares - up_shares)
        # assume 50% win rate on unmatched
        unmatched_ev = unmatched_up * (0.5 - up_avg) + unmatched_down * (0.5 - down_avg)

        capital_used = up_cost + down_cost
        return matched_pnl + unmatched_ev, up_shares, down_shares, combined, capital_used


class PaperTrader:
    def __init__(self):
        self.tokens = {}
        self.books = {}
        self.results = []
        self.session_pnl = 0
        self.session_capital = 0

    async def send_telegram(self, msg: str):
        if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
            print(f'[tg] {msg[:100]}...')
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

    async def fetch_markets(self, window_ts: int):
        self.tokens.clear()
        self.books.clear()

        async with aiohttp.ClientSession() as session:
            for coin in COINS:
                slug = f'{coin}-updown-15m-{window_ts}'
                try:
                    async with session.get(f'{GAMMA_API}/markets?slug={slug}') as resp:
                        if resp.status != 200:
                            continue
                        data = await resp.json()
                        if not data:
                            continue

                        market = data[0] if isinstance(data, list) else data
                        tokens = market.get('clobTokenIds', [])
                        if isinstance(tokens, str):
                            tokens = json.loads(tokens)

                        if len(tokens) >= 2:
                            self.tokens[tokens[0]] = (coin, 'up')
                            self.tokens[tokens[1]] = (coin, 'down')
                            self.books[coin] = CoinBook(coin=coin)

                except Exception as e:
                    print(f'[paper] {coin} error: {e}')

    async def run_window(self, window_ts: int):
        window_end = window_ts + 900
        accumulate_end = window_ts + 540  # first 9 min

        dt = datetime.utcfromtimestamp(window_ts)
        print(f'\n[paper] window {dt.strftime("%H:%M")} UTC')

        await self.fetch_markets(window_ts)

        if not self.tokens:
            return

        fills_log = []

        try:
            async with websockets.connect(CLOB_WS) as ws:
                await ws.send(json.dumps({
                    'type': 'subscribe',
                    'channel': 'market',
                    'assets_ids': list(self.tokens.keys())
                }))

                async for raw in ws:
                    now = time.time()
                    if now >= window_end:
                        break

                    try:
                        data = json.loads(raw)
                        if isinstance(data, list):
                            data = data[0] if data else {}

                        event_type = data.get('event_type')
                        elapsed = now - window_ts

                        if event_type == 'price_change':
                            for pc in data.get('price_changes', []):
                                info = self.tokens.get(pc.get('asset_id'))
                                if not info:
                                    continue
                                coin, side = info
                                book = self.books.get(coin)
                                if not book:
                                    continue

                                if side == 'up':
                                    book.up_bid = float(pc.get('best_bid', 0))
                                    book.up_ask = float(pc.get('best_ask', 1))
                                else:
                                    book.down_bid = float(pc.get('best_bid', 0))
                                    book.down_ask = float(pc.get('best_ask', 1))

                                book.sample_edge()

                        elif event_type == 'last_trade_price':
                            info = self.tokens.get(data.get('asset_id'))
                            if not info:
                                continue

                            coin, side = info
                            book = self.books.get(coin)
                            if not book:
                                continue

                            price = float(data.get('price', 0))
                            size = float(data.get('size', 0))
                            trade_side = data.get('side', '')

                            book.trade_count += 1

                            # only try to fill on SELL trades in first 9 min
                            if trade_side == 'SELL':
                                book.add_market_sell(side, size)

                                if now < accumulate_end:
                                    fill = book.try_fill(side, price, size)
                                    if fill > 0:
                                        fills_log.append(f'{elapsed:.0f}s {coin}/{side} {fill:.1f}@${price:.2f}')

                    except:
                        pass

        except Exception as e:
            print(f'[paper] ws error: {e}')

        # calculate results
        total_pnl = 0
        total_capital = 0
        lines = [f'<b>Window {dt.strftime("%H:%M")} UTC</b>']
        lines.append(f'<i>Capital: ${CAPITAL_PER_SIDE}/side, Fill rate: {FILL_RATE*100:.0f}%</i>\n')

        for coin, book in sorted(self.books.items()):
            pnl, up_shares, down_shares, combined, capital = book.calc_pnl()
            total_pnl += pnl
            total_capital += capital

            avg_edge = sum(book.edge_samples) / len(book.edge_samples) * 100 if book.edge_samples else 0
            market_sells = book.market_up_sells + book.market_down_sells
            our_fills = up_shares + down_shares
            capture_rate = (our_fills / market_sells * 100) if market_sells > 0 else 0

            if up_shares == 0 and down_shares == 0:
                lines.append(f'<b>{coin.upper()}</b>: no fills (mkt sells={market_sells:.0f})')
                continue

            emoji = '+' if pnl > 0 else '-' if pnl < 0 else '='
            lines.append(
                f'{emoji} <b>{coin.upper()}</b>: '
                f'↑{up_shares:.0f} ↓{down_shares:.0f} | '
                f'${capital:.0f} used | '
                f'edge {avg_edge:+.1f}% | '
                f'<b>${pnl:+.2f}</b>'
            )
            lines.append(
                f'   mkt sells: ↑{book.market_up_sells:.0f} ↓{book.market_down_sells:.0f} | '
                f'capture: {capture_rate:.1f}%'
            )

        self.session_pnl += total_pnl
        self.session_capital += total_capital
        self.results.append({'window': window_ts, 'pnl': total_pnl, 'capital': total_capital})

        # summary
        roi = (total_pnl / total_capital * 100) if total_capital > 0 else 0
        session_roi = (self.session_pnl / self.session_capital * 100) if self.session_capital > 0 else 0

        lines.append(f'\n<b>Window:</b> ${total_pnl:+.2f} on ${total_capital:.0f} ({roi:+.1f}% ROI)')
        lines.append(f'<b>Session:</b> ${self.session_pnl:+.2f} on ${self.session_capital:.0f} ({session_roi:+.1f}% ROI)')
        lines.append(f'<b>Windows:</b> {len(self.results)}')

        msg = '\n'.join(lines)
        await self.send_telegram(msg)
        print(f'[paper] capital=${total_capital:.0f} pnl=${total_pnl:+.2f} roi={roi:+.1f}%')

    async def run(self):
        print('=' * 60)
        print('PAPER TRADER (back of queue)')
        print(f'Capital: ${CAPITAL_PER_SIDE} per side per coin')
        print(f'Fill rate: {FILL_RATE*100:.0f}% of market SELL flow')
        print(f'Coins: {COINS}')
        print('=' * 60)

        startup_msg = (
            f'<b>Paper Trader Started</b>\n'
            f'Capital: ${CAPITAL_PER_SIDE}/side/coin\n'
            f'Fill rate: {FILL_RATE*100:.0f}% (back of queue)\n'
            f'Coins: {", ".join(c.upper() for c in COINS)}'
        )
        await self.send_telegram(startup_msg)

        # on startup, join current window if in first 10 min
        now = int(time.time())
        current_window = now - (now % 900)
        elapsed = now - current_window

        if elapsed < 600:
            print(f'[startup] joining current window ({elapsed}s in)')
            await self.run_window(current_window)

        while True:
            now = int(time.time())
            current_window = now - (now % 900)
            next_window = current_window + 900
            wait = next_window - now

            if wait > 5:
                await asyncio.sleep(wait - 5)

            await self.run_window(next_window)


if __name__ == '__main__':
    asyncio.run(PaperTrader().run())
