# Bot Build Prompt

You are building a Polymarket 15-minute binary options market making bot. Below are factual findings from analyzing a profitable trader's on-chain data.

---

## APIs Available

### Market Discovery
```
GET https://gamma-api.polymarket.com/events?tag_id=102467&closed=false&limit=20
```
Returns 15m crypto markets. Parse `slug` with regex `(btc|eth|sol|xrp).*15m-(\d+)` to get coin and window timestamp.

### Orderbook (REST)
```
GET https://clob.polymarket.com/book?token_id={token}
```
Returns `bids` and `asks` arrays with `price` and `size`.

---

## WebSocket APIs

### 1. CLOB Market WebSocket (Orderbook)
```
wss://ws-subscriptions-clob.polymarket.com/ws/market
```

Subscribe:
```json
{"type": "subscribe", "channel": "market", "assets_ids": ["<up_token>", "<down_token>"]}
```

Message types:
- `book` - Full orderbook snapshot (on subscribe, periodic)
- `price_change` - Real-time updates with `best_bid`, `best_ask`
- `last_trade_price` - Trade executions

Example `price_change`:
```json
{
  "event_type": "price_change",
  "price_changes": [
    {
      "asset_id": "601840759...",
      "price": "0.55",
      "size": "100",
      "side": "BUY",
      "best_bid": "0.55",
      "best_ask": "0.56"
    }
  ],
  "timestamp": "1765138692351"
}
```

Notes:
- First message may be a list `[{...}]` not dict
- `book` messages can have stale prices - use `price_change` for real-time
- Message frequency: ~10-50/sec during active trading

### 2. Real Time Data Socket (RTDS)
```
wss://ws-live-data.polymarket.com
```

Subscribe:
```json
{
  "action": "subscribe",
  "subscriptions": [
    {
      "topic": "topic_name",
      "type": "*",
      "filters": ""
    }
  ]
}
```

Unsubscribe:
```json
{
  "action": "unsubscribe",
  "subscriptions": [{"topic": "topic_name", "type": "*"}]
}
```

Connection management:
- Send PING every 5 seconds to maintain connection
- Supports dynamic subscribe/unsubscribe without reconnect

Available topics:

**crypto_prices (Binance)**
```json
{"action": "subscribe", "subscriptions": [{"topic": "crypto_prices", "type": "*", "filters": ""}]}
```
Response payload: `{"symbol": "btcusdt", "timestamp": 1753314064213, "value": 91520.00}`

**crypto_prices_chainlink (Resolution oracle)**
```json
{"action": "subscribe", "subscriptions": [{"topic": "crypto_prices_chainlink", "type": "*", "filters": ""}]}
```
Response payload: `{"symbol": "btc/usd", "timestamp": 1753314064213, "value": 91523.45}`
Symbols: `btc/usd`, `eth/usd`, `sol/usd`, `xrp/usd`

Authentication (if needed):
```json
{
  "clob_auth": {"key": "api_key", "secret": "api_secret", "passphrase": "api_passphrase"},
  "gamma_auth": {"address": "wallet_address"}
}
```

### 3. Chainlink On-Chain (Direct Read)

Polygon contract addresses:
| Coin | Address |
|------|---------|
| BTC | `0xc907E116054Ad103354f2D350FD2514433D57F6f` |
| ETH | `0xF9680D99D6C9589e2a93a78A04A279e509205945` |
| SOL | `0x10C8264C0935b3B9870013e057f330Ff3e9C56dC` |

RPC: `https://polygon-rpc.com`

Call `latestRoundData()` returns: `(roundId, answer, startedAt, updatedAt, answeredInRound)`
Price = `answer / 1e8`

**Critical:** Polymarket resolves using Chainlink, not Binance. Oracle mismatch = loss even when "right".

---

## Analyzed Trader Data (20,000 trades)

### Trade Role Distribution
| Role | Side | Count | Percentage |
|------|------|-------|------------|
| Maker | BUY | 10,000 | 50% |
| Maker | SELL | 0 | 0% |
| Taker | BUY | 1,846 | 9% |
| Taker | SELL | 8,154 | 41% |

### Maker Buy Timing (by minute within 15m window)
```
min 0:  951 fills
min 1:  935 fills
min 2:  950 fills
min 3:  948 fills
min 4:  698 fills
min 5:  651 fills
min 6:  755 fills
min 7:  596 fills
min 8:  703 fills
min 9:  642 fills
min 10: 729 fills
min 11: 432 fills
min 12: 434 fills
min 13: 274 fills
min 14: 302 fills
```

### Taker Sell Timing (exits/rebalancing)
```
min 0:  1021 sells
min 1:  861 sells
min 2:  804 sells
min 3:  739 sells
min 4:  764 sells
min 5:  684 sells
min 6:  717 sells
min 7:  472 sells
min 8:  522 sells
min 9:  368 sells
min 10: 482 sells
min 11: 251 sells
min 12: 194 sells
min 13: 158 sells
min 14: 117 sells
```

