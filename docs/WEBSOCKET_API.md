# Polymarket WebSocket APIs

## Overview

Three websocket feeds available:
1. **CLOB Market** - orderbook and price updates
2. **Real Time Data Socket (RTDS)** - crypto prices, comments, activity
3. **Chainlink On-Chain** - resolution oracle (direct Polygon read)

---

# 1. CLOB Market WebSocket

## Connection

```
wss://ws-subscriptions-clob.polymarket.com/ws/market
```

## Subscription

Send JSON after connecting:

```json
{
  "type": "subscribe",
  "channel": "market",
  "assets_ids": ["<up_token>", "<down_token>"]
}
```

## Message Types

### 1. `book` - Full Orderbook Snapshot

Sent on first subscribe and periodically.

```json
{
  "event_type": "book",
  "asset_id": "601840759...",
  "market": "0x000a551f...",
  "bids": [
    {"price": "0.54", "size": "100"},
    {"price": "0.53", "size": "200"}
  ],
  "asks": [
    {"price": "0.56", "size": "150"},
    {"price": "0.57", "size": "300"}
  ],
  "timestamp": "1765138692000"
}
```

### 2. `price_change` - Real-time Updates

Sent on every order/cancel.

```json
{
  "event_type": "price_change",
  "market": "0x000a551f...",
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

### 3. `last_trade_price` - Trade Execution

```json
{
  "event_type": "last_trade_price",
  "asset_id": "601840759...",
  "price": "0.456",
  "size": "219.21",
  "side": "BUY",
  "timestamp": "1750428146322"
}
```

## Arbitrage Detection

For binary markets (UP/DOWN), check:

```python
combined = up_best_ask + down_best_ask

if combined < 1.00:
    edge = 1 - combined  # profit per $1
    # BUY both sides
```

Example:
- UP ask: $0.48
- DOWN ask: $0.51
- Combined: $0.99
- Edge: 1% guaranteed profit

## Python Example

```python
import asyncio
import json
import websockets

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

async def monitor_arb(up_token, down_token):
    async with websockets.connect(WS_URL) as ws:
        # subscribe
        await ws.send(json.dumps({
            "type": "subscribe",
            "channel": "market",
            "assets_ids": [up_token, down_token]
        }))

        up_ask = down_ask = None

        async for msg in ws:
            data = json.loads(msg)

            if data.get('event_type') == 'price_change':
                for pc in data.get('price_changes', []):
                    if pc['asset_id'] == up_token:
                        up_ask = float(pc['best_ask'])
                    elif pc['asset_id'] == down_token:
                        down_ask = float(pc['best_ask'])

                if up_ask and down_ask:
                    combined = up_ask + down_ask
                    if combined < 1.00:
                        print(f"ARB! {combined:.4f} edge={1-combined:.2%}")
