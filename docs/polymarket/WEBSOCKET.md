# WebSocket API

url: `wss://ws-subscriptions-clob.polymarket.com/ws/market`

## Subscribe

```json
{
  "auth": {},
  "markets": ["<condition_id>"],
  "assets_ids": ["<token_id>"],
  "type": "market"
}
```

## Events

| event | description |
|-------|-------------|
| book | order book update |
| price_change | price update |
| last_trade_price | trade execution |
| tick_size_change | market config change |
