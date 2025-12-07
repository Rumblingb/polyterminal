// standalone data collector - runs independently of dashboard
// usage: npx ts-node scripts/collector.ts

import WebSocket from 'ws'
import Database from 'better-sqlite3'
import path from 'path'
import fs from 'fs'

const CLOB_WS = 'wss://ws-subscriptions-clob.polymarket.com/ws/market'
const GAMMA_API = 'https://gamma-api.polymarket.com'

const DB_PATH = path.join(process.cwd(), 'data', 'polymarket.db')

// ensure data dir
const dataDir = path.dirname(DB_PATH)
if (!fs.existsSync(dataDir)) {
  fs.mkdirSync(dataDir, { recursive: true })
}

const db = new Database(DB_PATH)

// init tables
db.exec(`
  CREATE TABLE IF NOT EXISTS price_ticks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    coin TEXT NOT NULL,
    window_ts INTEGER NOT NULL,
    timestamp INTEGER NOT NULL,
    up_bid REAL,
    up_ask REAL,
    down_bid REAL,
    down_ask REAL,
    combined_bid REAL,
    combined_ask REAL
  );

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
    up_bid REAL,
    down_bid REAL,
    chainlink_price REAL,
    created_at INTEGER DEFAULT (strftime('%s', 'now'))
  );

  CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    coin TEXT NOT NULL,
    window_ts INTEGER NOT NULL,
    timestamp INTEGER NOT NULL,
    combined_bid REAL,
    edge REAL,
    up_bid REAL,
    down_bid REAL,
    created_at INTEGER DEFAULT (strftime('%s', 'now'))
  );

  CREATE INDEX IF NOT EXISTS idx_ticks_coin_ts ON price_ticks(coin, window_ts);
  CREATE INDEX IF NOT EXISTS idx_ticks_timestamp ON price_ticks(timestamp);
`)

const insertTick = db.prepare(`
  INSERT INTO price_ticks (coin, window_ts, timestamp, up_bid, up_ask, down_bid, down_ask, combined_bid, combined_ask)
  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
`)

const insertSnapshot = db.prepare(`
  INSERT INTO edge_snapshots (coin, window_ts, timestamp, combined_ask, combined_bid, up_ask, down_ask, up_bid, down_bid, chainlink_price)
  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
`)

const insertAlert = db.prepare(`
  INSERT INTO alerts (coin, window_ts, timestamp, combined_bid, edge, up_bid, down_bid)
  VALUES (?, ?, ?, ?, ?, ?, ?)
`)

interface Market {
  coin: string
  windowTs: number
  upToken: string
  downToken: string
  upBid: number
  upAsk: number
  downBid: number
  downAsk: number
}

const markets = new Map<string, Market>()
const tokenMap = new Map<string, { coin: string; side: 'up' | 'down' }>()
let lastSave: Record<string, number> = {}
let lastAlert: Record<string, number> = {}

const ALERT_THRESHOLD = 0.98

async function fetchMarkets() {
  const coins = ['btc', 'eth', 'sol', 'xrp']
  const now = Math.floor(Date.now() / 1000)
  const windowStart = now - (now % 900)

  for (const coin of coins) {
    const slug = `${coin}-updown-15m-${windowStart}`
    try {
      const res = await fetch(`${GAMMA_API}/markets/slug/${slug}`)
      if (!res.ok) continue
      const data = await res.json()

      if (data.tokens?.length === 2) {
        const upToken = data.tokens.find((t: any) => t.outcome === 'Up')
        const downToken = data.tokens.find((t: any) => t.outcome === 'Down')

        if (upToken && downToken) {
          markets.set(coin, {
            coin,
            windowTs: windowStart,
            upToken: upToken.token_id,
            downToken: downToken.token_id,
            upBid: 0,
            upAsk: parseFloat(upToken.price) || 0.5,
            downBid: 0,
            downAsk: parseFloat(downToken.price) || 0.5,
          })

          tokenMap.set(upToken.token_id, { coin, side: 'up' })
          tokenMap.set(downToken.token_id, { coin, side: 'down' })

          console.log(`[${coin}] loaded ${slug}`)
        }
      }
    } catch (err) {
      console.error(`[${coin}] fetch error:`, err)
    }
  }
}

