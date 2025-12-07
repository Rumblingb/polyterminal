// local storage for window tracking

export interface WindowRecord {
  windowTs: number
  coin: string
  slug: string

  // timing
  startTime: string
  endTime: string

  // prices
  openPrice: number | null      // PRICE TO BEAT
  closePrice: number | null     // final price (after resolution)

  // market state at capture
  upPrice: number
  downPrice: number
  combinedAsk: number

  // outcome
  resolved: boolean
  outcome: 'up' | 'down' | null
  capturedAt: number
}

export interface EdgeSnapshot {
  timestamp: number
  coin: string
  windowTs: number
  combinedAsk: number
  combinedBid: number
  upAsk: number
  downAsk: number
  chainlinkPrice: number
}

const WINDOWS_KEY = 'polymarket_windows'
const EDGE_SNAPSHOTS_KEY = 'polymarket_edge_snapshots'
const ALERTS_KEY = 'polymarket_alerts'

// generic storage helpers
function load<T>(key: string, defaultValue: T): T {
  if (typeof window === 'undefined') return defaultValue
  try {
    const stored = localStorage.getItem(key)
    return stored ? JSON.parse(stored) : defaultValue
  } catch {
    return defaultValue
  }
}

function save<T>(key: string, value: T) {
  if (typeof window === 'undefined') return
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {}
}

// windows
export function loadWindows(): Record<string, WindowRecord> {
  return load(WINDOWS_KEY, {})
}

export function saveWindow(record: WindowRecord) {
  const windows = loadWindows()
  const key = `${record.coin}-${record.windowTs}`
  windows[key] = record
  save(WINDOWS_KEY, windows)
}

export function getWindow(coin: string, windowTs: number): WindowRecord | null {
  const windows = loadWindows()
  return windows[`${coin}-${windowTs}`] || null
}

export function getRecentWindows(coin: string, limit = 10): WindowRecord[] {
  const windows = loadWindows()
  return Object.values(windows)
    .filter(w => w.coin === coin)
    .sort((a, b) => b.windowTs - a.windowTs)
    .slice(0, limit)
}

export function getAllWindows(): WindowRecord[] {
  const windows = loadWindows()
  return Object.values(windows).sort((a, b) => b.windowTs - a.windowTs)
}

// edge snapshots (more granular than windows)
export function loadEdgeSnapshots(): EdgeSnapshot[] {
  return load(EDGE_SNAPSHOTS_KEY, [])
}

export function saveEdgeSnapshot(snapshot: EdgeSnapshot) {
  const snapshots = loadEdgeSnapshots()
  snapshots.push(snapshot)
  // keep last 1000 snapshots
  if (snapshots.length > 1000) {
    snapshots.splice(0, snapshots.length - 1000)
  }
  save(EDGE_SNAPSHOTS_KEY, snapshots)
}

export function getEdgeSnapshotsForWindow(coin: string, windowTs: number): EdgeSnapshot[] {
  const snapshots = loadEdgeSnapshots()
  return snapshots.filter(s => s.coin === coin && s.windowTs === windowTs)
}

// alerts
export interface ArbAlert {
  id: string
  coin: string
  timestamp: number
  windowTs: number
  combinedAsk: number
  edge: number
  upAsk: number
  downAsk: number
}

export function loadAlerts(): ArbAlert[] {
  return load(ALERTS_KEY, [])
}

export function saveAlert(alert: ArbAlert) {
  const alerts = loadAlerts()
  alerts.unshift(alert)
  // keep last 100 alerts
  if (alerts.length > 100) {
    alerts.splice(100)
  }
  save(ALERTS_KEY, alerts)
}

export function clearOldAlerts(maxAge = 3600000) {
  const now = Date.now()
  const alerts = loadAlerts().filter(a => now - a.timestamp < maxAge)
  save(ALERTS_KEY, alerts)
}

// stats
export function getWindowStats(coin?: string) {
  const windows = Object.values(loadWindows())
  const filtered = coin ? windows.filter(w => w.coin === coin) : windows

  const resolved = filtered.filter(w => w.resolved)
  const upWins = resolved.filter(w => w.outcome === 'up').length
  const downWins = resolved.filter(w => w.outcome === 'down').length

  const edgeOpportunities = filtered.filter(w => w.combinedAsk < 1)
  const avgEdge = edgeOpportunities.length > 0
    ? edgeOpportunities.reduce((sum, w) => sum + (1 - w.combinedAsk), 0) / edgeOpportunities.length
    : 0

  return {
    total: filtered.length,
    resolved: resolved.length,
    upWins,
    downWins,
    upWinRate: resolved.length > 0 ? upWins / resolved.length : 0,
    edgeOpportunities: edgeOpportunities.length,
    avgEdge,
  }
}

// clear all storage
export function clearAllStorage() {
  if (typeof window === 'undefined') return
  localStorage.removeItem(WINDOWS_KEY)
  localStorage.removeItem(EDGE_SNAPSHOTS_KEY)
  localStorage.removeItem(ALERTS_KEY)
}
