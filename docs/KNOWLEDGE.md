# Polymarket 15m Binary Options - Knowledge Base

## API Endpoints

### Polymarket Gamma API (market discovery)
```
Base: https://gamma-api.polymarket.com

GET /events?tag_id=102467&closed=false&limit=10
- tag_id=102467 is crypto 15m markets
- returns events with markets array
- each market has clobTokenIds: [UP_token, DOWN_token]
- slug format (open): btc-updown-15m-{unix_timestamp}
- slug format (closed): btc-up-or-down-15m-{unix_timestamp}
```

### Polymarket CLOB API (order book)
```
Base: https://clob.polymarket.com

GET /book?token_id={token}
- returns { bids: [{price, size}], asks: [{price, size}] }
- bids sorted low->high, asks sorted low->high
- best bid = max(bids), best ask = min(asks)
- prices are 0-1 (probability/cents)

GET /prices-history?market={token}&startTs={ts}&endTs={ts}&fidelity=1
- historical price data
- fidelity=1 gives finest granularity
- returns { history: [{t: timestamp, p: price}] }
```

### Polymarket WebSocket (UNRELIABLE)
```
wss://ws-subscriptions-clob.polymarket.com/ws/market

Subscribe msg:
{
  "auth": {},
  "markets": [],
  "assets_ids": [token_id],
  "type": "market"
}

Events: book, price_change, last_trade_price

PROBLEM: WebSocket sends stale data. Shows 0.49/0.51 constantly
even when real price is 0.10 or 0.90. Only occasionally flashes
correct price. DO NOT USE FOR LIVE TRADING.

WORKAROUND: Poll REST /book endpoint every 500ms instead.
```

### Binance API
```
WebSocket: wss://stream.binance.com:9443/ws/btcusdt@trade
- real-time trades
- msg format: { p: "price_string", ... }
- very fast, <50ms latency

REST Klines:
GET https://api.binance.com/api/v3/klines
?symbol=BTCUSDT&interval=1m&startTime={ms}&limit=1
- returns [[open_time, open, high, low, close, ...]]
- use to get BTC price at window start timestamp
```

## Market Structure

### 15-Minute Windows
- new window every 15 min on :00, :15, :30, :45
- window timestamp in slug is START time (unix seconds)
- end_ts = start_ts + 900
- UP wins if BTC price at end > BTC price at start
- DOWN wins if BTC price at end <= BTC price at start
- winner pays $1.00, loser pays $0.00

### Tokens
- each market has 2 tokens: UP and DOWN
- UP + DOWN prices should = ~1.00 (minus spread)
- clobTokenIds[0] = UP token
- clobTokenIds[1] = DOWN token

### Liquidity
- spread typically 1-3 cents
- MMs actively quote, respond to BTC moves in 3-6 seconds
- thin books - order sizes often $50-500
- late in window, spread can blow out to 10c+

## Observed Price Behavior

### MM Response Time
- MMs watch Binance in real-time
- reprice within 3-6 seconds of BTC move
- faster on large moves (>0.1%)
- sometimes pull quotes entirely during volatility

### Price Correlation to BTC
```
BTC move    | Typical UP price
------------|------------------
+0.00-0.05% | 0.50-0.55
+0.05-0.10% | 0.60-0.75
+0.10-0.20% | 0.75-0.90
+0.20%+     | 0.90-0.98
```

### Late Window Behavior
- last 3 min: prices converge toward 0 or 1
- last 1 min: can see 0.95+ or 0.05-
- spread widens significantly
- reversals still happen but less likely

## Strategy Learnings

### What Doesn't Work
1. **Speed arbitrage** - MMs are faster, have better infra
2. **BTC/ETH correlation trading** - divergences are real, not mispricing
   - correlation only ~0.46
   - same outcome rate ~79%
   - all pair strategies backtested negative
3. **Simple threshold signals** - by the time BTC moves 0.08%,
   MMs have already repriced

### What Might Work
1. **Late window momentum** - when outcome nearly certain (0.85+)
   with <5min left, buy winner side
2. **Mean reversion** - extreme prices (0.20 or 0.80) sometimes
   revert if BTC bounces
3. **Volatility plays** - high BTC volatility = more trading opps

### Backtest Results (from zero_assumptions.py)
- Poly prices are reasonably calibrated (0.70 price = ~70% win rate)
- Edge appears in specific time+price buckets
- Best entries: late window (9-12min elapsed) when price 0.75+
- Slippage kills most strategies (3c each way = 6c round trip)

## Code Patterns

### Async Bot Structure
```python
async def run(self):
    await asyncio.gather(
        self.binance_feed(),   # WS for BTC price
        self.poly_feed(),      # REST poll for book
        self.strategy_loop(),  # check signals
        self.reporter(),       # log status
        self.window_manager()  # refresh window every 15m
    )
```

### REST Polling (reliable)
```python
async def poly_feed(self):
    async with aiohttp.ClientSession() as session:
        while True:
            url = f"{CLOB_API}/book?token_id={self.window.token_up}"
            async with session.get(url) as resp:
                data = await resp.json()
                bids = data.get("bids", [])
                asks = data.get("asks", [])
                if bids:
                    self.poly_bid = max(float(b["price"]) for b in bids)
                if asks:
                    self.poly_ask = min(float(a["price"]) for a in asks)
            await asyncio.sleep(0.5)
```

### Window Detection
```python
# regex for active windows
match = re.search(r'15m-(\d+)', slug)
start_ts = int(match.group(1))
end_ts = start_ts + 900

# check if active
now = int(time.time())
if start_ts <= now <= end_ts:
    # window is live
```

### Startup Race Condition Fix
```python
def check_signal(self):
    if not self.window or not self.window.start_btc:
        return None
    if self.btc_price == 0:  # binance not connected yet
        return None
    # ... rest of signal logic
```

## Gotchas

1. **WebSocket data is stale** - use REST polling
2. **print() buffers** - use flush=True or custom log()
3. **Closed market slugs differ** - btc-up-or-down vs btc-updown
4. **Token order matters** - clobTokenIds[0]=UP, [1]=DOWN
5. **Binance klines use milliseconds** - multiply timestamp by 1000
6. **aiohttp sessions** - reuse sessions, don't create per request
7. **Window start_btc race** - manager may not fetch before strategy runs

## File Structure
```
polyterminal/
├── bot.py              # main paper trading bot
├── backtest.py         # historical backtest
├── btc_eth_corr.py     # cross-asset correlation analysis
├── hft_momentum.py     # momentum scalping backtest
├── zero_assumptions.py # empirical analysis, no assumptions
├── tick_recorder.py    # record tick data to JSON
├── time_analysis.py    # time-based pattern analysis
└── data/               # saved tick data, analysis results
```

## Dependencies
```toml
# pyproject.toml
dependencies = [
    "websockets>=12.0",
    "aiohttp>=3.9.0",
]
```

Run with: `uv run python bot.py`
