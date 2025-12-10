# ClickHouse Data Schema

Connection: `n60fu3ciqd.eastus2.azure.clickhouse.cloud:8443`

## Tables

### clob_events
Websocket events from Polymarket CLOB. Main data source.

| Column | Type | Description |
|--------|------|-------------|
| ts | DateTime64(3) | Event timestamp |
| window_ts | UInt32 | 15-min window unix timestamp |
| event_type | LowCardinality(String) | `price_change`, `last_trade_price`, `book` |
| asset_id | String | Token ID |
| market | String | Condition ID |
| raw | String | Full JSON payload |

**Volume**: ~185K events per window, 3.5M+ total

**Event types**:
- `price_change` (96%): bid/ask updates
- `last_trade_price` (2%): executed trades
- `book` (2%): full orderbook snapshots

#### price_change
```json
{
  "market": "0x...",
  "price_changes": [{
    "asset_id": "123...",
    "best_bid": "0.48",
    "best_ask": "0.52",
    "price": "0.50",
    "size": "100",
    "side": "BUY"
  }],
  "timestamp": "1765155600123",
  "event_type": "price_change"
}
```

#### last_trade_price
```json
{
  "market": "0x...",
  "asset_id": "123...",
  "price": "0.49",
  "size": "100",
  "side": "BUY",
  "timestamp": "1765155600123",
  "transaction_hash": "0x...",
  "event_type": "last_trade_price"
}
```
- `side`: `BUY` = someone bought (hit ask), `SELL` = someone sold (hit bid)

#### book
```json
{
  "market": "0x...",
  "asset_id": "123...",
  "timestamp": "1765166398699",
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

---

### token_registry
Maps token IDs to coin/side. Essential for joins.

| Column | Type | Description |
|--------|------|-------------|
| window_ts | UInt32 | Window timestamp |
| coin | LowCardinality(String) | `btc`, `eth`, `sol`, `xrp` |
| side | LowCardinality(String) | `up` or `down` |
| token_id | String | CLOB token ID |
| condition_id | String | Market condition ID |
| slug | String | e.g. `btc-updown-15m-1765155600` |
| created_at | DateTime64(3) | |

**Note**: 8 tokens per window (4 coins x 2 sides)

---

### crypto_prices
Resolution data from Gamma API (post-window).

| Column | Type | Description |
|--------|------|-------------|
| ts | DateTime64(3) | Fetch timestamp |
| window_ts | UInt32 | Window timestamp |
| coin | LowCardinality(String) | |
| raw | String | Full market JSON with resolution |

**Resolution parsing**:
```python
data = json.loads(raw)
if isinstance(data, list):
    data = data[0]
outcome_prices = json.loads(data.get('outcomePrices', '[]'))
# ['1', '0'] = UP won
# ['0', '1'] = DOWN won
```

---

### rtds_events
Chainlink price feed from RTDS websocket.

| Column | Type | Description |
|--------|------|-------------|
| ts | DateTime64(3) | |
| window_ts | UInt32 | |
| topic | LowCardinality(String) | `crypto_prices_chainlink` |
| symbol | LowCardinality(String) | `BTC`, `ETH`, etc |
| raw | String | |

---

### gamma_events / gamma_markets
Gamma API responses. Less frequently used.

---

## Common Queries

### Table counts
```sql
SELECT 'clob_events' as tbl, count(*) FROM clob_events
UNION ALL SELECT 'token_registry', count(*) FROM token_registry
UNION ALL SELECT 'crypto_prices', count(*) FROM crypto_prices
```

### Recent windows
```sql
SELECT
    window_ts,
    fromUnixTimestamp(window_ts) as window_time,
    count(*) as events,
    countIf(event_type='price_change') as price_changes,
    countIf(event_type='last_trade_price') as trades
FROM clob_events
WHERE window_ts > 0
GROUP BY window_ts
ORDER BY window_ts DESC
LIMIT 20
```

### SELL trades with coin mapping (first 9 min)
```sql
SELECT
    r.window_ts,
    r.coin,
    r.side,
    JSONExtractFloat(c.raw, 'price') as price,
    JSONExtractFloat(c.raw, 'size') as size,
    toUnixTimestamp(c.ts) - r.window_ts as elapsed
