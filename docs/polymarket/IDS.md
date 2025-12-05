# Polymarket IDs

## Types

| id | format | example |
|----|--------|---------|
| condition_id | 0x hex (66 chars) | 0x4319532e181605cb15b1bd677759a3bc7f7394b2fdf145195b700eeaedfd5221 |
| token_id / clob_token_id | large integer string | 52114319203203390135646099429968699951953007642634352528440270426113016916689 |
| question_id | 0x hex | 0x... |
| slug | url-friendly string | btc-updown-15m-1733382000 |

## Structure

```
Event (group of markets)
└── Market (single question)
    ├── Token 0 (clobTokenIds[0]) - usually Yes/Up
    └── Token 1 (clobTokenIds[1]) - usually No/Down
```

## 15m Markets Slug Format

`{coin}-updown-15m-{unix_timestamp}`

timestamp = window start time in unix seconds

examples:
- btc-updown-15m-1733382000
- eth-updown-15m-1733382000
- sol-updown-15m-1733382000
