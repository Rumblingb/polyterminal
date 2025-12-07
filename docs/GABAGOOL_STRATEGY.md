# GABAGOOL22 Strategy Analysis

**Wallet:** `0x6031b6eed1c97e853c6e0f03ad3ce3529351f96d`
**Profile:** https://polymarket.com/@gabagool22
**Analysis Date:** December 7, 2025

## Summary

| Metric | Value |
|--------|-------|
| Total Profit | **$191,356** |
| Volume Traded | $21.2M |
| Total Trades | 9,446 |
| Joined | October 2025 |
| Strategy | **Passive Limit Order Market Making** |

## The Strategy: Passive Limit Order Arbitrage

**This is NOT speed-based arb sniping.** Gabagool posts passive limit orders on both UP and DOWN, lets volatility fill them, and collects at resolution.

### The Core Insight

In a binary market, UP + DOWN must = $1.00 at resolution.

If you post limit BUY orders where:
```
UP_bid + DOWN_bid < 1.00
```

And both sides fill → **guaranteed profit regardless of outcome**.

### Why Limit Orders, Not Market Orders

| Approach | Combined Cost | Fees | Result |
|----------|---------------|------|--------|
| Taker (market buy) | UP_ask + DOWN_ask = 1.02 | +0.5% | **LOSS** |
| Maker (limit buy) | UP_bid + DOWN_bid = 0.97 | -rebate | **3% PROFIT** |

Gabagool isn't racing to hit asks. He's posting bids and waiting for retail to sell into him.

---

## Execution Model (Verified from 20,000 trades)

### Trade Breakdown

| Role | Side | Count | Volume | Purpose |
|------|------|-------|--------|---------|
| **Maker** | BUY | 10,000 (100%) | $47,753 | Post limit orders on both sides |
| **Maker** | SELL | 0 (0%) | $0 | - |
| **Taker** | BUY | 1,846 (18%) | $9,678 | Rebalance when one side light |
| **Taker** | SELL | 8,154 (82%) | $37,683 | Partial exits / rebalancing |

**Key insight:** 100% of maker orders are BUYS. He's always posting bids, never asks.

### The Four Phases

```
PHASE 1: ACCUMULATE (Minutes 0-4)
├── Post limit BUY on UP at best_bid
├── Post limit BUY on DOWN at best_bid
├── Combined < 0.98 = edge locked in
└── Let retail sell into your bids

PHASE 2: REBALANCE (Minutes 4-10)
├── Track: UP_shares vs DOWN_shares
├── If imbalance > 20%:
│   ├── Taker BUY the light side, OR
│   └── Taker SELL the heavy side
└── Target: <10% imbalance

PHASE 3: HOLD (Minutes 10-15)
└── Do nothing, wait for resolution

PHASE 4: RESOLUTION
├── Winning side pays $1.00 per share
├── Losing side pays $0
└── Profit = matched_shares × (1 - combined_bid)
```

### Timing Pattern (Maker Buys)

```
Minute  0:  951 trades  ████████████████████
Minute  1:  935 trades  ███████████████████
Minute  2:  950 trades  ███████████████████
Minute  3:  948 trades  ██████████████████
Minute  4:  698 trades  █████████████
Minute  5:  651 trades  █████████████
...
Minute 11:  432 trades  ████████
Minute 14:  302 trades  ██████
```

**Done by minute 4.** Posts orders early when spreads are fattest, then lets volatility fill.

### Timing Pattern (Taker Sells - Exits)

```
Minute  0: 1021 exits  ██████████████████████████████████
Minute  1:  861 exits  ████████████████████████████
Minute  5:  684 exits  ██████████████████████
Minute 10:  482 exits  ████████████████
Minute 14:  117 exits  ███
```

Exits happen throughout the window - partial profit-taking and rebalancing.

---

## Position Analysis

### Balance Verification

| Metric | Value |
|--------|-------|
| Windows analyzed | 20 |
| Avg position per side | 1,308 shares |
| Avg imbalance | 102 shares (7.8%) |
| Well-balanced windows (<20% imbalance) | **95%** |

He keeps UP and DOWN positions within 10% of each other.

### Bid Price Distribution (Where He Posts)

| Price Range | Fills | % | Volume |
|-------------|-------|---|--------|
| $0.00-0.30 | 2,107 | 21% | $3,662 |
| $0.30-0.40 | 1,415 | 14% | $4,555 |
| $0.40-0.50 | 1,870 | 19% | $8,282 |
| $0.50-0.60 | 1,692 | 17% | $9,243 |
| $0.60-0.70 | 1,291 | 13% | $8,422 |
| $0.70-0.80 | 976 | 10% | $7,572 |

