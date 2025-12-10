# Polymarket API Reference

Skills for querying Polymarket on-chain and off-chain data.

## Data Sources Overview

| Source | Type | Use Case | Latency | Limit |
|--------|------|----------|---------|-------|
| **Goldsky Subgraph** | GraphQL | Historical wallet trades (source of truth) | ~1-5s | 1000/query |
| **Data API** | REST | Enriched trades with market metadata | ~5s | 500/query |
| **Gamma API** | REST | Market metadata, token mapping | ~1-3s | - |
| **CLOB API** | REST | Orderbook, prices | ~1-3s | - |

---

## Modules

### subgraph.py - On-Chain Trade Data (Source of Truth)

Queries the Goldsky subgraph which indexes Polymarket smart contracts.

```
URL: https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/orderbook-subgraph/0.0.1/gn
```

#### Functions

```python
from subgraph import get_wallet_trades, decode_trade, count_wallet_trades

# fetch trades for a wallet
trades = get_wallet_trades(
    wallet='0x6031...',
    limit=100,          # max 1000
    skip=0,             # pagination offset
    role='both',        # 'maker', 'taker', or 'both'
    since_ts=1234567    # unix timestamp filter
)

# decode raw event into readable trade
decoded = decode_trade(raw_event, wallet)
# returns: {tx, timestamp, role, side, shares, price, usdc, fee, token_id}

# quick trade count
counts = count_wallet_trades(wallet)
# returns: {maker: N, taker: N, total: N}

# get ALL trades (paginated)
all_trades = get_wallet_trades_all(wallet, role='both')

# trades by token
token_trades = get_trades_by_token(token_id, limit=100)

# recent global trades
recent = get_recent_trades(limit=50)
```

#### Raw Event Structure

```json
{
  "id": "0x123..._0xabc...",
  "timestamp": "1765345143",
  "transactionHash": "0x...",
  "orderHash": "0x...",
  "maker": "0x...",
  "taker": "0x...",
  "makerAssetId": "0",
  "takerAssetId": "61923092...",
  "makerAmountFilled": "3180000",
  "takerAmountFilled": "6000000",
  "fee": "0"
}
```

#### Trade Decoding Logic

```
Asset ID "0" = USDC
Asset ID != "0" = Conditional token (outcome)

Amounts are in 6 decimal units (divide by 1e6)

If wallet is MAKER:
  makerAssetId == "0" → BUY (sent USDC, got tokens)
  makerAssetId != "0" → SELL (sent tokens, got USDC)

If wallet is TAKER:
  takerAssetId == "0" → BUY
  takerAssetId != "0" → SELL

Price = usdc_amount / shares_amount
```

#### CLI

```bash
python subgraph.py trades <wallet> [--limit N] [--role maker|taker]
python subgraph.py count <wallet>
python subgraph.py recent [--limit N]
python subgraph.py token <token_id> [--limit N]
```

---

### data_api.py - Enriched Trade Data

Pre-decoded trades with market metadata. Easier but may miss historical data.

```
URL: https://data-api.polymarket.com/trades
```

#### Functions

```python
from data_api import get_trades, get_wallet_trades, summarize_trades

# recent trades
trades = get_trades(limit=100, offset=0, market=None, asset_id=None)

# wallet trades
trades = get_wallet_trades(wallet, limit=100, offset=0, role='both')

# all wallet trades (paginated)
all_trades = get_wallet_trades_all(wallet, max_trades=5000)

# trades by market
trades = get_market_trades(condition_id, limit=100)

# trades by token
trades = get_token_trades(token_id, limit=100)

# summarize trades
summary = summarize_trades(trades)
# returns: {count, total_volume_usdc, buy_count, sell_count, ...}
```

#### Response Structure

```json
{
  "proxyWallet": "0x...",
  "side": "BUY",
  "asset": "61923092...",
  "conditionId": "0x...",
  "size": 14.0,
  "price": 0.83,
  "timestamp": 1765345313,
  "title": "Bitcoin Up or Down - December 10...",
  "slug": "btc-updown-15m-1765344600",
  "outcome": "Up",
  "outcomeIndex": 0,
  "transactionHash": "0x...",
  "name": "gabagool22",
  "pseudonym": "Grown-Cantaloupe"
}
```

#### CLI

```bash
python data_api.py recent [--limit N]
python data_api.py wallet <address> [--limit N]
python data_api.py market <condition_id> [--limit N]
python data_api.py summary <address>
```

---

### gamma.py - Market Metadata

Map token IDs to markets, get market details, find active markets.

```
URL: https://gamma-api.polymarket.com
```

#### Functions