### Taker Buy Timing (rebalancing)
```
min 0:  91 buys
min 1:  123 buys
min 2:  152 buys
min 3:  138 buys
min 4:  174 buys
min 5:  202 buys
min 6:  196 buys
min 7:  158 buys
min 8:  133 buys
min 9:  114 buys
min 10: 150 buys
min 11: 62 buys
min 12: 48 buys
min 13: 44 buys
min 14: 61 buys
```

### Maker Buy Price Distribution
| Price Range | Fills | Percentage | Volume |
|-------------|-------|------------|--------|
| $0.00-0.30 | 2,107 | 21.1% | $3,662 |
| $0.30-0.40 | 1,415 | 14.1% | $4,555 |
| $0.40-0.50 | 1,870 | 18.7% | $8,282 |
| $0.50-0.60 | 1,692 | 16.9% | $9,243 |
| $0.60-0.70 | 1,291 | 12.9% | $8,422 |
| $0.70-0.80 | 976 | 9.8% | $7,572 |
| $0.80-0.90 | 416 | 4.2% | $3,680 |
| $0.90-1.00 | 233 | 2.3% | $2,336 |

### Top Bid Price Levels (most fills)
| Price | Fills | Volume |
|-------|-------|--------|
| $0.54 | 240 | $1,338 |
| $0.44 | 239 | $1,044 |
| $0.45 | 230 | $1,061 |
| $0.55 | 221 | $1,293 |
| $0.43 | 218 | $892 |
| $0.46 | 215 | $1,022 |
| $0.37 | 209 | $712 |
| $0.47 | 207 | $944 |
| $0.50 | 197 | $998 |
| $0.56 | 192 | $1,081 |

### Order Sizing
| Share Range | Fills | Percentage |
|-------------|-------|------------|
| 0-5 shares | 1,980 | 19.8% |
| 5-10 shares | 2,498 | 25.0% |
| 10-15 shares | 2,847 | 28.5% |
| 15-20 shares | 2,675 | 26.8% |

Average: 9.8 shares per fill
Median: 10.7 shares per fill
Max: 16.0 shares per fill

### Position Balance (across 20 windows analyzed)
| Metric | Value |
|--------|-------|
| Avg position per side | 1,308 shares |
| Avg imbalance | 102 shares |
| Imbalance as % of position | 7.8% |
| Windows with <20% imbalance | 95% |

### Execution Speed
- Max fills in one second: 37
- Fills occurring in same second as another: 7,608 (76%)

### Combined Price Analysis (UP_bid + DOWN_bid)
| Range | Pair Trades | Percentage |
|-------|-------------|------------|
| 0.80-0.90 | 88 | 8.5% |
| 0.90-0.95 | 79 | 7.6% |
| 0.95-0.98 | 157 | 15.2% |
| 0.98-1.00 | 198 | 19.1% |
| 1.00-1.02 | 60 | 5.8% |
| 1.02-1.10 | 80 | 7.7% |

Pairs with combined < 1.00: 66.2%
Average combined when < 1.00: 0.9738

### Profit Metrics (from sample)
| Metric | Value |
|--------|-------|
| Avg shares per window | 3,339 |
| Estimated profit per window | $54 |
| Windows per hour | 4 |
| Trader's total profit | $191,356 |
| Trader's total volume | $21.2M |

---

## Existing Code in Repo

### `/Users/ishan/Desktop/polyterminal/main.py`
Production recorder - polls CLOB API, streams Binance, stores to Supabase.

### `/Users/ishan/Desktop/polyterminal/chainlink.py`
Reads Chainlink oracle prices from Polygon. Class `ChainlinkFeeds` with `get_price(coin)`.

### `/Users/ishan/Desktop/polyterminal/arb_monitor.py`
WebSocket monitor for real-time orderbook. Connects, subscribes, tracks best bid/ask.

### `/Users/ishan/Desktop/polyterminal/scripts/fetch_wallet.py`
Fetches trade history from Polymarket subgraph for any wallet.

### `/Users/ishan/Desktop/polyterminal/scripts/analyze_gabagool.py`
Analysis script that produced the data above.

---

## Environment

- Python 3.11 with venv at `.venv/`
- Supabase credentials in `.env` (SUPABASE_URL, SUPABASE_KEY)
- Dependencies: aiohttp, websockets, web3, requests

---

## Raw Data Files

- `data/gabagool_raw_trades.json` - 20,000 decoded trades
- `data/gabagool_activity.json` - 110,125 lines activity data
- `data/gabagool_trades.json` - empty (failed fetch)

---

## Binary Market Mechanics

- Each 15m window has UP token and DOWN token
- At resolution, winning side pays $1.00 per share, losing side pays $0
- UP wins if spot price at minute 15 >= spot price at minute 0
- DOWN wins if spot price at minute 15 < spot price at minute 0
- Tokens discovered via Gamma API, `clobTokenIds` field contains [up_token, down_token]

---

## What the Analyzed Trader Does

1. Posts limit BUY orders on both UP and DOWN tokens
2. All 10,000 maker orders are buys, zero maker sells
3. Activity concentrated in minutes 0-4
4. Maintains position balance within ~10%
5. Uses taker orders to rebalance (18% taker buys, 82% taker sells)
6. Bids concentrated at $0.40-0.60 price range
7. Order sizes typically 10-16 shares
8. 95% of windows end with balanced UP/DOWN positions
