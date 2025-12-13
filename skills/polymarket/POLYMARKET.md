# Polymarket API Reference

Skills for querying and trading on Polymarket.

## API Endpoints

| API | Base URL | Auth |
|-----|----------|------|
| CLOB | `https://clob.polymarket.com` | L1/L2 for trading |
| Gamma | `https://gamma-api.polymarket.com` | None |
| Data API | `https://data-api.polymarket.com` | None |
| Subgraph | `https://api.goldsky.com/.../orderbook-subgraph/0.0.1/gn` | None |

## Data Sources Overview

| Source | Type | Use Case | Latency |
|--------|------|----------|---------|
| **Goldsky Subgraph** | GraphQL | Historical wallet trades (source of truth) | ~1-5s |
| **Data API** | REST | Enriched trades with market metadata | ~5s |
| **Gamma API** | REST | Market metadata, token mapping, search | ~1-3s |
| **CLOB API** | REST/WS | Orderbook, prices, order placement | ~1-3s |

---

# CLOB Trading System

The CLOB (Central Limit Order Book) is hybrid-decentralized: off-chain matching with on-chain settlement via the Exchange contract.

## Authentication

### Signature Types

| Type | ID | Description | Funder |
|------|----|-------------|--------|
| EOA | 0 | Standard wallet (MetaMask) | EOA address, needs POL for gas |
| POLY_PROXY | 1 | Magic Link email/Google login | Proxy wallet from Polymarket.com |
| GNOSIS_SAFE | 2 | Browser wallet proxy (most common) | Deployed Safe proxy wallet |

### L1 Authentication (Private Key)

Signs EIP-712 messages to prove wallet ownership. Used to create/derive API keys.

**Headers:**
```
POLY_ADDRESS:   Polygon signer address
POLY_SIGNATURE: EIP-712 signature
POLY_TIMESTAMP: Unix timestamp
POLY_NONCE:     Nonce (default 0)
```

**Endpoints:**
```
POST /auth/api-key        # create new API credentials
GET  /auth/derive-api-key # derive existing credentials
```

**Response:**
```json
{
  "apiKey": "550e8400-e29b-41d4-a716-446655440000",
  "secret": "base64EncodedSecretString",
  "passphrase": "randomPassphraseString"
}
```

### L2 Authentication (API Key)

HMAC-SHA256 signed requests for trading operations.

**Headers:**
```
POLY_ADDRESS:    Polygon signer address
POLY_SIGNATURE:  HMAC signature
POLY_TIMESTAMP:  Unix timestamp
POLY_API_KEY:    API key
POLY_PASSPHRASE: Passphrase
```

### Python Client Setup

```python
from py_clob_client.client import ClobClient

host = "https://clob.polymarket.com"
key = "0x..."  # private key
chain_id = 137

# for browser wallet users (signature_type=2)
client = ClobClient(host, key=key, chain_id=chain_id,
                    signature_type=2, funder=PROXY_ADDRESS)

# get/create API credentials
client.set_api_creds(client.create_or_derive_api_creds())
```

---

## Orders

### Order Types

| Type | Description |
|------|-------------|
| **GTC** | Good-Til-Cancelled. Limit order active until filled or cancelled |
| **GTD** | Good-Til-Date. Active until specified UTC timestamp (add 60s security threshold) |
| **FOK** | Fill-Or-Kill. Market order must fill entirely or cancel |
| **FAK** | Fill-And-Kill. Market order fills what's available, cancels rest |

### Place Single Order

```
POST /order
```

**Payload:**
```json
{
  "order": {
    "salt": 660377097,
    "maker": "0x...",
    "signer": "0x...",
    "taker": "0x0000000000000000000000000000000000000000",
    "tokenId": "88613172...",
    "makerAmount": "50000",
    "takerAmount": "5000000",
    "expiration": "0",
    "nonce": "0",
    "feeRateBps": "0",
    "side": "BUY",
    "signatureType": 0,
    "signature": "0x..."
  },
  "owner": "api_key",
  "orderType": "GTC"
}
```

