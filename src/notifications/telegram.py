import logging
from typing import Any

from telegram import Bot
from telegram.error import TelegramError

from src.config import config
from src.strategy.signals import Signal, SignalDirection
from src.trading.paper import PaperTrade

log = logging.getLogger(__name__)


class TelegramNotifier:
    """send trading alerts via telegram"""

    def __init__(self):
        self.bot: Bot | None = None
        self.chat_id: str = config.telegram_chat_id
        self._enabled = bool(config.telegram_token and config.telegram_chat_id)

        if self._enabled:
            self.bot = Bot(token=config.telegram_token)
            log.info("telegram notifier enabled")
        else:
            log.warning("telegram not configured, notifications disabled")

    async def send(self, message: str):
        """send a message to telegram"""
        if not self._enabled or not self.bot:
            log.debug(f"telegram disabled, would send: {message}")
            return

        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode="HTML",
            )
        except TelegramError as e:
            log.error(f"telegram error: {e}")

    async def signal_alert(self, signal: Signal):
        """send alert for new trading signal"""
        emoji = "🟢" if signal.direction == SignalDirection.UP else "🔴"
        direction = signal.direction.value

        msg = (
            f"{emoji} <b>SIGNAL: {signal.coin} {direction}</b>\n"
            f"Price: ${signal.start_price:.2f} → ${signal.current_price:.2f}\n"
            f"Change: {signal.pct_change*100:+.2f}%\n"
            f"Edge: {signal.edge*100:.2f}%"
        )
        await self.send(msg)

    async def entry_alert(self, trade: PaperTrade):
        """send alert for trade entry"""
        emoji = "🟢" if trade.signal.direction == SignalDirection.UP else "🔴"
        direction = trade.signal.direction.value

        msg = (
            f"{emoji} <b>PAPER ENTRY: {trade.signal.coin} {direction}</b>\n"
            f"Size: {trade.entry_size:.2f} shares @ ${trade.entry_price:.2f}\n"
            f"Cost: ${trade.entry_cost:.2f}"
        )
        await self.send(msg)

    async def exit_alert(self, trade: PaperTrade):
        """send alert for trade exit"""
        pnl_emoji = "✅" if trade.pnl > 0 else "❌"

        msg = (
            f"{pnl_emoji} <b>PAPER EXIT: {trade.signal.coin}</b>\n"
            f"Entry: ${trade.entry_price:.2f} → Exit: ${trade.exit_price:.2f}\n"
            f"P&L: <b>${trade.pnl:+.2f}</b>"
        )
        await self.send(msg)

    async def summary_alert(self, summary: str):
        """send paper trading summary"""
        msg = f"📊 <b>Paper Trading Summary</b>\n<pre>{summary}</pre>"
        await self.send(msg)

    async def startup_alert(self):
        """send alert when bot starts"""
        msg = (
            "🚀 <b>Polymarket Bot Started</b>\n"
            f"Coins: {', '.join(config.coins)}\n"
            f"Position Size: ${config.position_size}\n"
            f"Entry Threshold: {config.entry_threshold*100:.2f}%\n"
            f"Mode: Paper Trading"
        )
        await self.send(msg)