FROM clob_events c
JOIN token_registry r ON r.token_id = c.asset_id AND r.window_ts = c.window_ts
WHERE c.event_type = 'last_trade_price'
  AND JSONExtractString(c.raw, 'side') = 'SELL'
  AND toUnixTimestamp(c.ts) - r.window_ts BETWEEN 0 AND 540
```

### Trade volume by coin
```sql
SELECT
    r.coin,
    count(*) as trades,
    countIf(JSONExtractString(c.raw, 'side')='SELL') as sells,
    countIf(JSONExtractString(c.raw, 'side')='BUY') as buys,
    sum(JSONExtractFloat(c.raw, 'size')) as total_size
FROM clob_events c
JOIN token_registry r ON r.token_id = c.asset_id
WHERE c.event_type = 'last_trade_price'
GROUP BY r.coin
```

### Book edge by coin (combined bid analysis)
```sql
WITH prices AS (
    SELECT
        window_ts, ts,
        arrayJoin(JSONExtractArrayRaw(raw, 'price_changes')) as pc
    FROM clob_events
    WHERE event_type = 'price_change' AND window_ts > 0
),
bids AS (
    SELECT
        p.window_ts, r.coin, r.side,
        JSONExtractFloat(p.pc, 'best_bid') as bid
    FROM prices p
    JOIN token_registry r
        ON r.token_id = JSONExtractString(p.pc, 'asset_id')
        AND r.window_ts = p.window_ts
    WHERE JSONExtractFloat(p.pc, 'best_bid') > 0
),
combined AS (
    SELECT
        window_ts, coin,
        avgIf(bid, side='up') as up_bid,
        avgIf(bid, side='down') as down_bid
    FROM bids
    GROUP BY window_ts, coin
)
SELECT
    coin,
    round(avg(1.0 - (up_bid + down_bid)) * 100, 2) as avg_edge_pct
FROM combined
WHERE up_bid > 0 AND down_bid > 0
GROUP BY coin
ORDER BY avg_edge_pct DESC
```

### Book depth at best bid
```sql
SELECT
    r.side,
    avg(JSONExtractFloat(
        arrayElement(JSONExtractArrayRaw(c.raw, 'bids'), 1),
        'size'
    )) as avg_depth
FROM clob_events c
JOIN token_registry r ON r.token_id = c.asset_id AND r.window_ts = c.window_ts
WHERE c.event_type = 'book' AND r.coin = 'btc'
GROUP BY r.side
```

### Check for gaps between windows
```sql
SELECT
    window_ts,
    fromUnixTimestamp(window_ts) as window,
    window_ts - lagInFrame(window_ts, 1) OVER (ORDER BY window_ts) as gap_sec
FROM (SELECT DISTINCT window_ts FROM clob_events WHERE window_ts > 0)
ORDER BY window_ts
```

---

## Key Metrics (from 19 windows)

### Edge by Coin
| Coin | Book Edge | Realized Edge |
|------|-----------|---------------|
| XRP | 3.9% | ~4% (low fills) |
| SOL | 3.0% | ~0% (unbalanced) |
| ETH | 1.5% | ~2-3% |
| BTC | 1.1% | ~2-3% |

### Trade Flow
- BUY:SELL ratio ~4:1 across all coins
- Most volume at 0.4-0.6 price range
- ~60% of SELLs execute at best bid

### Fill Rates (both sides filled)
| Coin | Rate |
|------|------|
| BTC | 100% |
| ETH | 100% |
| SOL | 53% |
| XRP | 12% |

---

## CLI Tools

```bash
# set password (or source .env)
export CLICKHOUSE_PASSWORD='...'

# status - check collector health
python3 skills/status.py

# analyze - market analysis
python3 skills/analyze.py windows
python3 skills/analyze.py edge --coin btc
python3 skills/analyze.py fills <window_ts> --coin btc
python3 skills/analyze.py depth <window_ts> --coin btc

# backtest - simulate strategy
python3 skills/backtest.py --coin btc --capital 50
python3 skills/backtest.py --sensitivity

# resolution - check outcomes
python3 skills/resolution.py --windows 20
python3 skills/resolution.py --coin btc

# raw queries
python3 skills/clickhouse.py "SELECT count(*) FROM clob_events"
python3 skills/clickhouse.py tables
python3 skills/clickhouse.py schema clob_events
```