**Response:**
```json
{
  "success": true,
  "errorMsg": "",
  "orderID": "0x...",
  "transactionHashes": ["0x..."],
  "status": "live",
  "takingAmount": "50000",
  "makingAmount": "5000000"
}
```

**Order Statuses:**
- `matched` - placed and matched with resting order
- `live` - resting on book
- `delayed` - marketable but delayed
- `unmatched` - marketable but delay failed

### Place Multiple Orders (Batch)

```
POST /orders
```

Max 15 orders per batch.

```python
from py_clob_client.clob_types import OrderArgs, OrderType, PostOrdersArgs

resp = client.post_orders([
    PostOrdersArgs(
        order=client.create_order(OrderArgs(
            price=0.50, size=100, side=BUY, token_id="..."
        )),
        orderType=OrderType.GTC,
    ),
    PostOrdersArgs(
        order=client.create_order(OrderArgs(
            price=0.45, size=100, side=BUY, token_id="..."
        )),
        orderType=OrderType.GTC,
    )
])
```

### Get Order

```
GET /data/order/<order_hash>
```

**Response:**
```json
{
  "id": "0x...",
  "status": "LIVE",
  "owner": "api_key",
  "maker_address": "0x...",
  "market": "0x...",
  "asset_id": "61923...",
  "side": "BUY",
  "original_size": "100",
  "size_matched": "0",
  "price": "0.50",
  "associate_trades": [],
  "outcome": "Yes",
  "created_at": 1702345678,
  "expiration": "0",
  "order_type": "GTC"
}
```

### Get Active Orders

```
GET /data/orders
```

**Query params:** `id`, `market`, `asset_id`

### Cancel Orders

```bash
DELETE /order              # single order: {"orderID": "0x..."}
DELETE /orders             # multiple: ["0x...", "0x..."]
DELETE /cancel-all         # all orders
DELETE /cancel-market-orders  # by market: {"market": "0x...", "asset_id": "..."}
```

**Response:**
```json
{
  "canceled": ["0x..."],
  "not_canceled": {}
}
```

### Order Reward Scoring

```
GET /order-scoring?order_id=0x...
POST /orders-scoring  # batch: {"orderIds": ["0x..."]}
```

### Order Errors

| Error | Description |
|-------|-------------|
| INVALID_ORDER_MIN_TICK_SIZE | Price breaks tick size rules |
| INVALID_ORDER_MIN_SIZE | Size below minimum |
| INVALID_ORDER_DUPLICATED | Same order already placed |
| INVALID_ORDER_NOT_ENOUGH_BALANCE | Insufficient balance/allowance |
| INVALID_ORDER_EXPIRATION | Expiration in the past |
| FOK_ORDER_NOT_FILLED_ERROR | FOK order couldn't fill completely |

---

## Trades

### Get Trades (L2 Required)

```
GET /data/trades
```

**Query params:** `id`, `taker`, `maker`, `market`, `before`, `after`

**Trade Statuses:**
| Status | Terminal | Description |
|--------|----------|-------------|
| MATCHED | no | Sent to executor |
| MINED | no | Mined, no finality |
| CONFIRMED | yes | Finalized, successful |
| RETRYING | no | Failed, being retried |
| FAILED | yes | Failed permanently |

**Response:**
```json
{
  "id": "...",
  "taker_order_id": "0x...",
  "market": "0x...",
  "asset_id": "61923...",
  "side": "BUY",
  "size": "100",
  "fee_rate_bps": "0",
  "price": "0.50",
  "status": "CONFIRMED",
  "match_time": "1702345678",
  "outcome": "Yes",
  "transaction_hash": "0x...",
  "trader_side": "TAKER",
  "maker_orders": [
    {
      "order_id": "0x...",
      "matched_amount": "100",
      "price": "0.50",
      "side": "SELL"
    }
  ]
}
```

---

## Orderbook & Pricing

### Get Orderbook

```
GET /book?token_id=...
```

