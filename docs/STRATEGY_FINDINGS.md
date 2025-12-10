# Strategy Findings

Analysis based on 19 windows (~4.75 hours) of collected data from Dec 8-10, 2025.

## Market Structure

### How It Works
- 15-minute windows, markets resolve UP or DOWN based on Chainlink price feed
- Each coin has UP and DOWN tokens (binary outcome)
- Combined price should equal 1.0 (UP + DOWN = 1.0)
- Edge exists when combined bid < 1.0 (you can buy both sides for less than guaranteed payout)

### Resolution Outcomes (64 samples)
```
UP:   37 (58%)
DOWN: 27 (42%)
```

---

## Edge Analysis

### Book Edge by Coin (from price_change events)
| Coin | Avg Edge | Min | Max | Liquidity |
|------|----------|-----|-----|-----------|
| XRP | 3.86% | 2.43% | 4.88% | Lowest |
| SOL | 3.05% | 1.10% | 4.32% | Low |
| ETH | 1.50% | 0.89% | 2.15% | Medium |
| BTC | 1.08% | 0.46% | 1.77% | Highest |

**Inverse relationship**: higher edge = lower liquidity = harder to fill both sides.

### Realized Edge (from actual fills at bid)
When looking at SELL trades that execute AT the best_bid:
```
BTC avg realized edge: +2.3%
Positive windows: 14/19 (74%)
```

Edge varies significantly per window (-5% to +15%) due to price movement during accumulation.

---

## Trade Flow Analysis

### BUY vs SELL Ratio
| Coin | Total Trades | SELL | BUY | Ratio |
|------|--------------|------|-----|-------|
| BTC | 45,667 | 9,193 | 36,474 | 4:1 |
| ETH | 20,463 | 4,856 | 15,607 | 3:1 |
| SOL | 6,071 | 1,530 | 4,541 | 3:1 |
| XRP | 4,335 | 1,058 | 3,277 | 3:1 |

**Implication**: More people buying (taking liquidity) than selling. Market makers get filled on SELL flow.

### SELL Trade Price Distribution (BTC)
```
Price   Volume
0.1-0.2   11%
0.2-0.3   10%
0.3-0.4   11%
0.4-0.5   15%  <- mid
0.5-0.6   19%  <- most volume
0.6-0.7   13%
0.7-0.8    8%
0.8-0.9   10%
```

SELLs happen across all price levels, not just at bid. Fills depend on where you post.

### Fills at Bid vs Away
```
BTC UP:   at bid (±1c) = 2,265 shares | near bid (±3c) = 1,670 shares
BTC DOWN: at bid (±1c) = 3,809 shares | near bid (±3c) = 2,494 shares
```

~60% of SELL volume executes at or very near the best bid.

---

## Queue Dynamics

### Book Depth at Best Bid
```
BTC UP:   avg 387 shares, max 36,005
BTC DOWN: avg 667 shares, max 10,003
```

With $50 capital (~100 shares at 0.50), you're competing against 400-700 shares ahead of you.

### Fill Mechanism
Large SELL orders sweep through the bid queue. Back-of-queue gets filled when:
1. Someone market sells a large order
2. The order size exceeds the depth ahead of you
3. You capture the overflow

---

## Strategy Backtests

### 1. Naive Simulation (assumed 10% fill rate)
```
BTC: $46/19 windows = $233/day
All coins: $104/19 windows = $528/day
ROI: ~2.6%
```

### 2. Queue-Based Simulation (real book depth)
Using actual book snapshots to determine when fills reach back of queue:
```
Capital: $50/side
PnL: $38.39 over 19 windows
ROI: 2.0%
Daily projection: ~$194
Win rate: 58% of windows profitable
```

### Variance
| Window | Edge | PnL |
|--------|------|-----|
| Best (09:00) | +22.3% | +$20.28 |
| Worst (02:00) | -9.8% | -$8.83 |

High variance - some windows lose money even with positive average edge.

---

## Sensitivity Analysis (BTC only)

| Capital/side | Fill Rate | Daily PnL | ROI |
|--------------|-----------|-----------|-----|
| $50 | 5% | $233 | 2.8% |
| $50 | 10% | $233 | 2.6% |
| $100 | 5% | $600 | 3.8% |
| $100 | 10% | $490 | 2.8% |
| $200 | 10% | $1,199 | 3.7% |
| $500 | 15% | $1,798 | 2.6% |

ROI stays relatively flat (~2-3%). More capital = more absolute PnL if you can get filled.

---

## Per-Coin Strategy Recommendations

### BTC
- **Best for**: Consistent fills, lowest variance
- **Edge**: 1-2% book, 2-3% realized
- **Fill rate**: ~100% both sides every window
- **Recommendation**: Primary coin for market making

### ETH
- **Best for**: Good balance of edge and liquidity
- **Edge**: 1.5-2% book, 2-3% realized
- **Fill rate**: ~100% both sides
- **Recommendation**: Secondary coin, run alongside BTC

### SOL
- **Edge**: 3% book but often unrealized
- **Fill rate**: Both sides filled only 53% of windows
- **Issue**: Unbalanced fills = coin flip exposure
- **Recommendation**: Skip or reduce capital

### XRP
- **Edge**: 4% book (highest)
- **Fill rate**: Both sides filled only 12% of windows
- **Issue**: Very low liquidity, rarely get matched fills
- **Recommendation**: Skip entirely

---

## Key Risks

### 1. Unmatched Exposure
If you fill UP but not DOWN (or vice versa), you're exposed to 50/50 outcome.
- Matched fills = guaranteed edge profit
- Unmatched fills = gambling

### 2. Adverse Selection
Large informed traders may sweep the book when they know direction.
- Your fills may cluster on the losing side
- Mitigation: stop accumulating after minute 9

### 3. Queue Competition
Other market makers (gabagool, etc.) compete for same fills.
- Real fill rate depends on queue position
- Back of queue = lower fill rate but better prices

### 4. Variance
Even with +2% edge, individual windows can lose 5-10%.
- Need sufficient bankroll to weather drawdowns
- 19 windows is small sample size

---

## Conclusions

1. **Strategy is viable** with ~2% ROI per window
2. **BTC + ETH** are the only coins worth trading (liquidity)
3. **$50-100/side** is reasonable starting capital
4. **Expected daily PnL**: $150-300 for BTC alone
5. **High variance**: expect losing windows, need 50+ windows for confidence

### Next Steps
- Collect more data (100+ windows)
- Track actual gabagool fills to calibrate queue position
- Consider front-of-queue strategy (tighter spreads, faster fills, lower edge)
- Monitor for regime changes in edge/liquidity
