# Trading Skill

Order placement and market making for Polymarket CLOB.

## Setup

Requires env vars:
```
PRIVATE_KEY=0x...
POLY_ADDRESS=0x...       # proxy wallet with USDC
POLY_API_KEY=...
POLY_API_SECRET=...
POLY_PASSPHRASE=...
```

## orders.py

Place and manage orders.

```python
from skills.trading import place_order, place_orders, get_orders, cancel_order, cancel_all

# single order
result = place_order(token_id, price=0.45, size=100, side='BUY')

# batch orders
results = place_orders([
    {'token_id': '...', 'price': 0.44, 'size': 100, 'side': 'BUY'},
    {'token_id': '...', 'price': 0.46, 'size': 100, 'side': 'BUY'},
])

# get open orders
orders = get_orders()

# cancel
cancel_order(order_id)
cancel_all()
```

### CLI

```bash
python skills/trading/orders.py list
python skills/trading/orders.py cancel <order_id>
python skills/trading/orders.py cancel-all
```

## mm.py

Market making for BTC 15m updown markets.

```python
from skills.trading import post_grid, post_window

# post for single window
result = post_window(window_ts, price_levels=[0.44, 0.46, 0.48], size=108)

# post for next N windows
results = post_grid(windows_ahead=4)
```

### CLI

```bash
# list upcoming windows
python skills/trading/mm.py windows --hours 2

# post for specific window
python skills/trading/mm.py post 1765696500

# post grid for next 4 windows
python skills/trading/mm.py grid --count 4
```

## Strategy Params

Default grid:
- Levels: 0.44, 0.46, 0.48
- Size: 108 shares/level
- Edges: 12%, 8%, 4%
- Cost: ~$298/window (both sides)

Expected returns (from backtest):
- 0% queue: ~$1400/day
- 50% queue: ~$775/day
- Realistic: ~$700-1000/day