**Sweet spot: $0.40-0.60** - where the arb lives.

### Top Bid Levels

| Price | Fills | Volume |
|-------|-------|--------|
| $0.54 | 240 | $1,338 |
| $0.44 | 239 | $1,044 |
| $0.45 | 230 | $1,061 |
| $0.55 | 221 | $1,293 |
| $0.43 | 218 | $892 |

---

## Profit Calculation

### Per Window Economics

| Metric | Value |
|--------|-------|
| Avg shares per side | 3,339 |
| Avg combined bid | 0.97 |
| Edge per share | $0.03 |
| Profit per window | ~$54 |
| Windows per hour | 4 |
| **Hourly profit** | **~$216** |

### Why It Works

1. **No speed required** - Passive limit orders, not latency racing
2. **Retail flow** - Directional bettors sell into your bids when scared
3. **Volatility is your friend** - Price swings fill both sides
4. **Resolution guarantee** - One side ALWAYS pays $1.00

---

## Execution Example

### Live Trace (11:05:19)

```
Time       Price   Shares  Token (truncated)
11:05:19   0.251     12.0  101597841760237... (DOWN)
11:05:19   0.410     12.0  110927946818744... (?)
11:05:19   0.280      5.0  101597841760237... (DOWN)
11:05:19   0.560      6.0  885218706809207... (UP)
11:05:19   0.570     12.0  885218706809207... (UP)
11:05:19   0.570     12.0  885218706809207... (UP)
11:05:19   0.580     12.0  885218706809207... (UP)
11:05:19   0.290     12.0  101597841760237... (DOWN)
```

**Combined:** UP @ 0.57 + DOWN @ 0.27 = **0.84 (16% edge!)**

37 fills in one second across multiple tokens. Not racing - just getting filled on posted bids.

---

## What This Is NOT

| Myth | Reality |
|------|---------|
| HFT speed game | Passive limit orders, no latency race |
| Taker arb sniping | Maker-only accumulation |
| Prediction/direction | Market-neutral, both sides |
| Complex ML model | Simple combined price check |
| Massive infrastructure | WebSocket + order posting |

---

## How to Replicate

### The Algorithm

```python
def on_new_window(up_token, down_token):
    # check if arb exists
    up_bid = get_best_bid(up_token)
    down_bid = get_best_bid(down_token)
    combined = up_bid + down_bid

    if combined < 0.98:  # 2% min edge
        # post and forget
        post_limit_buy(up_token, price=up_bid, shares=500)
        post_limit_buy(down_token, price=down_bid, shares=500)

        log(f"posted bids: combined={combined:.3f}, edge={1-combined:.1%}")

def on_fill(token, shares, side):
    # track position
    update_position(token, shares, side)

    # check balance
    up_pos = get_position(UP_TOKEN)
    down_pos = get_position(DOWN_TOKEN)
    imbalance = abs(up_pos - down_pos) / max(up_pos, down_pos, 1)

    if imbalance > 0.20:  # >20% imbalanced
        rebalance()

def rebalance():
    up_pos = get_position(UP_TOKEN)
    down_pos = get_position(DOWN_TOKEN)

    if up_pos > down_pos:
        # buy more DOWN or sell some UP
        taker_buy(DOWN_TOKEN, up_pos - down_pos)
    else:
        # buy more UP or sell some DOWN
        taker_buy(UP_TOKEN, down_pos - up_pos)
```

### Infrastructure Required

1. **Polymarket API key** - to post orders
2. **WebSocket connection** - monitor orderbook
3. **Position tracker** - track UP vs DOWN shares
4. **Rebalancing logic** - keep positions within 20%

### What You DON'T Need

- Sub-millisecond latency
- Co-location
- Complex pricing models
- Massive capital (start with $1k per side)
- ML/prediction

---

## Risks

| Risk | Mitigation |
|------|------------|
| One side fills, other doesn't | Rebalance with taker order |
| Combined > 1.00 (no edge) | Don't post, wait for opportunity |
| Competition increases | Edge compresses, still profitable |
| API/execution issues | Position limits, monitoring |

---

## Key Takeaways

1. **Passive, not active** - Post bids and wait
2. **First 4 minutes** - When spreads are fattest
3. **Both sides always** - Never directional
4. **Balance religiously** - Keep within 10-20%
5. **Let volatility work** - Swings fill your orders
6. **Hold to resolution** - Don't exit early unless rebalancing

The strategy is **harvesting volatility through passive limit orders** on both sides of a binary market.

---

*Deep analysis from on-chain trade data, December 2025*