```

## Observations

From testing (Dec 7, 2025):

| Metric | Value |
|--------|-------|
| Message frequency | ~10-50/sec during active trading |
| Typical combined | 1.01-1.03 (1-3% spread) |
| Arb opportunities | Rare, milliseconds when they appear |
| Latency | Sub-100ms from event to message |

## Notes

- First message after subscribe may be a list `[{...}]` not dict
- `book` messages have stale prices (0.01/0.99) - use `price_change` for real-time
- Both UP and DOWN tokens must be subscribed to calculate arb
- Gabagool captures arbs at combined < 0.99 (~1%+ edge)

---

# 2. Real Time Data Socket (RTDS)

Official Polymarket streaming service for real-time data.

## Connection

```
wss://ws-live-data.polymarket.com
```

Protocol: WebSocket
Data Format: JSON

## Authentication

Two types depending on subscription:

**CLOB Authentication** (trading-related):
```json
{
  "clob_auth": {
    "key": "api_key",
    "secret": "api_secret",
    "passphrase": "api_passphrase"
  }
}
```

**Gamma Authentication** (user-specific):
```json
{
  "gamma_auth": {
    "address": "wallet_address"
  }
}
```

## Connection Management

- Send PING messages every 5 seconds to maintain connection
- Supports dynamic subscriptions (add/remove without disconnect)

## Subscribe

```json
{
  "action": "subscribe",
  "subscriptions": [
    {
      "topic": "topic_name",
      "type": "message_type",
      "filters": "optional_filter_string"
    }
  ]
}
```

## Unsubscribe

```json
{
  "action": "unsubscribe",
  "subscriptions": [
    {
      "topic": "topic_name",
      "type": "message_type"
    }
  ]
}
```

## Message Structure

All messages follow this format:
```json
{
  "topic": "string",
  "type": "string",
  "timestamp": 1753314064237,
  "payload": {}
}
```

| Field | Description |
|-------|-------------|
| topic | Subscription topic (e.g., "crypto_prices") |
| type | Event type (e.g., "update") |
| timestamp | Unix milliseconds |
| payload | Event-specific data |

## Available Topics

### crypto_prices (Binance)

```json
{
  "action": "subscribe",
  "subscriptions": [
    {
      "topic": "crypto_prices",
      "type": "*",
      "filters": ""
    }
  ]
}
```

Response:
```json
{
  "topic": "crypto_prices",
  "type": "update",
  "timestamp": 1753314064237,
  "payload": {
    "symbol": "btcusdt",
    "timestamp": 1753314064213,
    "value": 91520.00
  }
}
```

### crypto_prices_chainlink

**Critical:** Polymarket resolves using Chainlink, not Binance.

```json
{
  "action": "subscribe",
  "subscriptions": [
    {
      "topic": "crypto_prices_chainlink",
      "type": "*",
      "filters": ""
    }
  ]
}
```

With symbol filter:
```json
{
  "action": "subscribe",
  "subscriptions": [
    {
      "topic": "crypto_prices_chainlink",
      "type": "*",
      "filters": "{\"symbol\":\"btc/usd\"}"
    }
  ]
}
```

Response:
```json
{
  "topic": "crypto_prices_chainlink",
  "type": "update",
  "timestamp": 1753314064237,
  "payload": {
    "symbol": "btc/usd",
    "timestamp": 1753314064213,
    "value": 91523.45
  }
}
```

Supported symbols: `btc/usd`, `eth/usd`, `sol/usd`, `xrp/usd`

### comments

Comment and reaction events on markets.

## Price Source Comparison

| Source | Symbol Format | Use Case | Latency |
|--------|---------------|----------|---------|
| Binance (crypto_prices) | `btcusdt` | Real-time signals | Fastest |
| Chainlink (crypto_prices_chainlink) | `btc/usd` | Resolution prediction | Slight delay |

**Oracle mismatch risk:** If you trade on Binance price but Chainlink differs at resolution, you lose even when "right". Use Chainlink for resolution-critical decisions.

---

# 3. Chainlink On-Chain (Direct Read)

For guaranteed resolution price, read directly from Polygon Chainlink oracles.

## Contract Addresses (Polygon)

| Coin | Address |
|------|---------|
| BTC | `0xc907E116054Ad103354f2D350FD2514433D57F6f` |
| ETH | `0xF9680D99D6C9589e2a93a78A04A279e509205945` |
| SOL | `0x10C8264C0935b3B9870013e057f330Ff3e9C56dC` |

## ABI (latestRoundData)

```json
{
  "inputs": [],
  "name": "latestRoundData",
  "outputs": [
    {"name": "roundId", "type": "uint80"},
    {"name": "answer", "type": "int256"},
    {"name": "startedAt", "type": "uint256"},
    {"name": "updatedAt", "type": "uint256"},
    {"name": "answeredInRound", "type": "uint80"}
  ],
  "stateMutability": "view",
  "type": "function"
}
```

## Python Example

```python
from web3 import Web3

w3 = Web3(Web3.HTTPProvider("https://polygon-rpc.com"))
contract = w3.eth.contract(address=BTC_ADDRESS, abi=ABI)
data = contract.functions.latestRoundData().call()
price = data[1] / 1e8  # 8 decimals
```

See `chainlink.py` for full implementation.

---

# Usage Strategy

## For Directional Trading

1. Subscribe to **CLOB market** for UP/DOWN prices
2. Subscribe to **Chainlink prices** for resolution oracle
3. At minute 11, compare:
   - Current Chainlink price vs window start
   - If Chainlink > start → buy UP
   - If Chainlink < start → buy DOWN

## For Arbitrage

1. Subscribe to **CLOB market** for both UP and DOWN tokens
2. Monitor `combined = up_ask + down_ask`
3. When `combined < 0.99`:
   - Buy equal shares of UP and DOWN
   - Guaranteed profit = `1 - combined`

## Eliminating Oracle Risk

Old approach (5% mismatch risk):
```
Signal: Binance price vs start
Risk: Chainlink may differ at resolution
```

New approach (0% mismatch risk):
```
Signal: Chainlink price vs start
Resolution: Same Chainlink price
```
