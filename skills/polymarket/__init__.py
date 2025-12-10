# polymarket skills - query onchain and offchain data
#
# modules:
#   subgraph - on-chain trades (source of truth)
#   data_api - enriched trades with metadata
#   gamma    - market metadata, token mapping
#   clob     - orderbook, prices
#   wallet   - high-level wallet analysis

from .subgraph import (
    get_wallet_trades,
    get_wallet_trades_all,
    get_recent_trades,
    get_trades_by_token,
    decode_trade,
    count_wallet_trades
)

from .data_api import (
    get_trades,
    get_wallet_trades as get_enriched_trades,
    get_market_trades,
    get_token_trades,
    summarize_trades
)

from .gamma import (
    get_market_by_token,
    get_market_by_slug,
    get_market_by_condition,
    get_token_info,
    batch_token_info,
    find_markets,
    find_events
)

from .clob import (
    get_book,
    get_price,
    get_spread,
    get_depth,
    get_midpoint,
    get_combined_spread,
    estimate_fill,
    get_markets,
    get_market
)

from .wallet import (
    analyze_wallet,
    get_decoded_trades,
    get_positions,
    get_pnl,
    get_activity
)