```json
{
  "market": "0x...",
  "asset_id": "61923...",
  "timestamp": "1702345678000",
  "bids": [{"price": "0.48", "size": "500"}],
  "asks": [{"price": "0.52", "size": "400"}]
}
```

### Get Price

```
GET /price?token_id=...&side=buy
```

### Get Midpoint

```
GET /midpoint?token_id=...
```

### Batch Spreads

```
POST /spreads
```

```json
[{"token_id": "123..."}, {"token_id": "456..."}]
```

**Response:**
```json
{"123...": "0.04", "456...": "0.02"}
```

### Price History

```
GET /prices-history?market=0x...&interval=1h&fidelity=1
```

---

## Fees

Currently 0 bps for both maker and taker.

Fee calculation:
- **Selling:** `fee = baseRate * min(price, 1-price) * size`
- **Buying:** `fee = baseRate * min(price, 1-price) * size / price`

---

# Gamma API (Markets)

## Search

```
GET /public-search?q=bitcoin
```

**Query params:**
- `q` (required) - search query
- `limit_per_type` - results per type
- `events_status` - filter status
- `search_profiles` - include profiles

**Response:**
```json
{
  "events": [...],
  "tags": [...],
  "profiles": [...],
  "pagination": {}
}
```

## List Markets

```
GET /markets
```

**Query params:**
| Param | Type | Description |
|-------|------|-------------|
| limit | int | max results |
| offset | int | pagination |
| order | string | sort field (volume24hr, liquidity, etc) |
| ascending | bool | sort direction |
| slug | string[] | filter by slugs |
| clob_token_ids | string[] | filter by tokens |
| condition_ids | string[] | filter by conditions |
| active | bool | active only |
| closed | bool | closed only |
| tag_id | int | category filter |

**Response fields:**
```json
{
  "id": "900298",
  "question": "Will Bitcoin hit $100k?",
  "conditionId": "0x...",
  "slug": "bitcoin-100k",
  "outcomes": "[\"Yes\", \"No\"]",
  "outcomePrices": "[\"0.65\", \"0.35\"]",
  "clobTokenIds": "[\"123...\", \"456...\"]",
  "volume": "1500000",
  "volume24hr": 50000,
  "liquidity": "250000",
  "active": true,
  "closed": false,
  "endDate": "2024-12-31T00:00:00Z",
  "bestBid": 0.64,
  "bestAsk": 0.66,
  "lastTradePrice": 0.65
}
```

## Events

```
GET /events           # list events
GET /events/<id>      # by id
GET /events?slug=...  # by slug
```

## Tags

```
GET /tags             # all tags
GET /tags/<id>        # by id
```

---

# Data API

## Trades

```
GET /trades?proxyWallet=0x...&limit=100
```

**Response:**
```json
{
  "proxyWallet": "0x...",
  "side": "BUY",
  "asset": "61923...",
  "conditionId": "0x...",
  "size": 100.0,
  "price": 0.65,
  "timestamp": 1702345678,
  "title": "Bitcoin to $100k",
  "slug": "bitcoin-100k",
  "outcome": "Yes",
  "transactionHash": "0x..."
}
```

## Positions

```
GET /positions?user=0x...
```

## Activity

```
GET /activity?user=0x...
```

---

# Subgraph (On-Chain Truth)

```
URL: https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/orderbook-subgraph/0.0.1/gn
```

## Query Trades

```graphql
{
  orderFilledEvents(
    where: {maker: "0x..."}
    orderBy: timestamp
    orderDirection: desc
    first: 100
  ) {
    id
    timestamp
    transactionHash
    orderHash
    maker
    taker
    makerAssetId
    takerAssetId
    makerAmountFilled
    takerAmountFilled
    fee
  }
}
```

## Trade Decoding

```
Asset ID "0" = USDC
Asset ID != "0" = Conditional token

Amounts in 6 decimals (divide by 1e6)

MAKER:
  makerAssetId == "0" → BUY (sent USDC, got tokens)
  makerAssetId != "0" → SELL (sent tokens, got USDC)

TAKER:
  takerAssetId == "0" → BUY
  takerAssetId != "0" → SELL

Price = usdc_amount / shares_amount
```

