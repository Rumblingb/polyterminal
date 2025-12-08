# GABAGOOL22 Strategy Analysis (CORRECTED)

**Wallet:** `0x6031b6eed1c97e853c6e0f03ad3ce3529351f96d`
**Profile:** https://polymarket.com/@gabagool22
**Analysis Date:** December 7, 2025
**Data Source:** 493,343 trades over 7 days via Goldsky Subgraph + Gamma API metadata

## Summary

| Metric | Value |
|--------|-------|
| Total Profit | **$191,356** (profile) |
| 7-Day Expected P&L | **$23,800** |
| Volume Traded | $2.42M (7 days) |
| Total Trades | 493,343 |
| Markets | **BTC + ETH only** (no SOL) |
| Windows Traded | 1,335 |
| Strategy | **Dual-Sided Inventory Accumulation** |

## The Strategy: Accumulate Matched Pairs, Hold to Resolution

**NOT "spread capture" or "market making exits".** Gabagool accumulates inventory on both UP and DOWN, holds to resolution, and profits from the arb spread.

### The Core Mechanic

In a binary market, UP + DOWN = $1.00 at resolution.

If you buy both sides where combined entry < $1:
```
UP_entry + DOWN_entry < 1.00
```

At resolution: one side pays $1, the other $0.
**Profit = matched_shares × (1 - combined_entry)**

### The Reality Check

| Metric | Value |
|--------|-------|
| Windows with arb (combined < $1) | **72.7%** |
| Windows LOSING (combined >= $1) | **27.3%** |
| Avg combined entry | $0.993 |
| Avg arb margin when profitable | 4.51% |

**NOT "guaranteed profit" - 27% of windows are underwater.**

---

## Execution Model (Verified from 493,343 trades)

### Trade Breakdown (7-day, 493k trades)

| Role | Side | Count | Volume | % |
|------|------|-------|--------|---|
| **Maker** | BUY | 350,044 | $1.62M | 92.3% of buys |
| **Taker** | BUY | 29,262 | $265k | 7.7% of buys |
| **Maker** | SELL | 0 | $0 | 0% |
| **Taker** | SELL | 113,869 | $531k | 100% of sells |

**Key insight:**
- Buys: 92% maker (posting bids), 8% taker (rebalancing)
- Sells: 100% taker (aggressive exits/rebalancing)
- **Only sells 30% of what he buys** - accumulating inventory

### Order Sizing (Verified)

| Metric | Value |
|--------|-------|
| Avg trade size | **$4.97** |
| Median trade size | **$4.40** |
| Max trade size | **$26.25** |
| Avg shares/trade | **10.1** |

**He sprays tiny orders.** Not big lumpy bets - hundreds of small $5 limit buys across price levels.

### The Four Phases

```
PHASE 1: ACCUMULATE (Seconds 15-45, Minutes 0-4)
├── Wait ~15 sec for spreads to settle
├── Post limit BUY on UP at best_bid
├── Post limit BUY on DOWN at best_bid
├── Combined < 0.98 = edge locked in
└── Let retail sell into your bids

PHASE 2: CONTINUE ACCUMULATING (Minutes 4-9)
├── Activity tapers but still posting
├── 80% of fills happen by minute 9
└── Track position balance

PHASE 3: REBALANCE IF NEEDED (Minutes 9-14)
├── Track: UP_shares vs DOWN_shares
├── If imbalance > 20%:
│   ├── Taker BUY the light side, OR
│   └── Taker SELL the heavy side
└── Target: <10% imbalance

PHASE 4: RESOLUTION
├── Winning side pays $1.00 per share
├── Losing side pays $0
└── Profit = matched_shares × (1 - combined_bid)
```

### Timing Pattern (Maker Buys) - Minute Level

```
Minute  0:  11,086 (8.5%)   ██████████████████████████████████
Minute  1:  12,732 (9.7%)   ████████████████████████████████████████  ← PEAK
Minute  2:  11,630 (8.9%)   ████████████████████████████████████
Minute  3:  11,787 (9.0%)   █████████████████████████████████████
Minute  4:  10,570 (8.1%)   ─────────────────────────────────
Minute  5:  10,127 (7.8%)   ───────────────────────────────
Minute  6:   9,924 (7.6%)   ───────────────────────────────
Minute  7:   9,485 (7.3%)   █████████████████████████████
Minute  8:   9,333 (7.1%)   █████████████████████████████
Minute  9:   7,587 (5.8%)   ───────────────────────
Minute 10:   7,825 (6.0%)   ████████████████████████
Minute 11:   5,666 (4.3%)   █████████████████
Minute 12:   4,698 (3.6%)   ██████████████
Minute 13:   4,322 (3.3%)   █████████████
Minute 14:   3,831 (2.9%)   ████████████
```

