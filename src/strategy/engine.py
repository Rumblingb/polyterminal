import asyncio
import logging
from datetime import datetime
from typing import Callable

from src.config import config
from src.feeds.binance import BinanceWS
from src.feeds.chainlink import ChainlinkFetcher
from src.markets.clob import ClobClient
from src.markets.gamma import GammaClient, Market15m
from src.strategy.signals import Signal, SignalDirection

log = logging.getLogger(__name__)


class StrategyEngine:
    """
    main trading strategy engine
    monitors 15m windows and generates signals based on price movements
    """

    def __init__(
        self,
        binance: BinanceWS,
        chainlink: ChainlinkFetcher,
        gamma: GammaClient,
        clob: ClobClient,
    ):
        self.binance = binance
        self.chainlink = chainlink
        self.gamma = gamma
        self.clob = clob

        # active windows: coin -> Market15m
        self._active_markets: dict[str, Market15m] = {}
        # start prices for active windows: coin -> price
        self._start_prices: dict[str, float] = {}
        # signals already generated (to avoid duplicates): market_slug -> Signal
        self._generated_signals: dict[str, Signal] = {}

        # callback for signals
        self.on_signal: Callable[[Signal], None] | None = None

        self._running = False

    async def run(self):
        """main loop - check for signals on each price update"""
        self._running = True
        log.info("strategy engine started")

        while self._running:
            try:
                await self._check_signals()
                await asyncio.sleep(0.1)  # 100ms tick
            except Exception as e:
                log.error(f"strategy error: {e}")
                await asyncio.sleep(1)

    async def update_markets(self):
        """fetch and update active 15m markets"""
        markets = await self.gamma.fetch_15m_markets()
        now = datetime.utcnow()

        for market in markets:
            # only track markets that are currently in their window
            if market.is_active:
                if market.coin not in self._active_markets:
                    log.info(f"new active window: {market.coin} - {market.title}")
                    self._active_markets[market.coin] = market

                    # record start price from current binance price
                    price = self.binance.get_price(market.coin)
                    if price:
                        self._start_prices[market.coin] = price
                        log.info(f"start price for {market.coin}: ${price:.2f}")

        # remove expired markets
        expired = []
        for coin, market in self._active_markets.items():
            if now > market.end_time:
                log.info(f"window ended: {coin} - {market.title}")
                expired.append(coin)

        for coin in expired:
            del self._active_markets[coin]
            if coin in self._start_prices:
                del self._start_prices[coin]
            # clean up generated signals for this market
            slug = self._active_markets.get(coin, {})
            if isinstance(slug, Market15m):
                self._generated_signals.pop(slug.slug, None)

    async def _check_signals(self):
        """check for trading signals based on price movements"""
        for coin, market in self._active_markets.items():
            # skip if already generated signal for this market
            if market.slug in self._generated_signals:
                continue

            start_price = self._start_prices.get(coin)
            current_price = self.binance.get_price(coin)

            if not start_price or not current_price:
                continue

            pct_change = (current_price - start_price) / start_price

            # check if price moved enough to generate signal
            if abs(pct_change) >= config.entry_threshold:
                direction = SignalDirection.UP if pct_change > 0 else SignalDirection.DOWN
                token_id = (
                    market.up_token_id
                    if direction == SignalDirection.UP
                    else market.down_token_id
                )

                signal = Signal(
                    coin=coin,
                    direction=direction,
                    timestamp=datetime.utcnow(),
                    start_price=start_price,
                    current_price=current_price,
                    pct_change=pct_change,
                    market_slug=market.slug,
                    token_id=token_id,
                )

                log.info(
                    f"SIGNAL: {coin} {direction.value} | "
                    f"${start_price:.2f} -> ${current_price:.2f} ({pct_change*100:.2f}%)"
                )

                self._generated_signals[market.slug] = signal

                if self.on_signal:
                    self.on_signal(signal)

    def stop(self):
        self._running = False
