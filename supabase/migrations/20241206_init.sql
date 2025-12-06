-- polymarket 15m markets schema

-- markets (one per 15-min window per coin)
CREATE TABLE IF NOT EXISTS markets (
    id              BIGSERIAL PRIMARY KEY,
    coin            TEXT NOT NULL,
    window_ts       BIGINT NOT NULL,
    slug            TEXT NOT NULL,
    up_token        TEXT NOT NULL,
    down_token      TEXT NOT NULL,
    spot_start      DOUBLE PRECISION,
    spot_end        DOUBLE PRECISION,
    outcome         TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(coin, window_ts)
);

-- snapshots (order book + price at a point in time)
CREATE TABLE IF NOT EXISTS snapshots (
    id              BIGSERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL,
    market_id       BIGINT REFERENCES markets(id) ON DELETE CASCADE,
    spot_price      DOUBLE PRECISION,
    up_bid          DOUBLE PRECISION,
    up_ask          DOUBLE PRECISION,
    down_bid        DOUBLE PRECISION,
    down_ask        DOUBLE PRECISION,
    up_depth        JSONB,
    down_depth      JSONB
);

-- indexes
CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON snapshots(ts);
CREATE INDEX IF NOT EXISTS idx_snapshots_market ON snapshots(market_id);
CREATE INDEX IF NOT EXISTS idx_markets_window ON markets(window_ts);
CREATE INDEX IF NOT EXISTS idx_markets_coin ON markets(coin);

-- useful views
CREATE OR REPLACE VIEW v_market_summary AS
SELECT
    m.coin,
    m.window_ts,
    m.outcome,
    m.spot_start,
    m.spot_end,
    CASE WHEN m.spot_start > 0
         THEN ((m.spot_end - m.spot_start) / m.spot_start * 100)
         ELSE NULL END as spot_change_pct,
    COUNT(s.id) as snapshot_count,
    MIN(s.ts) as first_snapshot,
    MAX(s.ts) as last_snapshot
FROM markets m
LEFT JOIN snapshots s ON s.market_id = m.id
GROUP BY m.id;
