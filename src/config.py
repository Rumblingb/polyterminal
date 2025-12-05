import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")


@dataclass
class Config:
    # polymarket credentials
    private_key: str = os.getenv("PRIVATE_KEY", "")
    poly_address: str = os.getenv("POLY_ADDRESS", "")
    poly_api_key: str = os.getenv("POLY_API_KEY", "")
    poly_api_secret: str = os.getenv("POLY_API_SECRET", "")
    poly_passphrase: str = os.getenv("POLY_PASSPHRASE", "")

    # telegram
    telegram_token: str = os.getenv("TELEGRAM_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # api endpoints
    gamma_api: str = "https://gamma-api.polymarket.com"
    clob_api: str = "https://clob.polymarket.com"
    binance_ws: str = "wss://stream.binance.com:9443/ws"

    # trading params
    entry_threshold: float = 0.001  # 0.1% price move to trigger
    exit_price: float = 0.90  # exit when poly price hits 90c
    position_size: float = 75.0  # $75 per trade
    max_slippage: float = 0.02  # 2 cents max slippage

    # coins to trade
    coins: tuple = ("BTC", "ETH", "SOL", "XRP")

    # binance symbols
    binance_symbols: dict = None

    # 15m market tag id
    tag_15m: int = 102467

    # poll interval for new markets (seconds)
    poll_interval: int = 60

    def __post_init__(self):
        self.binance_symbols = {
            "BTC": "btcusdt",
            "ETH": "ethusdt",
            "SOL": "solusdt",
            "XRP": "xrpusdt",
        }


config = Config()
