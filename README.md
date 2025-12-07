# Polymarket 15m Binary Options Recorder

Records order book snapshots and spot prices for Polymarket's 15-minute crypto binary options markets.

## What it does

- Polls Polymarket CLOB API every 500ms for order book data
- Streams real-time crypto prices from Binance WebSocket
- Stores snapshots to Supabase (markets + snapshots tables)
- Auto-resolves markets when windows close
- Tracks BTC, ETH, SOL, XRP

## Market Structure

Each 15-minute window has:
- **UP token**: wins if price at end >= price at start
- **DOWN token**: wins if price at end < price at start
- Winner pays $1.00, loser pays $0.00
- Windows start on :00, :15, :30, :45

## APIs

### Polymarket Gamma API (market discovery)
```
Base: https://gamma-api.polymarket.com

GET /events?tag_id=102467&closed=false&limit=10
- tag_id=102467 = 15m crypto markets
- returns events with markets array
- clobTokenIds: [UP_token, DOWN_token]
- slug format: btc-updown-15m-{unix_timestamp}
```

### Polymarket CLOB API (order book)
```
Base: https://clob.polymarket.com

GET /book?token_id={token}
- returns { bids: [{price, size}], asks: [{price, size}] }
- prices are 0-1 (probability/cents)
- best bid = max(bids), best ask = min(asks)
```

### Binance WebSocket (spot prices)
```
wss://stream.binance.com:9443/stream?streams=btcusdt@trade/ethusdt@trade/...

- real-time trade stream
- <50ms latency
- msg format: { data: { s: "BTCUSDT", p: "99000.50" } }
```

### Binance REST (historical)
```
GET https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&startTime={ms}&limit=1

- get open price at specific timestamp
- used for spot_start at window begin
```

## Database Schema

```sql
-- markets (one per 15-min window per coin)
CREATE TABLE markets (
    id              BIGSERIAL PRIMARY KEY,
    coin            TEXT NOT NULL,
    window_ts       BIGINT NOT NULL,
    slug            TEXT NOT NULL,
    up_token        TEXT NOT NULL,
    down_token      TEXT NOT NULL,
    spot_start      DOUBLE PRECISION,
    spot_end        DOUBLE PRECISION,
    outcome         TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(coin, window_ts)
);

-- snapshots (order book + price at a point in time)
CREATE TABLE snapshots (
    id              BIGSERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL,
    market_id       BIGINT REFERENCES markets(id),
    spot_price      DOUBLE PRECISION,
    up_bid          DOUBLE PRECISION,
    up_ask          DOUBLE PRECISION,
    down_bid        DOUBLE PRECISION,
    down_ask        DOUBLE PRECISION,
    up_depth        JSONB,
    down_depth      JSONB
);
```

## Environment Variables

```bash
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_KEY=eyJ...
```

## Running

```bash
uv run python main.py
```

## Deployment

Deployed on Railway. Uses `Procfile`:
```
web: python main.py
```

## Data Rates

- 4 coins x 2 tokens x 2 snapshots/sec = 16 API calls/sec
- ~1800 snapshots per 15-min window per coin
- ~1.7 GB/day with full depth data
- ~70 MB/day without depth data

## Rate Limits

- CLOB /book: 200 req/10s (safe at 500ms polling)
- Gamma /events: 100 req/10s (only called every 30s)

## Observed Market Behavior

- MM response time: 3-6 seconds after BTC move
- Spread: 1-3 cents typical
- Late window (last 3 min): prices converge toward 0 or 1
- Spread widens significantly in last minute

## Price Correlation

| BTC move | Typical UP price |
|----------|------------------|
| +0.00-0.05% | 0.50-0.55 |
| +0.05-0.10% | 0.60-0.75 |
| +0.10-0.20% | 0.75-0.90 |
| +0.20%+ | 0.90-0.98 |
