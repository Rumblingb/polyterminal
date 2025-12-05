# CLOB API

base: `https://clob.polymarket.com`

## Endpoints

### GET /book
order book for a token
```
params:
  token_id: string (required)

response:
{
  "market": "0x...",
  "asset_id": "123...",
  "timestamp": "1733...",
  "hash": "0x...",
  "bids": [{"price": "0.45", "size": "100"}],
  "asks": [{"price": "0.55", "size": "100"}]
}
```

### GET /midpoint
```
params: token_id
response: {"mid": "0.55"}
```

### GET /spread
```
params: token_id
response: {"spread": "0.02"}
```

### GET /last-trade-price
```
params: token_id
response: {"price": "0.55", "side": "BUY"}
```

### GET /prices-history
historical price data
```
params:
  market: token_id (required)
  startTs: unix timestamp
  endTs: unix timestamp
  fidelity: 1 (seconds) or higher

response:
{
  "history": [
    {"t": 1733382000, "p": 0.52},
    {"t": 1733382001, "p": 0.51}
  ]
}
```

### GET /markets
list all markets
```
params:
  next_cursor: string (pagination)

response:
{
  "data": [...],
  "next_cursor": "...",
  "limit": 1000,
  "count": N
}
```

### GET /markets/{condition_id}
single market details
```
response:
{
  "condition_id": "0x...",
  "question_id": "0x...",
  "question": "...",
  "description": "...",
  "market_slug": "...",
  "end_date_iso": "2024-...",
  "tokens": [{"token_id": "123...", "outcome": "Yes"}],
  "active": true,
  "closed": false,
  "accepting_orders": true,
  "minimum_order_size": "5",
  "minimum_tick_size": "0.01"
}
```

## Trading Endpoints (require auth)

### POST /order
### DELETE /order
### GET /orders

auth: API key + HMAC signature via py-clob-client
