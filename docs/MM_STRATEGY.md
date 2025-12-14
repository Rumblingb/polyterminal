# Market Making Strategy Research

## Key Frameworks

### 1. Avellaneda-Stoikov Model
The foundational academic framework for optimal market making.

**Core formulas:**

```
Reservation Price:  r = s - q·γ·σ²·(T-t)
Optimal Spread:     δ = (1/γ)·log(1 + γ/k) + (1/2)·γ·σ²·(T-t)
Bid/Ask:            bid = r - δ,  ask = r + δ
```

**Parameters:**
| Symbol | Meaning |
|--------|---------|
| s | mid-price |
| q | inventory (+ long, - short) |
| γ | risk aversion (higher = wider spreads) |
| σ | volatility |
| κ | order book liquidity |
| T-t | time remaining |

**Key insight:** The model turns inventory management into a feedback control system. As inventory grows, reservation price shifts to encourage mean reversion.

Sources:
- [Hummingbot Technical Deep Dive](https://hummingbot.org/blog/technical-deep-dive-into-the-avellaneda--stoikov-strategy/)
- [QuantBeckman Implementation](https://www.quantbeckman.com/p/can-you-manage-inventoryor-is-it)

### 2. Production Frameworks

| Framework | Type | Use Case |
|-----------|------|----------|
| [Hummingbot](https://hummingbot.org/) | Full MM platform | Built-in strategies, exchange connectors |
| [CCXT](https://github.com/ccxt/ccxt) | Exchange library | API abstraction, build custom logic |
| [Jesse](https://jesse.trade/) | Trading framework | Backtesting, optimization |

Hummingbot implements Avellaneda-Stoikov with:
- Dynamic spread based on volatility
- Inventory skew (wider spread on overweight side)
- Order refresh intervals

---

## Binary Options MM: Our Specific Case

### How It's Different
Traditional MM: post bid/ask around mid-price on ONE asset
Binary options MM: post bids on TWO complementary assets (UP + DOWN = 1)

### Edge Calculation
```
combined_bid = bid_up + bid_down
edge = 1.0 - combined_bid

Example:
  bid_up = 0.48, bid_down = 0.48
  combined = 0.96
  edge = 4% (guaranteed if BOTH fill)
```

### The Constraint
**combined_bid MUST be < 1.0** for positive edge.

Lower bids = higher edge but lower fill probability:
| Bid Level | Combined | Edge | Fill Probability |
|-----------|----------|------|------------------|
| 0.50 | 1.00 | 0% | High |
| 0.48 | 0.96 | 4% | Medium |
| 0.45 | 0.90 | 10% | Low |
| 0.40 | 0.80 | 20% | Very Low |

---

## Multi-Order Grid Strategy

### Why Multiple Small Orders?
1. **Queue competition** - smaller orders = less depth to compete with
2. **Price diversification** - catch fills at different levels
3. **Risk distribution** - lose less if market moves
4. **Inventory management** - fill gradually, not all at once

### Grid Design
```python
PRICE_LEVELS = [0.45, 0.46, 0.47, 0.48]
ORDER_SIZE = 10  # shares per order (~$4.50)
ORDERS_PER_LEVEL = 5

# total capital per side = 4 levels × 5 orders × $4.50 = $90
# both sides = $180
```

### Price Level Selection

**Approach 1: Fixed Grid**
Evenly spaced levels from min to max acceptable price.
```python
min_price = 0.40  # 20% edge if both fill
max_price = 0.48  # 4% edge if both fill
levels = np.linspace(min_price, max_price, num_levels)
```

**Approach 2: Data-Driven**
Analyze historical fill rates at each price level, weight towards levels with good edge AND fill rate.

**Approach 3: Volatility-Adjusted (Avellaneda-style)**
Wider grid during high volatility, tighter during low.
```python
spread = base_spread + γ * σ² * time_remaining
```

---

## Empirical Data (100 windows analyzed)

### Price Level Hit Rates
| Price | Edge | UP Hits | DOWN Hits | Both Sides |
|-------|------|---------|-----------|------------|
| 0.40 | 20% | 69% | 68% | ~50% |
| 0.42 | 16% | 70% | 69% | ~52% |
| 0.44 | 12% | 74% | 72% | ~55% |
| 0.46 | 8% | 76% | 73% | ~58% |
| 0.48 | 4% | 80% | 80% | **67%** |
| 0.50 | 0% | 83% | 83% | ~70% |

**Key finding:** 67% of windows have BOTH sides with sells at <= 0.48

### Volume Distribution
- Total UP SELL volume: 662,878 (51% at <= 0.48)
- Total DOWN SELL volume: 660,846 (52% at <= 0.48)

### Best Hours (UTC)
| Time Range | Both Fill Rate |
|------------|----------------|
| 03:00-06:00 | 75-100% |
| 13:00-14:00 | 83-100% |
| 18:00-21:00 | 67-100% |
| 23:00-00:00 | 100% |

**Worst hours:** 08:00 (0%), 16:00-17:00 (20-25%)

### Queue Depths
Average queue at each price level: ~5,800-6,000 shares
This is significant competition - need to post early for queue priority.

---

## Backtest Results (50 windows)

### Grid Strategy: 4 levels × 50 shares
| Metric | Value |
|--------|-------|
| Price levels | 0.42, 0.44, 0.46, 0.48 |
| Order size per level | 50 shares |
| Total capital | ~$180/window |
| Windows with both fills | 66% |
| Total PnL | $482.84 |
| Per window | $9.66 |
| **Per day projected** | **$927** |

### Fill Analysis by Price Level
| Price | Edge | UP Fills | DN Fills | Avg Queue |
|-------|------|----------|----------|-----------|
| 0.42 | 16% | 1,867 | 1,610 | 6,012 |
| 0.44 | 12% | 1,928 | 1,640 | 5,958 |
| 0.46 | 8% | 1,930 | 1,736 | 5,863 |
| 0.48 | 4% | 1,976 | 1,829 | 5,752 |

**Observation:** Lower prices have fewer fills but higher edge. The 0.42-0.48 grid captures good balance.

---

## Recommended Strategy

### Configuration
```python
PRICE_LEVELS = [0.42, 0.44, 0.46, 0.48]
ORDER_SIZE = 10  # shares per order (~$4.50)
ORDERS_PER_LEVEL = 5
TOTAL_CAPITAL = 200

# derived
capital_per_side = sum(p * ORDER_SIZE * ORDERS_PER_LEVEL for p in PRICE_LEVELS) = ~$90
```

### Optimal Hours (UTC)
Focus trading during high-activity windows:
- **Best:** 03:00-06:00, 13:00-14:00, 18:00-21:00, 23:00-00:00
- **Avoid:** 08:00, 16:00-17:00

### Queue Priority
Post orders **24h ahead** when markets open to be first at each price level.
Queue depths average 6,000 shares - late orders have slim chance of filling.

---

## Risk Considerations

1. **Adverse selection** - fills happen when price moves against you
2. **Inventory risk** - one side fills but not the other (50/50 gamble)
3. **Queue position** - late orders never get filled (~6000 queue depth)
4. **Execution risk** - orders may not post/cancel properly
5. **Capital efficiency** - money tied up in unfilled orders

### Mitigation
- Small order sizes (<$5) limit single-fill losses
- Multiple price levels diversify risk
- Post early for queue priority
- Monitor inventory imbalance

---

## Expected Performance

| Scenario | Both Fill Rate | Daily PnL |
|----------|----------------|-----------|
| Conservative | 50% | ~$500 |
| Base case | 67% | ~$900 |
| Optimistic | 80% | ~$1,200 |

**Capital requirement:** $200
**Expected ROI:** ~400-600%/day (theoretical, assumes perfect queue position)
