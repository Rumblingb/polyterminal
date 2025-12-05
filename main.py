import asyncio
import logging
import signal
import sys

from src.config import config
from src.feeds.binance import BinanceWS
from src.feeds.chainlink import ChainlinkFetcher
from src.markets.clob import ClobClient
from src.markets.gamma import GammaClient
from src.notifications.telegram import TelegramNotifier
from src.strategy.engine import StrategyEngine
from src.trading.paper import PaperTrader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


class TradingBot:
    def __init__(self):
        # data feeds
        self.binance = BinanceWS(on_price=self._on_price)
        self.chainlink = ChainlinkFetcher()

        # market data
        self.gamma = GammaClient()
        self.clob = ClobClient()

        # trading
        self.paper = PaperTrader(self.clob)

        # strategy
        self.engine = StrategyEngine(
            binance=self.binance,
            chainlink=self.chainlink,
            gamma=self.gamma,
            clob=self.clob,
        )

        # notifications
        self.telegram = TelegramNotifier()

        # wire up callbacks
        self.engine.on_signal = self._on_signal
        self.paper.on_entry = self._on_entry
        self.paper.on_exit = self._on_exit

        self._running = False

    def _on_price(self, coin: str, price: float):
        """callback for binance price updates"""
        # sync to chainlink tracker (as proxy for start prices)
        self.chainlink.set_price(coin, price)

    def _on_signal(self, signal):
        """callback for strategy signals"""
        asyncio.create_task(self.paper.handle_signal(signal))
        asyncio.create_task(self.telegram.signal_alert(signal))

    def _on_entry(self, trade):
        """callback for paper trade entries"""
        asyncio.create_task(self.telegram.entry_alert(trade))

    def _on_exit(self, trade):
        """callback for paper trade exits"""
        asyncio.create_task(self.telegram.exit_alert(trade))

    async def _market_poll_loop(self):
        """poll for new markets and check exits"""
        while self._running:
            try:
                await self.engine.update_markets()
                await self.paper.check_exits()
            except Exception as e:
                log.error(f"market poll error: {e}")

            await asyncio.sleep(config.poll_interval)

    async def _summary_loop(self):
        """send periodic summaries"""
        while self._running:
            await asyncio.sleep(3600)  # every hour
            try:
                summary = self.paper.get_summary()
                await self.telegram.summary_alert(summary)
            except Exception as e:
                log.error(f"summary error: {e}")

    async def run(self):
        """main entry point"""
        self._running = True

        log.info("=" * 50)
        log.info("POLYMARKET 15M TRADING BOT")
        log.info("=" * 50)
        log.info(f"Coins: {config.coins}")
        log.info(f"Position Size: ${config.position_size}")
        log.info(f"Entry Threshold: {config.entry_threshold*100:.2f}%")
        log.info(f"Mode: Paper Trading")
        log.info("=" * 50)

        await self.telegram.startup_alert()

        # fetch initial markets
        await self.engine.update_markets()

        # run all tasks
        try:
            await asyncio.gather(
                self.binance.run(),
                self.engine.run(),
                self._market_poll_loop(),
                self._summary_loop(),
            )
        except asyncio.CancelledError:
            log.info("shutting down...")
        finally:
            await self._cleanup()

    async def _cleanup(self):
        """cleanup on shutdown"""
        self._running = False
        self.binance.stop()
        self.engine.stop()

        # save trades
        self.paper.save_trades("paper_trades.json")

        # send final summary
        summary = self.paper.get_summary()
        await self.telegram.summary_alert(summary)
        log.info(summary)

        # close sessions
        await self.gamma.close()
        await self.clob.close()
        await self.chainlink.close()


def main():
    bot = TradingBot()

    # handle graceful shutdown
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def shutdown(sig, frame):
        log.info(f"received {sig}, shutting down...")
        for task in asyncio.all_tasks(loop):
            task.cancel()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        loop.run_until_complete(bot.run())
    except KeyboardInterrupt:
        log.info("interrupted")
    finally:
        loop.close()


if __name__ == "__main__":
    main()
