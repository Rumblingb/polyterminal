# Gamma API

base: `https://gamma-api.polymarket.com`

no auth required

## Endpoints

### GET /markets
```
params:
  limit: int (default 100)
  offset: int
  closed: true/false
  active: true/false
  slug: string (exact match)
  clob_token_ids: string (lookup by token ID)

response: [{
  "id": "...",
  "question": "...",
  "conditionId": "0x...",
  "slug": "...",
  "description": "...",
  "endDate": "2024-...",
  "startDate": "2024-...",
  "outcomes": "[\"Yes\", \"No\"]",
  "outcomePrices": "[\"0.55\", \"0.45\"]",
  "clobTokenIds": "[\"123...\", \"456...\"]",
  "volume": "10000",
  "liquidity": "5000",
  "active": true,
  "closed": false,
  "bestBid": "0.54",
  "bestAsk": "0.56"
}]
```

### GET /events
```
params:
  limit: int
  offset: int
  tag_id: int (filter by category)
  closed: true/false

response: [{
  "id": "...",
  "title": "...",
  "slug": "...",
  "startDate": "...",
  "endDate": "...",
  "active": true,
  "closed": false,
  "markets": [...]
}]
```

### GET /events/{id}
single event with nested markets

## Tag IDs

| tag_id | category |
|--------|----------|
| 102467 | crypto 15m up/down |

## Fields (JSON strings that need parsing)

- `outcomes`: `"[\"Yes\", \"No\"]"` or `"[\"Up\", \"Down\"]"`
- `outcomePrices`: `"[\"0.55\", \"0.45\"]"`
- `clobTokenIds`: `"[\"123...\", \"456...\"]"`
