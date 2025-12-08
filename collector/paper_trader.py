#!/usr/bin/env python3
"""
Gabagool Paper Trader - tracks real order book, calculates edge
BTC + ETH only
"""

import asyncio
import json
import os
import time
from datetime import datetime
from dataclasses import dataclass, field

import aiohttp
import websockets

GAMMA_API = 'https://gamma-api.polymarket.com'
CLOB_WS = 'wss://ws-subscriptions-clob.polymarket.com/ws/market'

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')

COINS = ['btc', 'eth']


@dataclass
class CoinBook:
    coin: str
    up_bid: float = 0
    up_ask: float = 1
    down_bid: float = 0
    down_ask: float = 1
    up_fills: list = field(default_factory=list)
    down_fills: list = field(default_factory=list)

    @property
    def combined_bid(self):
        return self.up_bid + self.down_bid

    @property
    def edge(self):
        return 1.0 - self.combined_bid if self.combined_bid < 1 else 0

    def add_fill(self, side: str, price: float, size: float):
        if side == 'up':
            self.up_fills.append((price, size))
        else:
            self.down_fills.append((price, size))

    def calc_pnl(self):
        up_shares = sum(s for _, s in self.up_fills)
        down_shares = sum(s for _, s in self.down_fills)
        up_cost = sum(p * s for p, s in self.up_fills)
        down_cost = sum(p * s for p, s in self.down_fills)

        if up_shares == 0 or down_shares == 0:
            return 0, 0, 0, 1.0

        matched = min(up_shares, down_shares)
        up_avg = up_cost / up_shares
        down_avg = down_cost / down_shares
        combined = up_avg + down_avg
        edge = 1.0 - combined

        matched_pnl = matched * edge
        unmatched_up = max(0, up_shares - down_shares)
        unmatched_down = max(0, down_shares - up_shares)
        unmatched_ev = unmatched_up * (0.5 - up_avg) + unmatched_down * (0.5 - down_avg)

        return matched_pnl + unmatched_ev, up_shares, down_shares, combined


class PaperTrader:
    def __init__(self):
        self.tokens = {}
        self.books = {}
        self.results = []
        self.session_pnl = 0

    async def send_telegram(self, msg: str):
        if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
            print(f'[tg] {msg}')
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
        accumulate_end = window_ts + 540

        dt = datetime.utcfromtimestamp(window_ts)
        print(f'\n[paper] window {dt.strftime("%H:%M")} UTC')

        await self.fetch_markets(window_ts)

        if not self.tokens:
            return

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

                        # track order book
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

                        # track fills (SELL = retail selling to us)
                        elif event_type == 'last_trade_price':
                            if data.get('side') != 'SELL':
                                continue
                            if now >= accumulate_end:
                                continue

                            info = self.tokens.get(data.get('asset_id'))
                            if not info:
                                continue

                            coin, side = info
                            book = self.books.get(coin)
                            if not book:
                                continue

                            price = float(data.get('price', 0))
                            size = float(data.get('size', 0))
                            book.add_fill(side, price, size)

                    except:
                        pass

        except Exception as e:
            print(f'[paper] ws error: {e}')

        # calculate results
        total_pnl = 0
        lines = [f'<b>📊 Window {dt.strftime("%H:%M")} UTC</b>\n']

        for coin, book in sorted(self.books.items()):
            pnl, up_shares, down_shares, combined = book.calc_pnl()

            if up_shares == 0 and down_shares == 0:
                # no fills, just show book state
                lines.append(
                    f'⚪ <b>{coin.upper()}</b>: no fills | '
                    f'book ${book.combined_bid:.3f} ({book.edge*100:+.1f}%)'
                )
                continue

            total_pnl += pnl
            edge = 1.0 - combined

            emoji = '🟢' if pnl > 0 else '🔴' if pnl < 0 else '⚪'
            lines.append(
                f'{emoji} <b>{coin.upper()}</b>: '
                f'↑{up_shares:.0f} ↓{down_shares:.0f} | '
                f'${combined:.3f} ({edge*100:+.1f}%) | '
                f'<b>${pnl:+.2f}</b>'
            )

        self.session_pnl += total_pnl
        self.results.append({'window': window_ts, 'pnl': total_pnl})

        emoji = '✅' if total_pnl > 0 else '❌' if total_pnl < 0 else '➖'
        lines.append(f'\n{emoji} <b>Window: ${total_pnl:+.2f}</b>')
        lines.append(f'📈 Session: ${self.session_pnl:+.2f} ({len(self.results)} windows)')

        msg = '\n'.join(lines)
        await self.send_telegram(msg)
        print(f'[paper] pnl=${total_pnl:+.2f} session=${self.session_pnl:+.2f}')

    async def run(self):
        print('=' * 50)
        print('GABAGOOL PAPER TRADER')
        print(f'Coins: {COINS}')
        print(f'Telegram: {"enabled" if TELEGRAM_TOKEN else "disabled"}')
        print('=' * 50)

        await self.send_telegram('🚀 <b>Paper Trader Started</b>\nCoins: BTC, ETH\nFill rate: 10%')

        while True:
            now = int(time.time())
            current = now - (now % 900)
            next_window = current + 900
            wait = next_window - now

            if wait > 5:
                await asyncio.sleep(wait - 5)

            await self.run_window(next_window)


if __name__ == '__main__':
    asyncio.run(PaperTrader().run())
