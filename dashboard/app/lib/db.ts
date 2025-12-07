import Database from 'better-sqlite3'
import path from 'path'

const DB_PATH = path.join(process.cwd(), 'data', 'polymarket.db')

// ensure data dir exists
import fs from 'fs'
const dataDir = path.dirname(DB_PATH)
if (!fs.existsSync(dataDir)) {
  fs.mkdirSync(dataDir, { recursive: true })
}

const db = new Database(DB_PATH)

// init tables
db.exec(`
  CREATE TABLE IF NOT EXISTS windows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    coin TEXT NOT NULL,
    window_ts INTEGER NOT NULL,
    slug TEXT,
    start_time TEXT,
    end_time TEXT,
    open_price REAL,
    close_price REAL,
    up_price REAL,
    down_price REAL,
    combined_ask REAL,
    resolved INTEGER DEFAULT 0,
    outcome TEXT,
    captured_at INTEGER,
    created_at INTEGER DEFAULT (strftime('%s', 'now')),
    UNIQUE(coin, window_ts)
  );

  CREATE TABLE IF NOT EXISTS edge_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    coin TEXT NOT NULL,
    window_ts INTEGER NOT NULL,
    timestamp INTEGER NOT NULL,
    combined_ask REAL,
    combined_bid REAL,
    up_ask REAL,
    down_ask REAL,
    chainlink_price REAL,
    created_at INTEGER DEFAULT (strftime('%s', 'now'))
  );

  CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    coin TEXT NOT NULL,
    window_ts INTEGER NOT NULL,
    timestamp INTEGER NOT NULL,
    combined_ask REAL,
    edge REAL,
    up_ask REAL,
    down_ask REAL,
    created_at INTEGER DEFAULT (strftime('%s', 'now'))
  );

  CREATE INDEX IF NOT EXISTS idx_windows_coin ON windows(coin);
  CREATE INDEX IF NOT EXISTS idx_windows_ts ON windows(window_ts);
  CREATE INDEX IF NOT EXISTS idx_edge_coin_ts ON edge_snapshots(coin, window_ts);
  CREATE INDEX IF NOT EXISTS idx_alerts_coin ON alerts(coin);
`)

// window operations
export interface WindowRecord {
  id?: number
  coin: string
  windowTs: number
  slug?: string
  startTime?: string
  endTime?: string
  openPrice: number | null
  closePrice: number | null
  upPrice: number
  downPrice: number
  combinedAsk: number
  resolved: boolean
  outcome: 'up' | 'down' | null
  capturedAt: number
}

export function saveWindow(record: WindowRecord) {
  const stmt = db.prepare(`
    INSERT INTO windows (coin, window_ts, slug, start_time, end_time, open_price, close_price, up_price, down_price, combined_ask, resolved, outcome, captured_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(coin, window_ts) DO UPDATE SET
      open_price = COALESCE(excluded.open_price, open_price),
      close_price = COALESCE(excluded.close_price, close_price),
      up_price = excluded.up_price,
      down_price = excluded.down_price,
      combined_ask = excluded.combined_ask,
      resolved = excluded.resolved,
      outcome = COALESCE(excluded.outcome, outcome),
      captured_at = excluded.captured_at
  `)
  return stmt.run(
    record.coin,
    record.windowTs,
    record.slug || null,
    record.startTime || null,
    record.endTime || null,
    record.openPrice,
    record.closePrice,
    record.upPrice,
    record.downPrice,
    record.combinedAsk,
    record.resolved ? 1 : 0,
    record.outcome,
    record.capturedAt
  )
}

export function getWindow(coin: string, windowTs: number): WindowRecord | null {
  const row = db.prepare(`
    SELECT * FROM windows WHERE coin = ? AND window_ts = ?
  `).get(coin, windowTs) as any

  if (!row) return null
  return mapWindowRow(row)
}

export function getRecentWindows(coin: string, limit = 10): WindowRecord[] {
  const rows = db.prepare(`
    SELECT * FROM windows WHERE coin = ? ORDER BY window_ts DESC LIMIT ?
  `).all(coin, limit) as any[]

  return rows.map(mapWindowRow)
}

export function getAllWindows(limit = 100): WindowRecord[] {
  const rows = db.prepare(`
    SELECT * FROM windows ORDER BY window_ts DESC LIMIT ?
  `).all(limit) as any[]

  return rows.map(mapWindowRow)
}

function mapWindowRow(row: any): WindowRecord {
  return {
    id: row.id,
    coin: row.coin,
    windowTs: row.window_ts,
    slug: row.slug,
    startTime: row.start_time,
    endTime: row.end_time,
    openPrice: row.open_price,
    closePrice: row.close_price,
    upPrice: row.up_price,
    downPrice: row.down_price,
    combinedAsk: row.combined_ask,
    resolved: row.resolved === 1,
    outcome: row.outcome as 'up' | 'down' | null,
    capturedAt: row.captured_at,
  }
}