```python
from gamma import get_market_by_token, get_token_info, find_markets

# map token to market (includes outcome detection)
market = get_market_by_token(token_id)
# adds: market['outcome'], market['outcome_index']

# get full token info
info = get_token_info(token_id)
# returns: {token_id, market, slug, outcome, current_price, condition_id, counterpart_token_id}

# batch lookup
infos = batch_token_info([token_id1, token_id2])

# get market by slug
market = get_market_by_slug('btc-updown-15m-1765344600')

# get market by condition
market = get_market_by_condition('0xa69a5bbd...')

# find markets
markets = find_markets(
    slug_contains='updown-15m',
    active=True,
    closed=False,
    limit=50
)

# find events (market groups)
events = find_events(slug_contains='updown', active=True)

# get 15m updown markets
markets = get_15m_updown_markets(coin='btc')
```

#### Market Structure

```json
{
  "id": "900298",
  "question": "Bitcoin Up or Down - December 10...",
  "conditionId": "0xa69a5bbd...",
  "slug": "btc-updown-15m-1765344600",
  "outcomes": "[\"Up\", \"Down\"]",
  "outcomePrices": "[\"0.495\", \"0.505\"]",
  "clobTokenIds": "[\"61923092...\", \"27239269...\"]",
  "volume": "60492.32",
  "liquidity": "16575.02",
  "active": true,
  "closed": false,
  "resolutionSource": "https://data.chain.link/streams/btc-usd"
}
```

#### Token ID ↔ Outcome Mapping

```python
outcomes = json.loads(market['outcomes'])        # ["Up", "Down"]
token_ids = json.loads(market['clobTokenIds'])   # ["61923...", "27239..."]

# token_ids[0] → outcomes[0] (Up)
# token_ids[1] → outcomes[1] (Down)
```

#### CLI

```bash
python gamma.py token <token_id>
python gamma.py slug <slug>
python gamma.py find [--slug-contains X] [--limit N]
python gamma.py 15m [--coin btc]
```

---

### clob.py - Orderbook & Pricing

Live orderbook snapshots and pricing.

```
URL: https://clob.polymarket.com
```

#### Functions

```python
from clob import get_book, get_price, get_spread, get_depth

# full orderbook
book = get_book(token_id)
# returns: {market, asset_id, timestamp, bids: [{price, size}], asks: [...]}

# best price
price = get_price(token_id, side='buy')  # or 'sell'

# bid/ask spread
spread = get_spread(token_id)
# returns: {bid, ask, spread, spread_pct, mid, bid_size, ask_size}

# orderbook depth
depth = get_depth(token_id, levels=5)
# returns: {bids, asks, total_bid_depth, total_ask_depth}

# combined spread for up/down pair (edge calculation)
edge = get_combined_spread(up_token, down_token)
# returns: {up_bid, down_bid, combined, edge, edge_pct}

# estimate fill for order
est = estimate_fill(token_id, side='buy', size=100)
# returns: {avg_price, total_cost, slippage_pct, levels_consumed}

# list markets
result = get_markets(limit=100, cursor=None)

# single market
market = get_market(condition_id)
```

#### Orderbook Structure

```json
{
  "market": "0xa69a5bbd...",
  "asset_id": "61923092...",
  "timestamp": "1765345129933",
  "bids": [
    {"price": "0.48", "size": "500"},
    {"price": "0.47", "size": "300"}
  ],
  "asks": [
    {"price": "0.52", "size": "400"},
    {"price": "0.53", "size": "200"}
  ]
}
```

#### CLI

```bash
python clob.py book <token_id>
python clob.py price <token_id> [--side buy|sell]
python clob.py spread <token_id>
python clob.py depth <token_id> [--levels N]
python clob.py estimate <token_id> <side> <size>
python clob.py markets [--limit N]
```

---

### markets.py - Market Discovery & Search

Find markets by volume, category, search. Get market details and summaries.

#### Functions

```python
from markets import get_trending, search_markets, get_market_details

# top markets by 24h volume
trending = get_trending(limit=20, timeframe='24hr')
# timeframe: '24hr', '1wk', '1mo', '1yr'

# search markets
results = search_markets('bitcoin', limit=10, active_only=True)

# get active markets
active = get_active_markets(limit=50, offset=0)

# get markets by category
sports = get_markets_by_category('Sports', limit=20)

# list all categories
categories = get_categories()

# get market details (full info)
market = get_market_details(slug='fed-rate-hike-in-2025')
market = get_market_details(condition_id='0xa69a...')

# get event (group of markets)
event = get_event(slug='fed-rate-hike-in-2025')

# list events
events = get_events(active=True, limit=20)

# get 15m updown markets
markets = get_15m_markets(coin='btc')

# get current 15m window for all coins
windows = get_current_15m_window()
# returns: {'btc': {...}, 'eth': {...}, 'sol': {...}, 'xrp': {...}}

# get full market summary with orderbook data
summary = get_market_summary(slug='fed-rate-hike-in-2025')

# get price history (from CLOB)
history = get_price_history(condition_id, interval='1h', fidelity=1)
```

#### CLI

```bash
python markets.py trending [--limit N]
python markets.py search <query> [--limit N]
python markets.py categories
python markets.py category <name> [--limit N]
python markets.py details <slug>
python markets.py event <slug>
python markets.py active [--limit N]
python markets.py 15m [--coin btc]
```

---

### wallet.py - Wallet Analysis