**Cumulative:** First 4 min = 36%, By minute 9 = 80% done.

### Timing Pattern (Maker Buys) - Second Level (First 60 sec)

```
Sec  0-4:     81  ██           ← slow start, wait for spreads
Sec  5-9:    402  ██████████████
Sec 10-14:   244  █████████
Sec 15-19:   981  ████████████████████████████████████
Sec 20-24: 1,043  ██████████████████████████████████████
Sec 25-29: 1,537  ████████████████████████████████████████████████████████  ← PEAK
Sec 30-34: 1,035  ██████████████████████████████████████
Sec 35-39: 1,583  ██████████████████████████████████████████████████████████  ← PEAK
Sec 40-44:   911  █████████████████████████████████
Sec 45-49: 1,307  ████████████████████████████████████████████████
Sec 50-54:   791  █████████████████████████████
Sec 55-59: 1,171  ███████████████████████████████████████████
```

**Key insight:** Waits ~15 sec for market to settle, then sprays bids from **sec 15-45**. Peak at **25-40 seconds**.

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

### Entry Price Distribution (Verified from 130k+ Maker Buys)

| Price Range | Fills | % | Cumulative |
|-------------|-------|---|------------|
| $0.00-0.20 | 12,627 | 11.4% | 11.4% |
| $0.20-0.35 | 19,287 | 17.4% | 28.8% |
| $0.35-0.50 | 26,948 | 24.4% | 53.2% |
| $0.50-0.65 | 24,573 | 22.2% | 75.4% |
| $0.65-0.80 | 17,373 | 15.7% | 91.1% |
| $0.80-1.00 | 9,827 | 8.9% | 100% |

**Avg entry price: $0.481** - right in the middle where arb exists.

**Sweet spot: $0.35-0.65** (46.6% of fills) - where combined_bid < 1.00 is most common.

### Top Entry Price Levels

| Price | Fills | Volume |
|-------|-------|--------|
| $0.44 | 2,061 | $8,665 |
| $0.43 | 1,986 | $8,259 |
| $0.47 | 1,985 | $9,192 |
| $0.48 | 1,927 | $8,899 |
| $0.46 | 1,846 | $8,553 |
| $0.56 | 1,820 | $10,730 |
| $0.45 | 1,811 | $8,108 |

Clusters around **$0.43-0.48** - the sweet spot for balanced fills.

---

## Profit Calculation (CORRECTED)

### 7-Day P&L Breakdown

| Metric | Value |
|--------|-------|
| USDC spent on buys | $1,887,299 |
| USDC received from sells | $531,380 |
| **Net USDC flow** | **-$1,355,919** |
| Net shares held | 2,759,243 |

### Resolution Value (Expected)

| Scenario | P&L |
|----------|-----|
| If ALL windows go UP | $31,456 |
| If ALL windows go DOWN | $15,948 |
| **Expected (50/50)** | **$23,702** |

### Profit Sources

| Source | Amount | % of P&L |
|--------|--------|----------|
| Matched pair arb | $22,404 | 94% |
| Unmatched UP exposure | $1,179 | 5% |
| Unmatched DOWN exposure | $219 | 1% |
| **TOTAL** | **$23,802** | 100% |

**Daily:** ~$3,400 | **Hourly:** ~$142

### Why It Works

1. **Accumulation, not trading** - Hold inventory to resolution
2. **Combined entry < $1** - Works 73% of the time
3. **Matched pairs = locked profit** - One side ALWAYS pays $1
4. **Unmatched is coin flip** - Small edge at fair prices

### The Key Insight: Early Window = Two-Way Flow

The strategy works because **direction is unclear in seconds 15-45**:

```
Early window (sec 15-45, min 0-4):
  - Price near 50/50, direction uncertain
  - SELL flow happens on BOTH sides (scalpers, traders, etc.)
  - Your bids on both UP and DOWN get filled
  - Result: Balanced fills at combined < $1.00

Late window (min 10+):
  - Direction becomes clear (one side ~$0.90+)
  - Losers dump → adverse selection kicks in
  - But you're already positioned from early fills
```

