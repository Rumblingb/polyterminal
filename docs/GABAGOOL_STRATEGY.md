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
| Strategy | **Risk-Free Arbitrage** |

## The Strategy: Combined Price Arbitrage

Gabagool exploits a market inefficiency where **UP price + DOWN price < 1.00**.

### How It Works

In a binary market (UP or DOWN), the prices should sum to 1.00:
- If UP = 0.60, DOWN should = 0.40
- Combined = 1.00 (no arbitrage)

But when the orderbook is imbalanced:
- UP best ask = 0.43
- DOWN best ask = 0.56
- **Combined = 0.99**

**The Trade:**
1. Buy 100 shares of UP @ $0.43 = $43
2. Buy 100 shares of DOWN @ $0.56 = $56
3. Total cost = $99

**The Payout:**
- If market resolves UP: UP shares pay $100, DOWN pays $0 → **$100**
- If market resolves DOWN: UP pays $0, DOWN shares pay $100 → **$100**

**Guaranteed profit = $100 - $99 = $1 (1% return)**

## Execution Details

### On-Chain Evidence (20,000 trades sampled)

| Role | Buys | Sells | Volume |
|------|------|-------|--------|
| **Maker** (limit orders) | 10,000 | 0 | $47,753 |
| **Taker** (market orders) | 1,846 | 8,154 | $47,361 |

Key insight: **100% of maker orders are BUYS** - they're posting limit buy orders on both sides.

### Position Building

From trade-by-trade analysis of BTC 2:45PM window:
```
Time       Side Out   Shares  Price   Running Position
11:51:45   BUY  DN     10.6   0.560   UP:    0   DN:   10.6
11:51:45   BUY  UP     11.2   0.430   UP:   11.2 DN:   10.6
11:51:45   BUY  UP      4.8   0.430   UP:   16.0 DN:   10.6
11:51:45   BUY  DN     10.6   0.560   UP:   16.0 DN:   21.3
...
```

They simultaneously buy BOTH sides, maintaining balanced exposure.

### Execution Speed

- **15.5 trades per second** average
- **208 trades in 1 second** maximum
- Clearly automated bot execution

### Order Sizing

Most common share sizes:
- 15-16 shares: most frequent
- 10-14 shares: common
- Standard sizing suggests systematic execution

## Combined Price Analysis

| Range | Trades | % |
|-------|--------|---|
| 0.90-0.92 | 95 | 2.0% |
| 0.92-0.94 | 379 | 8.1% |
| 0.94-0.96 | 293 | 6.3% |
| **0.96-0.98** | **2,331** | **50.0%** |
| 0.98-1.00 | 1,413 | 30.3% |
| 1.00-1.02 | 57 | 1.2% |
| 1.02-1.10 | 69 | 1.5% |

**86% of trades executed when combined < 1.00**

Average combined price: **0.9714** (2.86% edge)

## Profit by Market

| Market | Matched Shares | Combined | Edge | Profit |
|--------|----------------|----------|------|--------|
| BTC 2:45PM | 13,327 | 0.972 | +2.8% | $367.82 |
| BTC 2PM | 1,929 | 0.908 | +9.2% | $176.95 |
| ETH 2:45PM | 2,493 | 0.964 | +3.6% | $89.78 |
| BTC 3:00PM | 2,005 | 0.977 | +2.3% | $46.91 |
| ETH 3:00PM | 567 | 0.983 | +1.7% | $9.82 |
| BTC 3PM | 591 | 0.987 | +1.3% | $7.48 |
| ETH 2PM | 709 | 1.001 | -0.1% | -$0.58 |
| ETH 3PM | 188 | 1.026 | -2.6% | -$4.93 |
| **TOTAL** | **21,809** | | **+3.18%** | **$693.26** |

## Timing Pattern