// edge snapshots
export interface EdgeSnapshot {
  id?: number
  coin: string
  windowTs: number
  timestamp: number
  combinedAsk: number
  combinedBid: number
  upAsk: number
  downAsk: number
  chainlinkPrice: number
}

export function saveEdgeSnapshot(snapshot: EdgeSnapshot) {
  const stmt = db.prepare(`
    INSERT INTO edge_snapshots (coin, window_ts, timestamp, combined_ask, combined_bid, up_ask, down_ask, chainlink_price)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
  `)
  return stmt.run(
    snapshot.coin,
    snapshot.windowTs,
    snapshot.timestamp,
    snapshot.combinedAsk,
    snapshot.combinedBid,
    snapshot.upAsk,
    snapshot.downAsk,
    snapshot.chainlinkPrice
  )
}

export function getEdgeSnapshots(coin: string, windowTs: number): EdgeSnapshot[] {
  const rows = db.prepare(`
    SELECT * FROM edge_snapshots WHERE coin = ? AND window_ts = ? ORDER BY timestamp ASC
  `).all(coin, windowTs) as any[]

  return rows.map(row => ({
    id: row.id,
    coin: row.coin,
    windowTs: row.window_ts,
    timestamp: row.timestamp,
    combinedAsk: row.combined_ask,
    combinedBid: row.combined_bid,
    upAsk: row.up_ask,
    downAsk: row.down_ask,
    chainlinkPrice: row.chainlink_price,
  }))
}

export function getRecentEdgeSnapshots(coin: string, limit = 900): EdgeSnapshot[] {
  const rows = db.prepare(`
    SELECT * FROM edge_snapshots WHERE coin = ? ORDER BY timestamp DESC LIMIT ?
  `).all(coin, limit) as any[]

  return rows.reverse().map(row => ({
    id: row.id,
    coin: row.coin,
    windowTs: row.window_ts,
    timestamp: row.timestamp,
    combinedAsk: row.combined_ask,
    combinedBid: row.combined_bid,
    upAsk: row.up_ask,
    downAsk: row.down_ask,
    chainlinkPrice: row.chainlink_price,
  }))
}

// alerts
export interface ArbAlert {
  id?: number
  coin: string
  windowTs: number
  timestamp: number
  combinedAsk: number
  edge: number
  upAsk: number
  downAsk: number
}

export function saveAlert(alert: ArbAlert) {
  const stmt = db.prepare(`
    INSERT INTO alerts (coin, window_ts, timestamp, combined_ask, edge, up_ask, down_ask)
    VALUES (?, ?, ?, ?, ?, ?, ?)
  `)
  return stmt.run(
    alert.coin,
    alert.windowTs,
    alert.timestamp,
    alert.combinedAsk,
    alert.edge,
    alert.upAsk,
    alert.downAsk
  )
}

export function getRecentAlerts(limit = 50): ArbAlert[] {
  const rows = db.prepare(`
    SELECT * FROM alerts ORDER BY timestamp DESC LIMIT ?
  `).all(limit) as any[]

  return rows.map(row => ({
    id: row.id,
    coin: row.coin,
    windowTs: row.window_ts,
    timestamp: row.timestamp,
    combinedAsk: row.combined_ask,
    edge: row.edge,
    upAsk: row.up_ask,
    downAsk: row.down_ask,
  }))
}

// stats
export function getWindowStats(coin?: string) {
  const whereClause = coin ? 'WHERE coin = ?' : ''
  const params = coin ? [coin] : []

  const total = db.prepare(`SELECT COUNT(*) as count FROM windows ${whereClause}`).get(...params) as { count: number }
  const resolved = db.prepare(`SELECT COUNT(*) as count FROM windows ${whereClause} ${coin ? 'AND' : 'WHERE'} resolved = 1`).get(...params) as { count: number }
  const upWins = db.prepare(`SELECT COUNT(*) as count FROM windows ${whereClause} ${coin ? 'AND' : 'WHERE'} outcome = 'up'`).get(...params) as { count: number }
  const downWins = db.prepare(`SELECT COUNT(*) as count FROM windows ${whereClause} ${coin ? 'AND' : 'WHERE'} outcome = 'down'`).get(...params) as { count: number }
  const edgeOpps = db.prepare(`SELECT COUNT(*) as count, AVG(1 - combined_ask) as avg_edge FROM windows ${whereClause} ${coin ? 'AND' : 'WHERE'} combined_ask < 1`).get(...params) as { count: number; avg_edge: number }

  return {
    total: total.count,
    resolved: resolved.count,
    upWins: upWins.count,
    downWins: downWins.count,
    upWinRate: resolved.count > 0 ? upWins.count / resolved.count : 0,
    edgeOpportunities: edgeOpps.count,
    avgEdge: edgeOpps.avg_edge || 0,
  }
}

export default db
