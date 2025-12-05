# Polymarket 15-Minute Market Analysis

## Data Collection Session: 2025-12-05 05:30 UTC

### Raw Data Captured

**Window: btc-updown-15m-1764912600 (05:30-05:45 UTC)**
**Start Price: $91,986.97**

```
Time     | BTC Price  | %Chg    | UP Bid/Ask
---------|------------|---------|------------
05:30:03 | $91,986.97 | +0.000% | 0.52/0.53
05:30:06 | $91,983.09 | -0.004% | 0.50/0.53
05:30:09 | $91,968.76 | -0.020% | 0.48/0.49
05:30:13 | $91,963.21 | -0.026% | 0.47/0.49
05:30:16 | $91,944.98 | -0.046% | 0.42/0.44
05:30:19 | $91,944.97 | -0.046% | 0.41/0.42
05:30:22 | $91,936.33 | -0.055% | 0.39/0.40
05:30:32 | $91,936.32 | -0.055% | 0.38/0.39
05:30:39 | $91,924.17 | -0.068% | 0.34/0.35
05:30:45 | $91,916.94 | -0.076% | 0.33/0.34
05:31:32 | $91,903.12 | -0.091% | 0.28/0.29
05:31:52 | $91,896.72 | -0.098% | 0.25/0.26
05:32:02 | $91,880.13 | -0.116% | 0.22/0.23
05:32:15 | $91,880.13 | -0.116% | 0.21/0.23
05:32:28 | $91,891.60 | -0.104% | 0.27/0.28
05:32:48 | $91,919.52 | -0.073% | 0.30/0.31
05:33:08 | $91,927.80 | -0.064% | 0.34/0.35
05:33:54 | $91,906.59 | -0.087% | 0.28/0.29
05:34:40 | $91,922.67 | -0.070% | 0.31/0.32
```

---

## Key Findings

### 1. Market Maker Response Time: 3-6 SECONDS

The Polymarket order book updates within 3-6 seconds of BTC price changes on Binance.

**Evidence:**
- 05:30:03: BTC at start price → UP at 0.52
- 05:30:09: BTC drops 0.02% → UP already at 0.48 (6 seconds later)
- 05:30:16: BTC drops 0.046% → UP at 0.42 (13 seconds total)

**Implication:** Edge window is measured in SECONDS, not minutes.

### 2. Spread: Consistently 1-2 Cents

Throughout the entire session:
- Spread ranged from $0.01 to $0.02
- Never saw spread > $0.03

**Implication:** Round-trip cost is 2-4%. Need significant price movement to profit.

### 3. Price Correlation is Tight

| BTC % Change | Expected UP | Actual UP | Difference |
|--------------|-------------|-----------|------------|
| -0.02% | ~0.45 | 0.48 | +0.03 |
| -0.05% | ~0.35 | 0.40 | +0.05 |
| -0.07% | ~0.30 | 0.34 | +0.04 |
| -0.10% | ~0.20 | 0.26 | +0.06 |
| -0.12% | ~0.15 | 0.22 | +0.07 |

**Observation:** Poly prices are slightly HIGHER than theoretical fair value when BTC drops. This could indicate:
- Mean reversion expectation by MMs
- Risk premium
- Slight inefficiency (~5-7 cents)

### 4. Volatility Within Window

Price bounced significantly during the 4-minute observation:
- UP ranged from 0.21 to 0.52
- BTC ranged from $91,880 to $91,987 (-0.12% to 0%)

**Implication:** Even within a window, there are multiple trading opportunities as BTC oscillates.

### 5. Liquidity at Best Price

From earlier order book snapshots:
- Best bid/ask size: 50-400 shares typically
- Depth within 5 cents: ~$4,600 per side
- Market impact for $1000 order: ~2-3 cents slippage

---

## Strategy Implications

### What DOESN'T Work:
1. **Simple threshold strategy (0.1% move → buy)** - Market already priced in by the time we react
2. **Polling every 60 seconds** - Way too slow
3. **Assuming 50/50 starting price** - Markets start at 0.52/0.53, not 0.50

### What MIGHT Work:

#### A. Sub-Second Arbitrage
- Need Binance WebSocket (not REST polling)
- Need pre-signed Polymarket orders
- React within 1-2 seconds of BTC move
- Target: Capture 3-5 cent mispricing
- Requires: Co-located servers near both Binance and Polymarket

#### B. Mean Reversion During Extremes
- When UP hits 0.20-0.25 (or 0.75-0.80), bet on reversion
- BTC often oscillates, doesn't go straight to resolution
- Evidence: In our data, UP went from 0.21 back to 0.35

#### C. Late Window Momentum
- With 2-3 minutes left, if price is at extreme (0.1 or 0.9), it's likely correct
- Buy the winning side at 0.90, sell at 0.99
- Lower risk, lower reward

---

## Technical Requirements for Live Trading

### Latency Requirements:
- Binance → Your Server: <50ms
- Your Server → Polymarket: <100ms
- Total round-trip: <200ms
- **Implication:** Need AWS us-east-1 (Polymarket) or co-location

### Order Execution:
- Must use CLOB API with pre-authenticated sessions
- Consider using FOK (fill-or-kill) orders to avoid partial fills
- Need to handle order signing without blocking

### Data Infrastructure:
- Binance WebSocket for real-time BTC price
- Polymarket WebSocket (if available) or fast polling
- Record all data for analysis

---

## Next Steps

1. **Build proper recorder** - Capture every tick for multiple windows
2. **Analyze historical trades** - Get trade-level data from CLOB API
3. **Quantify the edge** - Backtest on recorded data
4. **Measure latency** - Time each component
5. **Paper trade with realistic execution** - Account for latency + slippage

---

## Questions to Answer

1. Is there a Polymarket WebSocket for order book updates?
2. What's the actual latency from order submission to fill?
3. How many other bots are competing for this edge?
4. Does the edge persist across different volatility regimes?
5. What's the maximum position size before moving the market?