function connectWebSocket() {
  const allTokens = Array.from(tokenMap.keys())
  if (allTokens.length === 0) {
    console.log('no tokens, retrying in 5s...')
    setTimeout(connectWebSocket, 5000)
    return
  }

  console.log(`connecting to CLOB with ${allTokens.length} tokens...`)
  const ws = new WebSocket(CLOB_WS)

  ws.on('open', () => {
    console.log('CLOB connected')
    ws.send(JSON.stringify({
      type: 'subscribe',
      channel: 'market',
      assets_ids: allTokens
    }))
  })

  ws.on('message', (data: Buffer) => {
    try {
      let msg = JSON.parse(data.toString())
      if (Array.isArray(msg)) msg = msg[0]
      if (!msg) return

      if (msg.event_type === 'price_change') {
        for (const pc of msg.price_changes || []) {
          const info = tokenMap.get(pc.asset_id)
          if (!info) continue

          const market = markets.get(info.coin)
          if (!market) continue

          if (info.side === 'up') {
            market.upBid = parseFloat(pc.best_bid || '0')
            market.upAsk = parseFloat(pc.best_ask || '1')
          } else {
            market.downBid = parseFloat(pc.best_bid || '0')
            market.downAsk = parseFloat(pc.best_ask || '1')
          }

          const now = Date.now()
          const combinedBid = market.upBid + market.downBid
          const combinedAsk = market.upAsk + market.downAsk

          // save tick every second
          if (!lastSave[info.coin] || now - lastSave[info.coin] > 1000) {
            lastSave[info.coin] = now

            insertTick.run(
              info.coin,
              market.windowTs,
              now,
              market.upBid,
              market.upAsk,
              market.downBid,
              market.downAsk,
              combinedBid,
              combinedAsk
            )

            // save snapshot every 5 seconds
            if (now % 5000 < 1000) {
              insertSnapshot.run(
                info.coin,
                market.windowTs,
                now,
                combinedAsk,
                combinedBid,
                market.upAsk,
                market.downAsk,
                market.upBid,
                market.downBid,
                0
              )
            }

            // check for maker edge alert
            if (combinedBid > 0 && combinedBid < ALERT_THRESHOLD) {
              if (!lastAlert[info.coin] || now - lastAlert[info.coin] > 10000) {
                lastAlert[info.coin] = now
                const edge = 1 - combinedBid
                insertAlert.run(info.coin, market.windowTs, now, combinedBid, edge, market.upBid, market.downBid)
                console.log(`🚨 [${info.coin}] MAKER EDGE: ${combinedBid.toFixed(3)} (+${(edge * 100).toFixed(1)}%)`)
              }
            }

            // log every 10 seconds
            if (now % 10000 < 1000) {
              const edge = combinedBid > 0 && combinedBid < 1 ? `+${((1 - combinedBid) * 100).toFixed(1)}%` : '--'
              console.log(`[${info.coin}] bid=${combinedBid.toFixed(3)} ask=${combinedAsk.toFixed(3)} edge=${edge}`)
            }
          }
        }
      }
    } catch {}
  })

  ws.on('error', (err) => {
    console.error('ws error:', err.message)
  })

  ws.on('close', () => {
    console.log('ws closed, reconnecting in 2s...')
    setTimeout(connectWebSocket, 2000)
  })
}

async function main() {
  console.log('=== Polymarket Data Collector ===')
  console.log(`DB: ${DB_PATH}`)
  console.log(`Alert threshold: ${ALERT_THRESHOLD}`)
  console.log('')

  await fetchMarkets()

  // refresh markets every 15 minutes
  setInterval(fetchMarkets, 15 * 60 * 1000)

  connectWebSocket()
}

main().catch(console.error)
