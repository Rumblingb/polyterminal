# Polymarket API Documentation

comprehensive reference for all polymarket APIs discovered through exploration

## API Overview

| API | Base URL | Auth Required | Purpose |
|-----|----------|---------------|---------|
| CLOB API | `https://clob.polymarket.com` | Yes (for trading) | Trading, orderbooks, prices |
| Gamma API | `https://gamma-api.polymarket.com` | No | Market discovery, metadata |
| Data API | `https://data-api.polymarket.com` | No | Trade history |
| Subgraph | `https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/orderbook-subgraph/0.0.1/gn` | No | Historical trade data (unlimited) |
| WebSocket | `wss://ws-subscriptions-clob.polymarket.com/ws/market` | No | Real-time prices |

---

## Key Concepts

### IDs and Tokens
- **condition_id**: Unique market identifier (0x-prefixed hex string)
- **token_id / clob_token_id**: ERC1155 token ID for each outcome (large integer as string)
- **question_id**: Hash of the market question
- **slug**: URL-friendly market name

### Market Structure
```
Event (group of markets)
└── Market (single question)
    ├── Token YES (clob_token_id[0])
    └── Token NO (clob_token_id[1])
```

---

## 1. CLOB API

base: `https://clob.polymarket.com`

### Markets

**GET /markets**
```
params: next_cursor (pagination)
response: { data: [...], next_cursor: "...", limit: 1000, count: N }
```

**GET /markets/{condition_id}**
```
response: {
  condition_id, question_id, question, description,
  market_slug, end_date_iso, tokens: [{token_id, outcome}],
  active, closed, accepting_orders, minimum_order_size, minimum_tick_size
}
```

### Prices

**GET /midpoint**
```
params: token_id
response: { mid: "0.55" }
```

**GET /spread**
```
params: token_id
response: { spread: "0.02" }
```

**GET /last-trade-price**
```
params: token_id
response: { price: "0.55", side: "BUY" }
```

### Order Book

**GET /book**
```
params: token_id
response: {
  market, asset_id, timestamp, hash,
  bids: [{price, size}],
  asks: [{price, size}],
  min_order_size, tick_size, neg_risk
}
```

### Trading (Requires Auth)

**POST /order** - Place order
**DELETE /order** - Cancel order
**GET /orders** - Get user orders

auth requires API key + HMAC signature

---

## 2. Gamma API

base: `https://gamma-api.polymarket.com`

no auth required, best for market discovery

### Markets

**GET /markets**
```
params:
  - limit (default 100)
  - offset (pagination)
  - closed (true/false)
  - active (true/false)
  - slug (exact match)
  - clob_token_ids (lookup by token ID) ← KEY FOR ENRICHMENT

response: [{
  id, question, conditionId, slug, description,
  endDate, startDate, image, icon,
  outcomes: '["Yes", "No"]',
  outcomePrices: '["0.55", "0.45"]',
  clobTokenIds: '["123...", "456..."]',
  volume, liquidity, active, closed,
  bestBid, bestAsk, spread, lastTradePrice,
  volume24hr, volume1wk, volume1mo,
  events: [{...}]
}]
```

**Token ID Lookup Example:**
```python
# given a token_id from a trade, find the market
r = requests.get("https://gamma-api.polymarket.com/markets",
                 params={"clob_token_ids": token_id})
market = r.json()[0]
print(market['question'])
```

### Events

**GET /events**
```
params: limit, offset
response: [{
  id, title, slug, description,
  startDate, endDate, image,
  active, closed, archived,
  liquidity, volume, markets: [...]
}]
```

**GET /events/{id}**
```
returns single event with nested markets
```

### Key Fields for Strategy

| Field | Use |
|-------|-----|
| `endDate` | Filter markets ending soon |
| `outcomePrices` | Current odds |
| `closed` | Market still tradeable |
| `clobTokenIds` | Token IDs for trading |
| `liquidity` | Can you get in/out |

---

## 3. Subgraph API (The Graph / Goldsky)

base: `https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/orderbook-subgraph/0.0.1/gn`

GraphQL endpoint - best for historical trade data

### Query: Trades by Wallet

```graphql
{
  orderFilledEvents(
    where: { maker: "0x..." }  # or taker
    first: 1000
    skip: 0
    orderBy: timestamp
    orderDirection: asc
  ) {
    id
    transactionHash
    timestamp
    maker
    taker
    makerAssetId      # "0" = USDC, else token_id
    takerAssetId
    makerAmountFilled # amount in wei (divide by 1e6)
    takerAmountFilled
    fee
  }
}
```

### Decoding Trades

