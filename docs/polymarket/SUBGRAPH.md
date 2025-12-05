# Subgraph API (Goldsky)

base: `https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/orderbook-subgraph/0.0.1/gn`

GraphQL endpoint, POST requests

## Queries

### orderFilledEvents
trade history
```graphql
{
  orderFilledEvents(
    where: { maker: "0x..." }
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
    makerAssetId
    takerAssetId
    makerAmountFilled
    takerAmountFilled
    fee
  }
}
```

## Fields

| field | description |
|-------|-------------|
| makerAssetId | "0" = USDC, else token_id |
| takerAssetId | "0" = USDC, else token_id |
| makerAmountFilled | amount in wei (divide by 1e6) |
| takerAmountFilled | amount in wei (divide by 1e6) |

## Pagination

max 1000 results per query, use `skip` for pagination:
```graphql
first: 1000, skip: 0
first: 1000, skip: 1000
first: 1000, skip: 2000
```