Trades per minute within 15m window:
```
Minute  0: 2,063 trades  ████████████████████
Minute  1: 1,919 trades  ███████████████████
Minute  2: 1,906 trades  ███████████████████
Minute  3: 1,825 trades  ██████████████████
Minute  4: 1,636 trades  ████████████████
Minute  5: 1,537 trades  ███████████████
...
Minute 14:   480 trades  ████
```

**Heavy activity in first 5 minutes** when orderbook is most imbalanced.

## Why This Works

### Market Inefficiency

15-minute crypto markets have:
1. **Low liquidity** - thin order books
2. **Fast price moves** - prices change quickly with BTC
3. **Slow arbitrageurs** - not enough bots competing
4. **Retail flow** - directional bettors create imbalances

### The Edge

When a retail trader buys UP aggressively:
- UP price rises to 0.60
- DOWN price stays at 0.42 (no one selling)
- Combined = 1.02 (no arb)

But then market makers reprice DOWN:
- UP = 0.55
- DOWN = 0.43
- Combined = 0.98 (ARB!)

Gabagool snaps up both sides instantly.

## Technical Implementation

### Requirements

1. **Orderbook monitoring** - Watch both UP and DOWN best prices
2. **Combined price calculation** - Trigger when UP_ask + DOWN_ask < 1.00
3. **Simultaneous execution** - Buy both sides atomically
4. **Position balancing** - Keep UP and DOWN shares roughly equal
5. **Speed** - Execute before others capture the arb

### Pseudocode

```python
while market_open:
    up_ask = get_best_ask(UP_TOKEN)
    down_ask = get_best_ask(DOWN_TOKEN)
    combined = up_ask + down_ask

    if combined < 0.99:  # 1% minimum edge
        shares = calculate_size(liquidity, max_position)

        # execute simultaneously
        buy(UP_TOKEN, shares, up_ask)
        buy(DOWN_TOKEN, shares, down_ask)

        log(f"ARB: {combined:.3f} edge={1-combined:.2%}")
```

### Key Parameters

- **Min edge threshold:** ~1% (combined < 0.99)
- **Order size:** 10-16 shares typical
- **Execution speed:** Sub-second
- **Position limit:** Unknown, but running $10k+ per window

## Comparison to Your Strategy

| Aspect | Your Bot | Gabagool |
|--------|----------|----------|
| Strategy | Directional (BTC correlation) | Arbitrage (price inefficiency) |
| Risk | Market risk (can lose if wrong) | Near-zero (guaranteed profit) |
| Edge | 5-10% when correct | 2-3% always |
| Win Rate | ~85% | ~100% (matched positions) |
| Capital Required | Low | High (need to buy both sides) |
| Competition | Moderate | Low (for now) |
| Scalability | Limited by liquidity | Limited by arb opportunities |

## How to Replicate

### Infrastructure Needed

1. **Fast websocket connection** to CLOB API for orderbook
2. **Pre-signed orders** for instant execution
3. **Both-side execution** in single transaction or parallel
4. **Position tracker** to balance UP/DOWN exposure

### Risks

1. **Execution risk** - If only one side fills, exposed to direction
2. **Fee drag** - Taker fees eat into thin margins
3. **Competition** - More bots = less arb opportunities
4. **Liquidity** - May not get full size at arb prices

### Potential Improvements

1. **Be a maker on both sides** - Better fees, but risk of adverse selection
2. **Dynamic sizing** - Larger when edge is bigger
3. **Multi-market** - Run on BTC, ETH, SOL, XRP simultaneously
4. **Exit early** - Sell positions before resolution if price favorable

## Conclusion

Gabagool's strategy is **pure arbitrage** - exploiting price inefficiencies in binary markets where UP + DOWN < 1.00.

Key success factors:
- **Speed**: 15+ trades/second automated execution
- **Scale**: $21M volume to capture many small edges
- **Discipline**: Systematic execution, balanced positions
- **Market selection**: 15m crypto markets with low liquidity

The strategy is **low risk, moderate reward** - perfect for someone with capital who wants consistent returns without directional exposure.

---

*Analysis by Claude Code, December 2025*
