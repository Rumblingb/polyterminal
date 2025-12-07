"""
chainlink price feed helper
reads BTC/ETH/SOL prices from Polygon Chainlink feeds
no API key needed - public RPC
"""
from web3 import Web3
from datetime import datetime

POLYGON_RPC = "https://polygon-rpc.com"

# chainlink feed addresses on Polygon
FEEDS = {
    'btc': '0xc907E116054Ad103354f2D350FD2514433D57F6f',
    'eth': '0xF9680D99D6C9589e2a93a78A04A279e509205945',
    'sol': '0x10C8264C0935b3B9870013e057f330Ff3e9C56dC',
}

ABI = [
    {"inputs": [], "name": "latestRoundData", "outputs": [
        {"name": "roundId", "type": "uint80"},
        {"name": "answer", "type": "int256"},
        {"name": "startedAt", "type": "uint256"},
        {"name": "updatedAt", "type": "uint256"},
        {"name": "answeredInRound", "type": "uint80"}
    ], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "_roundId", "type": "uint80"}], "name": "getRoundData", "outputs": [
        {"name": "roundId", "type": "uint80"},
        {"name": "answer", "type": "int256"},
        {"name": "startedAt", "type": "uint256"},
        {"name": "updatedAt", "type": "uint256"},
        {"name": "answeredInRound", "type": "uint80"}
    ], "stateMutability": "view", "type": "function"},
]

class ChainlinkFeeds:
    def __init__(self, rpc_url=POLYGON_RPC):
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.feeds = {}
        self.decimals = {}
        
        for coin, addr in FEEDS.items():
            self.feeds[coin] = self.w3.eth.contract(address=addr, abi=ABI)
            self.decimals[coin] = self.feeds[coin].functions.decimals().call()
    
    def get_price(self, coin: str) -> tuple[float, int, datetime]:
        """get latest price, round_id, and update time"""
        coin = coin.lower()
        if coin not in self.feeds:
            raise ValueError(f"unsupported coin: {coin}")
        
        data = self.feeds[coin].functions.latestRoundData().call()
        price = data[1] / (10 ** self.decimals[coin])
        updated = datetime.fromtimestamp(data[3])
        return price, data[0], updated
    
    def get_price_at_round(self, coin: str, round_id: int) -> tuple[float, datetime]:
        """get historical price at specific round"""
        coin = coin.lower()
        if coin not in self.feeds:
            raise ValueError(f"unsupported coin: {coin}")
        
        data = self.feeds[coin].functions.getRoundData(round_id).call()
        price = data[1] / (10 ** self.decimals[coin])
        updated = datetime.fromtimestamp(data[3])
        return price, updated

if __name__ == "__main__":
    feeds = ChainlinkFeeds()
    
    for coin in ['btc', 'eth', 'sol']:
        price, round_id, updated = feeds.get_price(coin)
        lag = (datetime.now() - updated).seconds
        print(f"{coin.upper()}: ${price:,.2f} (round={round_id}, {lag}s ago)")
