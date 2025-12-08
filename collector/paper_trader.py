#!/usr/bin/env python3
"""
Gabagool Paper Trader - runs alongside collector, sends Telegram updates
BTC + ETH only
"""

import asyncio
import json
import os
import time
from datetime import datetime
from dataclasses import dataclass

import aiohttp
import websockets

GAMMA_API = 'https://gamma-api.polymarket.com'
CLOB_WS = 'wss://ws-subscriptions-clob.polymarket.com/ws/market'

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')

COINS = ['btc', 'eth']
FILL_RATE = 0.10  # simulate 10% of retail flow


@dataclass
class Position:
    coin: str
    up_shares: float = 0
    up_cost: float = 0
    down_shares: float = 0
    down_cost: float = 0

    @property
    def matched(self):
        return min(self.up_shares, self.down_shares)

    @property
    def up_avg(self):
        return self.up_cost / self.up_shares if self.up_shares else 0

    @property
    def down_avg(self):
        return self.down_cost / self.down_shares if self.down_shares else 0

    @property
    def combined(self):
        if self.up_shares == 0 or self.down_shares == 0:
            return 1.0
        return self.up_avg + self.down_avg

    @property
    def edge(self):
        return 1.0 - self.combined

    @property
    def imbalance(self):
        total = self.up_shares + self.down_shares
        return abs(self.up_shares - self.down_shares) / total if total else 0

    def expected_pnl(self):
        matched_pnl = self.matched * self.edge
        unmatched_up = max(0, self.up_shares - self.down_shares)
        unmatched_down = max(0, self.down_shares - self.up_shares)
        unmatched_ev = unmatched_up * (0.5 - self.up_avg) + unmatched_down * (0.5 - self.down_avg)
        return matched_pnl + unmatched_ev


class PaperTrader:
    def __init__(self):
        self.tokens = {}
        self.positions = {}
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
        self.positions.clear()

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
                            self.positions[coin] = Position(coin=coin)

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

                        if data.get('event_type') != 'last_trade_price':
                            continue
                        if data.get('side') != 'SELL':
                            continue
                        if now >= accumulate_end:
                            continue

                        token_id = data.get('asset_id')
                        info = self.tokens.get(token_id)
                        if not info:
                            continue

                        coin, side = info
                        pos = self.positions.get(coin)
                        if not pos:
                            continue

                        price = float(data.get('price', 0))
                        size = float(data.get('size', 0))
                        fill = size * FILL_RATE

                        if side == 'up':
                            pos.up_shares += fill
                            pos.up_cost += fill * price
                        else:
                            pos.down_shares += fill
                            pos.down_cost += fill * price

                    except:
                        pass

        except Exception as e:
            print(f'[paper] ws error: {e}')

        # calculate results
        total_pnl = 0
        lines = [f'<b>📊 Window {dt.strftime("%H:%M")} UTC</b>\n']

        for coin, pos in sorted(self.positions.items()):
            if pos.up_shares == 0 and pos.down_shares == 0:
                continue

            pnl = pos.expected_pnl()
            total_pnl += pnl

            emoji = '🟢' if pnl > 0 else '🔴' if pnl < 0 else '⚪'
            lines.append(
                f'{emoji} <b>{coin.upper()}</b>: '
                f'↑{pos.up_shares:.0f} ↓{pos.down_shares:.0f} | '
                f'${pos.combined:.3f} | '
                f'{pos.edge*100:+.1f}% | '
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
