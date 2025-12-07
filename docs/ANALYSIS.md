# Polymarket 15-Minute Binary Options Strategy

## Overview

Trading strategy for Polymarket's 15-minute BTC binary options.
Markets resolve based on Chainlink Data Streams BTC/USD price.

---

## Market Structure

- **Window Duration**: 15 minutes (start at :00, :15, :30, :45)
- **UP Token**: Wins $1 if price_end >= price_start
- **DOWN Token**: Wins $1 if price_end < price_start
- **Resolution Source**: Chainlink Data Streams

---

## The Strategy

### Entry Rules

```
At minute 10 of each 15-min window:

1. Get window start price (BTC open at :00/:15/:30/:45)
2. Get current BTC price from Binance
3. Calculate: spot_chg = (current - start) / start * 100

4. Predict MM mid:
   predicted_up = 0.50 + 1.5*spot_chg + 1.5*spot_chg*(10/15)
   predicted_up = clamp(predicted_up, 0.02, 0.98)

5. Calculate confidence:
   confidence = abs(predicted_up - 0.5)

6. IF confidence > 0.05:
   - If predicted_up > 0.5: BUY UP at ask
   - If predicted_up < 0.5: BUY DOWN at ask
   ELSE:
   - Skip this window
```

### Why Minute 10?

- Early enough to capture larger moves (better entry prices)
- Late enough that BTC momentum is established
- 5 minutes until resolution = high probability direction holds

### Why Confidence > 5%?

| Filter | Win Rate | Edge | Trades/Day |
|--------|----------|------|------------|
| All | 81.8% | +4.6% | 96 |
| **>5%** | **85.1%** | **+4.9%** | **84** |
| >10% | 87.0% | +3.8% | 73 |
| >15% | 88.5% | +2.5% | 63 |

Conf>5% has the highest edge (+4.9%) while maintaining good volume.

---

## Expected Performance

### Per $100 Bet (Fixed Size)

| Metric | Value |
|--------|-------|
| Win rate | 85.1% |
| Breakeven | 80.2% |
| Edge | +4.9% |
| Avg entry | $0.80 |
| ROI/trade | 7.5% |
| Daily P&L | $632 |
| 90-day P&L | $56,847 |

### Scaling (Start $1000)

| Week | Bet Size | Weekly Profit | Cumulative |
|------|----------|---------------|------------|
| 1 | $100 | ~$4,400 | $5,400 |
| 2 | $150 | ~$6,600 | $12,000 |
| 3 | $200 | ~$8,800 | $20,800 |
| 4+ | $500 (max) | ~$22,000/wk | liquidity-capped |

**Liquidity ceiling: ~$500-2000 per side per market**

---

## Position Sizing

### Fixed Size (Recommended for Start)

- Start with $50-100 per trade
- Scale up slowly as you verify live performance
- Never exceed available liquidity

### Kelly Criterion

| Metric | Value |
|--------|-------|
| Win probability | 0.85 |
| Avg odds | 0.25:1 |
| Full Kelly | 24.5% |
| Quarter Kelly | 6.1% |

**Use quarter-Kelly (6%) max** due to:
- Oracle mismatch risk (5.4%)
- Model uncertainty
- Execution variance

---

## Risk Factors

### 1. Oracle Mismatch (5.4%)

Polymarket uses Chainlink, we use Binance. In 5.4% of windows, they disagree on outcome.
- **Impact**: Random loss ~1 in 20 trades
- **Mitigation**: None without Chainlink Data Streams access ($$$)

### 2. Liquidity Constraints

- Typical depth: $500-2000 per side
- Large orders will move the market
- **Mitigation**: Cap bet size at visible liquidity

### 3. Spread Widening

| Time Remaining | Typical Spread |
|---------------|----------------|
| >10 min | 1-2 cents |
| 5-10 min | 2-3 cents |
| <5 min | 3-5+ cents |

- **Mitigation**: Enter at minute 10, not later

### 4. MM Adaptation

If your flow becomes predictable, MMs may widen spreads or adjust pricing.
- **Mitigation**: Vary timing slightly, don't always max size

---

## Implementation Requirements

### Must Have

- Binance WebSocket for real-time BTC price
- Track window start prices (open at :00/:15/:30/:45)
- Polymarket CLOB API for order execution
- API credentials (in .env)

### Nice to Have

- Telegram alerts for trades
- P&L tracking
- Automatic position management

### Not Required

- Chainlink Data Streams (too expensive)
- Sub-millisecond infrastructure
- Co-location

---

## Code Architecture

```
bot.py
├── BinanceWS           # real-time BTC price stream
├── WindowTracker       # tracks start prices for each window
├── Strategy            # calculates signals at minute 10
├── PolymarketClient    # executes orders via CLOB API
└── TelegramNotifier    # alerts on trades (optional)
```

### Key Functions

```python
def calculate_signal(start_price, current_price, elapsed_seconds):
    spot_chg = (current_price - start_price) / start_price * 100
    time_norm = elapsed_seconds / 900

    predicted_up = 0.50 + 1.5*spot_chg + 1.5*spot_chg*time_norm
    predicted_up = max(0.02, min(0.98, predicted_up))

    confidence = abs(predicted_up - 0.5)

    if confidence < 0.05:
        return None  # skip

    return 'UP' if predicted_up > 0.5 else 'DOWN'
```

---

## Backtest Summary

### Data

- 90 days of Binance 1-min OHLC
- 8,639 BTC windows
- Simulated MM pricing model

### Results (Conf>5% filter)

| Metric | Value |
|--------|-------|
| Total trades | 7,586 |
| Win rate | 85.1% |
| Edge over breakeven | +4.9% |
| Total P&L ($100/trade) | $56,847 |
| Max losing streak | 5 |

### Caveats

1. **Synthetic data**: Used model to simulate MM prices, not real order book
2. **No execution simulation**: Assumed fill at ask + 2c slippage
3. **Oracle mismatch randomized**: Used 5.4% random flip, real pattern unknown
4. **Past performance ≠ future results**

---

## Monitoring

### Daily Checks

- [ ] Win rate holding >80%?
- [ ] Average entry price stable (~80c)?
- [ ] Liquidity sufficient for bet size?
- [ ] Any unusual oracle mismatches?

### Red Flags

- Win rate drops below 75% for 3+ days
- Spreads consistently >5c at minute 10
- Fills significantly worse than expected
- Multiple consecutive losses on "high confidence" trades

---

## Changelog

- **2025-12-06**: Initial strategy based on 90-day synthetic backtest
- Conf>5% filter identified as optimal (edge +4.9%)
- BTC-only focus (most liquid, best data)