High-level wallet analysis combining all data sources.

#### Functions

```python
from wallet import analyze_wallet, get_positions, get_pnl, get_activity

# full analysis
analysis = analyze_wallet(wallet, limit=500)
# returns: {address, trade_counts, activity_24h, positions, pnl}

# decoded trades with market info
trades = get_decoded_trades(wallet, limit=500, since_ts=None)

# current open positions
positions = get_positions(wallet, limit=1000)
# returns: {token_id: {shares, avg_entry, market, outcome, current_price, unrealized_pnl}}

# realized P&L
pnl = get_pnl(wallet, limit=1000)
# returns: {total_pnl, total_volume, win_rate, by_market}

# recent activity
activity = get_activity(wallet, hours=24)
# returns: {trades_count, volume, buys, sells, unique_markets, hourly_breakdown}
```

#### CLI

```bash
python wallet.py analyze <address>
python wallet.py trades <address> [--limit N]
python wallet.py positions <address>
python wallet.py pnl <address>
python wallet.py activity <address> [--hours N]
```

---

## Smart Contracts

### CTF Exchange (main)
```
Address: 0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E
```

### NegRisk CTF
```
Address: 0xC5d563A36AE78145C45a50134d48A1215220f80a
```

### OrderFilled Event

```
Topic: 0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6

Indexed (topics):
  [0] event signature
  [1] orderHash
  [2] maker address
  [3] taker address

Data (non-indexed):
  bytes 0-31:   makerAssetId
  bytes 32-63:  takerAssetId
  bytes 64-95:  makerAmountFilled
  bytes 96-127: takerAmountFilled
  bytes 128-159: fee
```

---

## Known Wallets

| Name | Address | Notes |
|------|---------|-------|
| Gabagool | `0x6031b6eed1c97e853c6e0f03ad3ce3529351f96d` | Major market maker |
| Sharky | `0x751a2b86cab503496efd325c8344e10159349ea1` | Active trader |

---

## WebSocket Endpoints

### CLOB WebSocket (orderbook updates)
```
URL: wss://ws-subscriptions-clob.polymarket.com/ws/market
Subscribe: {"assets_ids": ["token_id1", ...], "type": "market"}

Events:
- book: full orderbook snapshot
- price_change: best bid/ask updates
- last_trade_price: executed trade
```

### RTDS WebSocket (Chainlink prices)
```
URL: wss://ws-live-data.polymarket.com
Events: crypto_prices_chainlink
```

### Polygon RPC (on-chain events)
```
wss://polygon-bor-rpc.publicnode.com
wss://polygon.drpc.org

Subscribe via eth_subscribe to OrderFilled logs
Latency: ~2-5s (vs ~10-30s from data-api)
```

---

## Common Patterns

### Get all trades for a wallet with market info

```python
from wallet import get_decoded_trades

trades = get_decoded_trades('0x6031...', limit=500)
for t in trades:
    print(f"{t['side']} {t['shares']:.2f} {t['outcome']} @ ${t['price']:.4f}")
    print(f"  Market: {t['market']}")
```

### Calculate book edge for 15m market

```python
from gamma import get_market_by_slug
from clob import get_combined_spread
import json

market = get_market_by_slug('btc-updown-15m-1765344600')
tokens = json.loads(market['clobTokenIds'])

edge = get_combined_spread(tokens[0], tokens[1])
print(f"Book edge: {edge['edge_pct']}%")
```

### Monitor wallet in real-time

```python
import time
from subgraph import get_wallet_trades

wallet = '0x6031...'
last_ts = int(time.time())

while True:
    trades = get_wallet_trades(wallet, limit=10, since_ts=last_ts)
    for t in trades:
        print(f"New trade: {t['transactionHash']}")
    if trades:
        last_ts = int(trades[0]['timestamp'])
    time.sleep(5)
```

---

## Rate Limits & Pagination

| API | Limit | Pagination |
|-----|-------|------------|
| Goldsky Subgraph | 1000/query | `skip` param |
| Data API | 500/query | `offset` or `before` param |
| CLOB markets | 1000/query | cursor-based |
| Gamma API | varies | `limit` param |

---

## Quick Reference

```bash
# market discovery
python markets.py trending --limit 10
python markets.py search "bitcoin" --limit 5
python markets.py 15m
python markets.py details fed-rate-hike-in-2025

# wallet analysis
python wallet.py analyze 0x6031b6eed1c97e853c6e0f03ad3ce3529351f96d
python wallet.py positions 0x6031...
python wallet.py pnl 0x6031...

# trades (source of truth)
python subgraph.py trades 0x6031... --limit 50
python subgraph.py count 0x6031...

# enriched trades with market names
python data_api.py wallet 0x6031... --limit 50
python data_api.py recent --limit 20

# token → market mapping
python gamma.py token 61923092...
python gamma.py slug btc-updown-15m-1765344600

# orderbook
python clob.py book 61923092...
python clob.py spread 61923092...
python clob.py depth 61923092... --levels 10
```
