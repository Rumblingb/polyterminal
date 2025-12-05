import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Callable

from src.config import config
from src.markets.clob import ClobClient
from src.strategy.signals import Signal, SignalDirection

log = logging.getLogger(__name__)


@dataclass
class PaperTrade:
    """represents a paper trade"""

    signal: Signal
    entry_price: float  # polymarket price we "bought" at
    entry_size: float  # position size in shares
    entry_cost: float  # total cost
    entry_time: datetime
    exit_price: float | None = None
    exit_time: datetime | None = None
    pnl: float = 0.0
    closed: bool = False

    def close(self, exit_price: float):
        self.exit_price = exit_price
        self.exit_time = datetime.utcnow()
        # pnl = (exit - entry) * size
        self.pnl = (exit_price - self.entry_price) * self.entry_size
        self.closed = True


@dataclass
class PaperStats:
    """paper trading statistics"""

    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    total_volume: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    win_rate: float = 0.0
    trades: list[dict] = field(default_factory=list)


class PaperTrader:
    """
    simulates trades without real execution
    tracks positions and calculates paper P&L
    """

    def __init__(self, clob: ClobClient):
        self.clob = clob
        self._positions: dict[str, PaperTrade] = {}  # market_slug -> trade
        self._closed_trades: list[PaperTrade] = []
        self._stats = PaperStats()

        # callbacks
        self.on_entry: Callable[[PaperTrade], None] | None = None
        self.on_exit: Callable[[PaperTrade], None] | None = None

    async def handle_signal(self, signal: Signal):
        """handle a trading signal - simulate entry"""
        # skip if already have position in this market
        if signal.market_slug in self._positions:
            log.debug(f"already have position in {signal.market_slug}")
            return

        # get current order book to simulate fill
        book = await self.clob.fetch_book(signal.token_id)
        if not book:
            log.error(f"could not fetch book for {signal.token_id}")
            return

        # simulate market buy at best ask
        entry_price = book.best_ask
        if entry_price > 1 - config.max_slippage:
            log.warning(f"price too high: {entry_price}, skipping")
            return

        # calculate position size (shares = dollars / price)
        entry_size = config.position_size / entry_price
        entry_cost = config.position_size

        trade = PaperTrade(
            signal=signal,
            entry_price=entry_price,
            entry_size=entry_size,
            entry_cost=entry_cost,
            entry_time=datetime.utcnow(),
        )

        self._positions[signal.market_slug] = trade
        self._stats.total_trades += 1
        self._stats.total_volume += entry_cost

        log.info(
            f"PAPER ENTRY: {signal.coin} {signal.direction.value} | "
            f"{entry_size:.2f} shares @ ${entry_price:.2f} = ${entry_cost:.2f}"
        )

        if self.on_entry:
            self.on_entry(trade)

    async def check_exits(self):
        """check open positions for exit conditions"""
        closed = []

        for slug, trade in self._positions.items():
            book = await self.clob.fetch_book(trade.signal.token_id)
            if not book:
                continue

            # exit if price hits target
            if book.best_bid >= config.exit_price:
                trade.close(book.best_bid)
                closed.append(slug)

                self._record_closed_trade(trade)

                log.info(
                    f"PAPER EXIT: {trade.signal.coin} | "
                    f"${trade.entry_price:.2f} -> ${trade.exit_price:.2f} | "
                    f"P&L: ${trade.pnl:.2f}"
                )

                if self.on_exit:
                    self.on_exit(trade)

        for slug in closed:
            del self._positions[slug]

    async def close_expired(self, market_slug: str, final_price: float):
        """force close a position when market expires"""
        if market_slug not in self._positions:
            return

        trade = self._positions[market_slug]
        trade.close(final_price)
        self._record_closed_trade(trade)

        log.info(
            f"PAPER EXPIRED: {trade.signal.coin} | "
            f"${trade.entry_price:.2f} -> ${final_price:.2f} | "
            f"P&L: ${trade.pnl:.2f}"
        )

        if self.on_exit:
            self.on_exit(trade)

        del self._positions[market_slug]

    def _record_closed_trade(self, trade: PaperTrade):
        """record trade in stats"""
        self._closed_trades.append(trade)
        self._stats.total_pnl += trade.pnl

        if trade.pnl > 0:
            self._stats.winning_trades += 1
        else:
            self._stats.losing_trades += 1

        # update averages
        wins = [t for t in self._closed_trades if t.pnl > 0]
        losses = [t for t in self._closed_trades if t.pnl <= 0]

        self._stats.avg_win = sum(t.pnl for t in wins) / len(wins) if wins else 0
        self._stats.avg_loss = sum(t.pnl for t in losses) / len(losses) if losses else 0
        self._stats.win_rate = (
            self._stats.winning_trades / len(self._closed_trades)
            if self._closed_trades
            else 0
        )

    def get_stats(self) -> PaperStats:
        """get current paper trading stats"""
        return self._stats

    def get_summary(self) -> str:
        """get formatted summary string"""
        s = self._stats
        return (
            f"Paper Trading Summary\n"
            f"---------------------\n"
            f"Total Trades: {s.total_trades}\n"
            f"Win/Loss: {s.winning_trades}/{s.losing_trades}\n"
            f"Win Rate: {s.win_rate*100:.1f}%\n"
            f"Total P&L: ${s.total_pnl:.2f}\n"
            f"Avg Win: ${s.avg_win:.2f}\n"
            f"Avg Loss: ${s.avg_loss:.2f}\n"
            f"Volume: ${s.total_volume:.2f}"
        )

    def save_trades(self, path: Path | str = "paper_trades.json"):
        """save trades to json file"""
        trades = []
        for trade in self._closed_trades:
            trades.append(
                {
                    "coin": trade.signal.coin,
                    "direction": trade.signal.direction.value,
                    "entry_price": trade.entry_price,
                    "exit_price": trade.exit_price,
                    "entry_size": trade.entry_size,
                    "pnl": trade.pnl,
                    "entry_time": trade.entry_time.isoformat(),
                    "exit_time": trade.exit_time.isoformat() if trade.exit_time else None,
                    "start_price": trade.signal.start_price,
                    "current_price": trade.signal.current_price,
                    "pct_change": trade.signal.pct_change,
                }
            )

        with open(path, "w") as f:
            json.dump({"trades": trades, "stats": asdict(self._stats)}, f, indent=2, default=str)

        log.info(f"saved {len(trades)} trades to {path}")