This is why gabagool does **36% of trades in first 4 minutes** and **80% by minute 9**. Get in while the market is uncertain and fills are balanced.

**The risk** is unbalanced fills late in the window. This is why rebalancing exists - to fix any imbalance before resolution.

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
| Big orders | Tiny $5 avg orders sprayed everywhere |
| Massive infrastructure | WebSocket + order posting |

---

## How to Replicate

### The Algorithm

```python
def on_new_window(up_token, down_token):
    # wait for spreads to settle
    sleep(15)  # don't rush - gabagool waits 15-20 sec

    # check if arb exists
    up_bid = get_best_bid(up_token)
    down_bid = get_best_bid(down_token)
    combined = up_bid + down_bid

    if combined < 0.98:  # 2% min edge
        # spray small orders at multiple price levels
        for offset in [0, -0.01, -0.02]:
            post_limit_buy(up_token, price=up_bid + offset, shares=10)
            post_limit_buy(down_token, price=down_bid + offset, shares=10)

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

### Key Parameters (from on-chain data)

| Parameter | Value |
|-----------|-------|
| Wait before posting | ~15 seconds |
| Order size | ~$5 / ~10 shares |
| Entry price target | $0.43-0.48 range |
| Stop posting after | Minute 9 (80% done) |
| Rebalance threshold | >20% imbalance |
| Min edge required | 2% (combined < 0.98) |

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
| Late window adverse selection | Get 80% done by minute 9 |

---

## Risk Mitigation (Backtest Findings)

**Problem:** Real market flow is skewed. In our backtest, BTC had 3.5:1 BUY:SELL ratio and heavy DOWN sells - fills are NOT naturally balanced.

### Backtest Results (Window 1765155600)

| Strategy | P&L | Loss Reduction |
|----------|-----|----------------|
| No hedging | -$59.61 | baseline |
| Dynamic sizing only | -$16.53 | 72% |
| Dynamic sizing + taker rebalance | -$8.42 | **86%** |

### 1. Dynamic Sizing (Critical)

Stop bidding the heavy side, increase bids on light side:

```python
# check position imbalance on each fill
up_shares = positions[coin]['up_shares']
down_shares = positions[coin]['down_shares']
total = up_shares + down_shares

if total > 0:
    imbalance = (down_shares - up_shares) / total  # positive = more DOWN

if imbalance > 0.2:      # too much DOWN
    up_fill_rate = 0.2   # aggressive on UP
    down_fill_rate = 0.0 # STOP taking DOWN
elif imbalance < -0.2:   # too much UP
    up_fill_rate = 0.0
    down_fill_rate = 0.2
else:                    # balanced
    up_fill_rate = 0.1
    down_fill_rate = 0.1
```

### 2. Taker Rebalancing (When Dynamic Sizing Isn't Enough)

If imbalance hits 30%, market buy the light side:

```python
if abs(imbalance) > 0.3 and total > 10:
    if imbalance > 0:  # too much DOWN, need UP
        rebal_size = (down_shares - up_shares) * 0.5
        taker_buy(UP_TOKEN, rebal_size, at=best_ask)
    else:              # too much UP, need DOWN
        rebal_size = (up_shares - down_shares) * 0.5
        taker_buy(DOWN_TOKEN, rebal_size, at=best_ask)