---

# Smart Contracts

## Addresses (Polygon)

| Contract | Address |
|----------|---------|
| CTF Exchange | `0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E` |
| NegRisk CTF | `0xC5d563A36AE78145C45a50134d48A1215220f80a` |
| USDC | `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174` |

## OrderFilled Event

```
Topic: 0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6

Indexed:
  [1] orderHash
  [2] maker
  [3] taker

Data:
  bytes 0-31:   makerAssetId
  bytes 32-63:  takerAssetId
  bytes 64-95:  makerAmountFilled
  bytes 96-127: takerAmountFilled
  bytes 128-159: fee
```

---

# WebSockets

## CLOB (Orderbook)

```
wss://ws-subscriptions-clob.polymarket.com/ws/market

Subscribe: {"assets_ids": ["token_id1"], "type": "market"}

Events: book, price_change, last_trade_price
```

## User Channel (L2 Required)

```
wss://ws-subscriptions-clob.polymarket.com/ws/user

Events: order updates, trade confirmations
```

## RTDS (Chainlink)

```
wss://ws-live-data.polymarket.com
Events: crypto_prices_chainlink
```

---

# Local Modules

## subgraph.py

On-chain trades (source of truth).

```python
from subgraph import get_wallet_trades, decode_trade, count_wallet_trades

trades = get_wallet_trades(wallet, limit=100, role='both', since_ts=None)
decoded = decode_trade(raw_event, wallet)
counts = count_wallet_trades(wallet)
all_trades = get_wallet_trades_all(wallet)
```

## data_api.py

Enriched trades with metadata.

```python
from data_api import get_trades, get_wallet_trades, summarize_trades

trades = get_trades(limit=100)
trades = get_wallet_trades(wallet, limit=100)
summary = summarize_trades(trades)
```

## gamma.py

Market metadata and token mapping.

```python
from gamma import get_market_by_token, get_token_info, find_markets

market = get_market_by_token(token_id)
info = get_token_info(token_id)
markets = find_markets(slug_contains='updown', active=True)
```

## clob.py

Orderbook and pricing.

```python
from clob import get_book, get_price, get_spread, get_spreads_batch

book = get_book(token_id)
price = get_price(token_id, side='buy')
spread = get_spread(token_id)
spreads = get_spreads_batch([token_id1, token_id2])
edge = get_combined_spread(up_token, down_token)
est = estimate_fill(token_id, 'buy', 100)
```

## markets.py

Market discovery and search.

```python
from markets import search_markets, get_trending, get_market_details, public_search

results = public_search('bitcoin')  # official search API
trending = get_trending(limit=20)
market = get_market_details(slug='bitcoin-100k')
windows = get_current_15m_window()
```

## wallet.py

Wallet analysis.

```python
from wallet import analyze_wallet, get_positions, get_pnl

analysis = analyze_wallet(wallet)
positions = get_positions(wallet)
pnl = get_pnl(wallet)
```

---

# CLI Quick Reference

```bash
# market discovery
python markets.py trending --limit 10
python markets.py search "bitcoin"
python markets.py 15m

# wallet analysis
python wallet.py analyze 0x6031...
python wallet.py positions 0x6031...

# orderbook
python clob.py book <token_id>
python clob.py spread <token_id>

# trades
python subgraph.py trades 0x6031... --limit 50
python data_api.py wallet 0x6031...

# token lookup
python gamma.py token <token_id>
```

---

# Known Wallets

| Name | Address |
|------|---------|
| Gabagool | `0x6031b6eed1c97e853c6e0f03ad3ce3529351f96d` |
| Sharky | `0x751a2b86cab503496efd325c8344e10159349ea1` |

---

# Rate Limits

| API | Limit | Pagination |
|-----|-------|------------|
| Subgraph | 1000/query | `skip` |
| Data API | 500/query | `offset` / `before` |
| CLOB | 1000/query | cursor |
| Gamma | varies | `limit` / `offset` |