```python
def decode_trade(event, wallet):
    maker_asset = event['makerAssetId']
    taker_asset = event['takerAssetId']
    maker_amt = int(event['makerAmountFilled']) / 1e6
    taker_amt = int(event['takerAmountFilled']) / 1e6

    is_maker = event['maker'].lower() == wallet.lower()

    if is_maker:
        if maker_asset == "0":  # gave USDC
            side, usdc, shares, token = "BUY", maker_amt, taker_amt, taker_asset
        else:  # gave shares
            side, shares, usdc, token = "SELL", maker_amt, taker_amt, maker_asset
    else:
        if taker_asset == "0":  # gave USDC
            side, usdc, shares, token = "BUY", taker_amt, maker_amt, maker_asset
        else:  # gave shares
            side, shares, usdc, token = "SELL", taker_amt, maker_amt, taker_asset

    price = usdc / shares if shares > 0 else 0
    return {"side": side, "usdc": usdc, "shares": shares, "token": token, "price": price}
```

### Available Types

```
orderFilledEvents, MarketData, Aggregation_interval
```

### Pagination

subgraph limits to 1000 results per query, use skip for pagination:
```graphql
{ orderFilledEvents(first: 1000, skip: 0) { ... } }
{ orderFilledEvents(first: 1000, skip: 1000) { ... } }
```

---

## 4. Data API

base: `https://data-api.polymarket.com`

limited endpoints available

**GET /trades**
```
params: limit
response: [{
  proxyWallet, side, asset, conditionId,
  size, price, timestamp, title, slug, icon
}]
```

note: limited to ~1500 records

---

## 5. WebSocket API

### Market Data Stream

```
url: wss://ws-subscriptions-clob.polymarket.com/ws/market
```

**Subscribe:**
```json
{
  "auth": {},
  "markets": ["<condition_id>"],
  "assets_ids": ["<token_id>"],
  "type": "market"
}
```

**Events Received:**
- `book` - order book updates
- `price_change` - price updates
- `last_trade_price` - trade executions
- `tick_size_change` - market config changes

### Python Example

```python
import websocket
import json

def on_message(ws, message):
    data = json.loads(message)
    print(data)

ws = websocket.WebSocketApp(
    "wss://ws-subscriptions-clob.polymarket.com/ws/market",
    on_message=on_message
)

# subscribe after connection
ws.send(json.dumps({
    "auth": {},
    "markets": ["0x4319532e181605cb15b1bd677759a3bc7f7394b2fdf145195b700eeaedfd5221"],
    "type": "market"
}))
```

---

## Common Patterns

### Find Markets Ending Soon with High Odds

```python
import requests
from datetime import datetime, timezone
import json

markets = requests.get(
    "https://gamma-api.polymarket.com/markets",
    params={"closed": "false", "limit": 100}
).json()

now = datetime.now(timezone.utc)

for m in markets:
    end = datetime.fromisoformat(m['endDate'].replace('Z', '+00:00'))
    hours_left = (end - now).total_seconds() / 3600

    if 0 < hours_left < 24:  # ending in 24h
        prices = json.loads(m.get('outcomePrices', '[]'))
        max_price = max(float(p) for p in prices) if prices else 0

        if max_price >= 0.95:
            print(f"{hours_left:.1f}h | {max_price:.3f} | {m['question'][:50]}")
```

### Get All Trades for a Wallet

```python
import requests

SUBGRAPH = "https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/orderbook-subgraph/0.0.1/gn"
wallet = "0x751a2b86cab503496efd325c8344e10159349ea1"

all_trades = []
for role in ["maker", "taker"]:
    skip = 0
    while True:
        query = f'''{{
          orderFilledEvents(
            where: {{ {role}: "{wallet}" }}
            first: 1000, skip: {skip}
            orderBy: timestamp, orderDirection: asc
          ) {{ id timestamp makerAssetId takerAssetId makerAmountFilled takerAmountFilled }}
        }}'''

        r = requests.post(SUBGRAPH, json={"query": query})
        events = r.json()['data']['orderFilledEvents']

        if not events:
            break
        all_trades.extend(events)
        skip += 1000

print(f"Total trades: {len(all_trades)}")
```

### Enrich Token IDs with Market Names

```python
def get_market_for_token(token_id):
    r = requests.get(
        "https://gamma-api.polymarket.com/markets",
        params={"clob_token_ids": token_id}
    )
    if r.json():
        m = r.json()[0]
        return {
            "question": m['question'],
            "outcome": m['outcomes'],
            "endDate": m['endDate']
        }
    return None
```

---

## Rate Limits

| API | Limit |
|-----|-------|
| Gamma API | ~1000 req/hour (unauth) |
| CLOB API | varies by endpoint |
| Subgraph | generous, no strict limit observed |
| WebSocket | rate limited on reconnects |

---

## Official Resources

- Docs: https://docs.polymarket.com
- Python SDK: https://github.com/Polymarket/py-clob-client
- TypeScript SDK: https://github.com/Polymarket/clob-client