```

**Cost:** You pay the spread (~2-3%), but guarantee balance.

### 3. Skip Bad Windows

If early flow (first 60 sec) is heavily one-sided:
- Check SELL flow balance: `up_sells / down_sells`
- If ratio > 3:1 either direction, consider skipping window
- Correlated moves (all coins same direction) = high risk

### 4. Position Limits

Don't over-concentrate:
- Max position per coin: $500 per side
- Max total exposure: $2000 across all coins
- If rebalance cost > expected edge, exit position

### Worst Case Scenario

Even with perfect hedging, bad windows happen:
- All 4 coins went UP in our test window
- DOWN flow dominated (people selling DOWN = betting UP)
- Result: -$8.42 with all mitigations active

**Expected value:** Over many windows, direction is ~50/50. Hedging turns catastrophic losses (-$60) into small scratches (-$8). Edge compounds over time.

---

## Key Takeaways

1. **Wait 15 seconds** - Let spreads settle before posting
2. **Spray tiny orders** - $5 avg, 10 shares, across price levels
3. **Target $0.43-0.48** - Where arb lives
4. **First 4 minutes** - 36% of activity, when spreads are fattest
5. **Done by minute 9** - 80% filled before adverse selection kicks in
6. **Both sides always** - Never directional
7. **Balance religiously** - Keep within 10-20%
8. **Let volatility work** - Swings fill your orders
9. **Hold to resolution** - Don't exit early unless rebalancing

The strategy is **harvesting volatility through passive limit orders** on both sides of a binary market.

---

---

## Per-Window Analysis (with Gamma API Metadata)

### Markets Traded

Only **BTC** and **ETH** 15-minute up/down markets (no SOL):

| Coin | Tokens | UP Buys | DOWN Buys | UP Sells | DOWN Sells |
|------|--------|---------|-----------|----------|------------|
| BTC | 164 | 15,477 | 15,584 | 4,792 | 5,055 |
| ETH | 161 | 8,079 | 7,938 | 2,359 | 2,673 |

### Combined Bid Analysis (The Core Arb)

In binary markets: **UP + DOWN = $1 at resolution**

If combined bid < $1, you lock in profit regardless of outcome.

| Metric | Value |
|--------|-------|
| Windows with both UP+DOWN buys | 142 |
| **Windows with combined < $1** | **107 (75.4%)** |
| Avg arb margin when < $1 | **4.91%** |
| Min combined bid | $0.729 |
| Max combined bid | $1.357 |
| Avg combined bid | $0.983 |

**3/4 of windows have guaranteed profit baked in.**

### UP vs DOWN Spreads (Exit - Entry)

| Token | Avg Entry | Avg Exit | Spread |
|-------|-----------|----------|--------|
| BTC UP | $0.461 | $0.510 | **+4.88%** |
| BTC DOWN | $0.509 | $0.498 | **-1.02%** |
| ETH UP | $0.449 | $0.493 | **+4.35%** |
| ETH DOWN | $0.524 | $0.488 | **-3.53%** |

**Key insight:** Makes money on UP tokens, loses on DOWN. This suggests gabagool's exits are more profitable when the market trends UP (more winning UP positions to sell).

### Timing Within Windows

Where in the 15-min window do trades happen?

| Window Position | BUY | SELL |
|-----------------|-----|------|
| 0-25% (0-3:45) | 9,826 | 4,165 |
| 25-50% (3:45-7:30) | 9,952 | 3,262 |
| 50-75% (7:30-11:15) | 7,565 | 2,532 |
| 75-100% (11:15-15:00) | 3,491 | 1,138 |

**64% of buys in first half, only 11% in final quarter.**

### Window Patterns

What happens in each window:

| Pattern | Windows | % |
|---------|---------|---|
| BUY both + SELL both | 142 | 85.5% |
| BUY UP only + SELL both | 8 | 4.8% |
| SELL only (no buys) | 6 | 3.6% |
| BUY DOWN only + SELL both | 5 | 3.0% |

**85% of windows have the full cycle: bid on both, fill on both, sell on both.**

### Sample Window Breakdown

**BTC @ 12/31 16:00** (12,133 trades):
```
BUY UP:    4,790 @ $0.427  ($22,300)
SELL UP:   1,256 @ $0.503  ($6,668)
BUY DOWN:  4,813 @ $0.551  ($28,747)
SELL DOWN: 1,274 @ $0.537  ($7,244)

Combined entry: $0.427 + $0.551 = $0.978 (2.2% edge)
```

### P&L Estimate (Enriched Sample)

| Metric | Value |
|--------|-------|
| USDC spent on buys | $228,627 |
| USDC received on sells | $70,238 |
| Net shares held | 324,830 |
| Matched UP+DOWN pairs | 226,115 |
| **Estimated arb profit** | **$3,747** |
| Avg profit per share | $0.017 |

*Based on 62k enriched trades (12.5% of total). Full dataset would scale ~8x.*

---

## Data Sources

- **On-chain:** Goldsky Subgraph orderbook-subgraph/0.0.1
- **Trades analyzed:** 493,343 trades (7-day window)
- **Enriched with metadata:** 61,957 trades via Gamma API
- **Query filter:** `maker/taker: 0x6031b6eed1c97e853c6e0f03ad3ce3529351f96d`

*Deep analysis from on-chain trade data + Gamma API market metadata, December 2025*
