# Sharky6999 Strategy Analysis

wallet: `0x751a2b86cab503496efd325c8344e10159349ea1`

## Stats

- 98,255 total trades
- $75M+ buy volume
- 99.5% win rate
- ~$100K/month profit

## The Strategy: "Tail-End" / "Certainty Buying"

### Core Concept

buy outcomes that are already 99%+ locked, wait for resolution, collect $1.00

```
buy @ $0.99 → resolve @ $1.00 = 1% profit
buy @ $0.995 → resolve @ $1.00 = 0.5% profit
```

### Why It Works

1. **No prediction required** - outcome is already known
2. **Near-zero risk** - only black swan events can ruin it
3. **High volume** - $300K+ per position, dozens of times daily
4. **Speed** - get in before price hits exactly $1.00

### Market Types

| Category | Volume | Markets |
|----------|--------|---------|
| Sports | 60%+ | NFL, NBA, UFC, college football |
| Crypto | 30%+ | BTC/ETH price targets |
| Politics | <5% | elections, events |

### Timing Patterns

- **Peak days**: Saturday, Sunday (sports events resolve)
- **Peak hours**: 18:00-21:00 UTC (US primetime sports)
- **Strategy**: trade when games end but markets haven't resolved yet

## The Edge

The gap between:
- **OUTCOME KNOWN** (game ends, score is final)
- **MARKET RESOLVES** (Polymarket officially settles)

During this window, you can buy at 99%+ odds with near certainty.

## Implementation

### Step 1: Find Opportunities

```python
# markets ending in 24h with 95%+ on one outcome
from datetime import datetime, timezone
import requests, json

markets = requests.get(
    "https://gamma-api.polymarket.com/markets",
    params={"closed": "false", "limit": 500}
).json()

now = datetime.now(timezone.utc)

for m in markets:
    end = datetime.fromisoformat(m['endDate'].replace('Z', '+00:00'))
    hours_left = (end - now).total_seconds() / 3600

    if 0 < hours_left < 24:
        prices = json.loads(m.get('outcomePrices', '[]'))
        if prices:
            max_price = max(float(p) for p in prices)
            if max_price >= 0.95:
                print(f"{hours_left:.1f}h | {max_price:.3f} | {m['question'][:50]}")
```

### Step 2: Verify Outcome is Locked

for sports: check live scores API
for crypto: check current BTC/ETH price
for weather: check weather API

if outcome is locked AND price < 0.995 → opportunity exists

### Step 3: Execute Trade

use py-clob-client or direct CLOB API with auth

### Step 4: Wait for Resolution

markets resolve automatically, shares redeem at $1.00

## Risk Management

from twitter analysis of similar traders:

1. **Max 10% per position** - never all-in
2. **Prioritize 99.7%+ odds** - higher certainty
3. **Prefer short time windows** - less black swan exposure
4. **Monitor for reversals** - sports comebacks, price swings

## Required Data Sources

| Need | Source |
|------|--------|
| Market discovery | Gamma API |
| Real-time prices | WebSocket / CLOB API |
| Live scores | ESPN/odds APIs |
| Crypto prices | Binance/CoinGecko API |
| Trade execution | py-clob-client |

## Sharky's Top Markets by Volume

from our analysis:

1. **Packers vs. Lions** - $390K
2. **Oklahoma State vs Northwestern** - $390K
3. **Bitcoin $122K September** - $325K
4. **Rockets vs. Cavaliers** - $350K
5. **NYC Mayor Election** - $400K

pattern: high-liquidity sports and crypto price targets

## Bot Architecture

```
┌─────────────────┐
│ Market Scanner  │ ← Gamma API (every 1 min)
│ - endDate < 24h │
│ - price > 0.95  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Verification    │ ← External APIs
│ - sports scores │
│ - crypto prices │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Price Check     │ ← WebSocket
│ - price < 0.998 │
│ - liquidity ok  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Execution       │ ← CLOB API
│ - market order  │
│ - max position  │
└─────────────────┘
```

## Key Insight

sharky doesn't predict anything. he waits for certainty, then captures the tiny remaining spread at massive volume.

this is more like market making than gambling.
