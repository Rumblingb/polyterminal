'use client'

import { useEffect, useState, useRef, useCallback } from 'react'
import dynamic from 'next/dynamic'

const PriceChart = dynamic(() => import('./components/PriceChart'), { ssr: false })
import WindowTracker from './components/WindowTracker'

const CLOB_WS = 'wss://ws-subscriptions-clob.polymarket.com/ws/market'
const PRICES_WS = 'wss://ws-live-data.polymarket.com'

interface OrderLevel {
  price: number
  size: number
}

interface OrderBook {
  bids: OrderLevel[]
  asks: OrderLevel[]
}

interface Market {
  coin: string
  windowTs: number
  windowStart: string
  windowEnd: string
  upToken: string
  downToken: string
  upBid: number
  upAsk: number
  downBid: number
  downAsk: number
  upBook: OrderBook
  downBook: OrderBook
  volume: number
  liquidity: number
  title: string
  conditionId: string
  slug: string
  spread: number | null
  competitive: number
}

interface Prices {
  [symbol: string]: number
}

interface EdgePoint {
  timestamp: number
  combinedAsk: number
  combinedBid: number
  upAsk: number
  downAsk: number
}

interface ArbAlert {
  id: string
  coin: string
  timestamp: number
  combinedBid: number
  edge: number
  upBid: number
  downBid: number
}


export default function Dashboard() {
  const [markets, setMarkets] = useState<Market[]>([])
  const [prices, setPrices] = useState<Prices>({})
  const [status, setStatus] = useState<'connecting' | 'live' | 'error'>('connecting')
  const [progress, setProgress] = useState(0)
  const [selectedCoin, setSelectedCoin] = useState<string | null>(null)
  const [alertThreshold, setAlertThreshold] = useState(0.995)

  // edge tracking
  const [edgeHistory, setEdgeHistory] = useState<Record<string, EdgePoint[]>>({})
  const [alerts, setAlerts] = useState<ArbAlert[]>([])
  const [targetPrices, setTargetPrices] = useState<Record<string, number | null>>({})
  const lastEdgeUpdateRef = useRef<Record<string, number>>({})

  const clobWsRef = useRef<WebSocket | null>(null)
  const pricesWsRef = useRef<WebSocket | null>(null)
  const tokenMapRef = useRef<Map<string, { coin: string; side: 'up' | 'down' }>>(new Map())

  // load alerts from sqlite on mount
  useEffect(() => {
    fetch('/api/db/alerts?limit=50')
      .then(res => res.json())
      .then(data => {
        if (Array.isArray(data)) {
          setAlerts(data.map((a: any, i: number) => ({
            id: a.id ? `db-${a.id}` : `${a.coin}-${a.timestamp}-${i}`,
            coin: a.coin,
            timestamp: a.timestamp,
            combinedBid: a.combinedBid || a.combinedAsk,
            edge: a.edge,
            upBid: a.upBid || a.upAsk,
            downBid: a.downBid || a.downAsk
          })))
        }
      })
      .catch(() => {})
  }, [])

  const fetchMarkets = useCallback(async () => {
    try {
      const res = await fetch('/api/markets')
      const data = await res.json()

      const newMarkets: Market[] = []
      const newTokenMap = new Map<string, { coin: string; side: 'up' | 'down' }>()

      for (const m of data) {
        newMarkets.push({
          coin: m.coin,
          windowTs: m.windowTs,
          windowStart: m.windowStart,
          windowEnd: m.windowEnd,
          upToken: m.upToken,
          downToken: m.downToken,
          upBid: 0,
          upAsk: m.upPrice || 1,
          downBid: 0,
          downAsk: m.downPrice || 1,
          upBook: { bids: [], asks: [] },
          downBook: { bids: [], asks: [] },
          volume: m.volume || 0,
          liquidity: m.liquidity || 0,
          title: m.title || '',
          conditionId: m.conditionId || '',
          slug: m.slug || '',
          spread: m.spread,
          competitive: m.competitive || 0,
        })

        newTokenMap.set(m.upToken, { coin: m.coin, side: 'up' })
        newTokenMap.set(m.downToken, { coin: m.coin, side: 'down' })
      }

      tokenMapRef.current = newTokenMap
      setMarkets(newMarkets)
      if (!selectedCoin && newMarkets.length > 0) {
        setSelectedCoin(newMarkets[0].coin)
      }
      return newMarkets
    } catch (err) {
      console.error('failed to fetch markets:', err)
      return []
    }
  }, [selectedCoin])

  const connectClob = useCallback((allTokens: string[]) => {
    if (clobWsRef.current) clobWsRef.current.close()

    const ws = new WebSocket(CLOB_WS)
    clobWsRef.current = ws

    ws.onopen = () => {
      ws.send(JSON.stringify({
        type: 'subscribe',
        channel: 'market',
        assets_ids: allTokens
      }))
    }

    ws.onmessage = (e) => {
      try {
        let data = JSON.parse(e.data)
        if (Array.isArray(data)) data = data[0]
        if (!data) return

        if (data.event_type === 'price_change') {
          for (const pc of data.price_changes || []) {
            const info = tokenMapRef.current.get(pc.asset_id)
            if (!info) continue

            setMarkets(prev => prev.map(m => {
              if (m.coin !== info.coin) return m

              const updated = info.side === 'up'
                ? { ...m, upBid: parseFloat(pc.best_bid || 0), upAsk: parseFloat(pc.best_ask || 1) }
                : { ...m, downBid: parseFloat(pc.best_bid || 0), downAsk: parseFloat(pc.best_ask || 1) }

              // track edge history (throttled to 1/sec)
              const now = Date.now()
              const lastUpdate = lastEdgeUpdateRef.current[m.coin] || 0
              if (now - lastUpdate > 1000) {
                lastEdgeUpdateRef.current[m.coin] = now
                const combinedAsk = updated.upAsk + updated.downAsk
                const combinedBid = updated.upBid + updated.downBid

                const newPoint: EdgePoint = {
                  timestamp: now,
                  combinedAsk,
                  combinedBid,
                  upAsk: updated.upAsk,
                  downAsk: updated.downAsk
                }

                setEdgeHistory(prev => {
                  const history = prev[m.coin] || []
                  const trimmed = [...history, newPoint].slice(-900)
                  return { ...prev, [m.coin]: trimmed }
                })

                // save edge snapshot to sqlite (every 5 sec to reduce writes)
                if (now - lastUpdate > 5000) {
                  fetch('/api/db/snapshots', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                      coin: m.coin,
                      windowTs: m.windowTs,
                      timestamp: now,
                      combinedAsk,
                      combinedBid,
                      upAsk: updated.upAsk,
                      downAsk: updated.downAsk,
                      chainlinkPrice: 0
                    })
                  }).catch(() => {})
                }

                // check for arb alert (gabagool: combined BID < threshold)
                if (combinedBid > 0 && combinedBid < alertThreshold) {
                  const edge = 1 - combinedBid
                  setAlerts(prev => {
                    const recentAlert = prev.find(a => a.coin === m.coin && now - a.timestamp < 10000)
                    if (recentAlert) return prev

                    // save alert to sqlite
                    fetch('/api/db/alerts', {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({
                        coin: m.coin,
                        windowTs: m.windowTs,
                        timestamp: now,
                        combinedBid,
                        edge,
                        upBid: updated.upBid,
                        downBid: updated.downBid
                      })
                    }).catch(() => {})

                    return [{
                      id: `${m.coin}-${now}-${Math.random().toString(36).slice(2, 8)}`,
                      coin: m.coin,
                      timestamp: now,
                      combinedBid,
                      edge,
                      upBid: updated.upBid,
                      downBid: updated.downBid
                    }, ...prev].slice(0, 100)
                  })
                }
              }

              return updated
            }))
          }
        }

        if (data.event_type === 'book') {
          const info = tokenMapRef.current.get(data.asset_id)
          if (!info) return

          const bids = (data.bids || [])
            .map((b: { price: string; size: string }) => ({ price: parseFloat(b.price), size: parseFloat(b.size) }))
            .sort((a: OrderLevel, b: OrderLevel) => b.price - a.price)
            .slice(0, 10)

          const asks = (data.asks || [])
            .map((a: { price: string; size: string }) => ({ price: parseFloat(a.price), size: parseFloat(a.size) }))
            .sort((a: OrderLevel, b: OrderLevel) => a.price - b.price)
            .slice(0, 10)

          setMarkets(prev => prev.map(m => {
            if (m.coin !== info.coin) return m
            if (info.side === 'up') {
              return { ...m, upBook: { bids, asks } }
            } else {
              return { ...m, downBook: { bids, asks } }
            }
          }))
        }
      } catch {}
    }

    ws.onerror = () => setStatus('error')
    ws.onclose = () => {
      setTimeout(() => {
        if (allTokens.length > 0) connectClob(allTokens)
      }, 2000)
    }
  }, [alertThreshold])

  const connectPrices = useCallback(() => {
    if (pricesWsRef.current) pricesWsRef.current.close()

    const ws = new WebSocket(PRICES_WS)
    pricesWsRef.current = ws
    let pingInterval: NodeJS.Timeout | null = null

    ws.onopen = () => {
      ws.send(JSON.stringify({
        action: 'subscribe',
        subscriptions: [{
          topic: 'crypto_prices_chainlink',
          type: '*',
          filters: ''
        }]
      }))
      setStatus('live')

      // send ping every 5 seconds to keep connection alive
      pingInterval = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send('PING')
        }
      }, 5000)
    }

    ws.onmessage = (e) => {
      if (!e.data || !e.data.startsWith('{')) return
      try {
        const data = JSON.parse(e.data)
        if (data.topic === 'crypto_prices_chainlink') {
          const { symbol, value } = data.payload || {}
          if (symbol && value) {
            const coin = symbol.split('/')[0]
            setPrices(prev => ({ ...prev, [coin]: value }))
          }
        }
      } catch {}
    }

    ws.onerror = () => setStatus('error')
    ws.onclose = () => {
      if (pingInterval) clearInterval(pingInterval)
      setTimeout(connectPrices, 2000)
    }
  }, [])

  useEffect(() => {
    const interval = setInterval(() => {
      const now = Math.floor(Date.now() / 1000)
      const windowStart = now - (now % 900)
      const elapsed = now - windowStart
      setProgress((elapsed / 900) * 100)
    }, 1000)
    return () => clearInterval(interval)
  }, [])

  useEffect(() => {
    const init = async () => {
      const mkts = await fetchMarkets()
      if (mkts.length > 0) {
        const tokens = mkts.flatMap(m => [m.upToken, m.downToken])
        connectClob(tokens)
      }
      connectPrices()
    }

    init()
    const interval = setInterval(fetchMarkets, 30000)

    return () => {
      clearInterval(interval)
      clobWsRef.current?.close()
      pricesWsRef.current?.close()
    }
  }, [fetchMarkets, connectClob, connectPrices])

  const formatPrice = (price: number, coin: string) => {
    if (!price) return '-'
    if (coin === 'btc' || coin === 'eth') {
      return price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    }
    return price.toFixed(4)
  }

  const getTimeLeft = (windowTs: number) => {
    const now = Math.floor(Date.now() / 1000)
    const left = windowTs + 900 - now
    return Math.max(0, left / 60).toFixed(1)
  }

  const getPhase = () => {
    const minuteInWindow = (progress / 100) * 15
    if (minuteInWindow < 4) return { name: 'ACCUMULATE', color: 'text-green-400' }
    if (minuteInWindow < 10) return { name: 'REBALANCE', color: 'text-yellow-400' }
    if (minuteInWindow < 15) return { name: 'HOLD', color: 'text-orange-400' }
    return { name: 'RESOLUTION', color: 'text-red-400' }
  }

  const coinColors: Record<string, string> = {
    btc: 'text-orange-500',
    eth: 'text-indigo-400',
    sol: 'text-emerald-400',
    xrp: 'text-gray-400'
  }

  const selectedMarket = markets.find(m => m.coin === selectedCoin)
  const phase = getPhase()

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-gray-200 p-4 font-mono">
      {/* header */}
      <div className="flex justify-between items-center mb-4 pb-3 border-b border-gray-800">
        <div className="flex items-center gap-4">
          <h1 className="text-lg font-medium">Polymarket 15m</h1>
          <div className={`text-sm px-2 py-1 rounded bg-gray-900 ${phase.color}`}>
            {phase.name}
          </div>
        </div>

        <div className="flex gap-3">
          {['btc', 'eth', 'sol', 'xrp'].map(coin => (
            <span key={coin} className={`text-sm px-2 py-1 bg-gray-900 rounded ${coinColors[coin]}`}>
              {coin.toUpperCase()} <span className="font-semibold ml-1">
                {prices[coin] ? `$${formatPrice(prices[coin], coin)}` : '-'}
              </span>
            </span>
          ))}
        </div>

        <div className={`text-sm ${status === 'live' ? 'text-green-400' : status === 'error' ? 'text-red-400' : 'text-gray-500'}`}>
          {status === 'live' ? '● live' : status === 'error' ? '● error' : '○ connecting...'}
        </div>
      </div>

      {/* alerts + edge heatmap row */}
      <div className="grid grid-cols-12 gap-4 mb-4">
        {/* arb alerts */}
        <div className="col-span-4 bg-[#111] border border-gray-800 rounded-lg p-3">
          <div className="flex justify-between items-center mb-2">
            <span className="text-sm text-gray-400">ARB ALERTS</span>
            <div className="flex items-center gap-2 text-xs">
              <span className="text-gray-500">thresh:</span>
              <input
                type="number"
                value={alertThreshold}
                onChange={e => setAlertThreshold(parseFloat(e.target.value) || 0.995)}
                className="w-16 bg-gray-900 border border-gray-700 rounded px-2 py-1 text-xs"
                step={0.001}
                min={0.9}
                max={1}
              />
            </div>
          </div>
          <div className="space-y-1 max-h-32 overflow-y-auto">
            {alerts.length === 0 ? (
              <div className="text-gray-600 text-xs">no recent alerts</div>
            ) : (
              alerts.slice(0, 10).map(a => (
                <div key={a.id} className="flex justify-between items-center text-xs bg-green-500/10 px-2 py-1 rounded">
                  <span className={`font-medium uppercase ${coinColors[a.coin]}`}>{a.coin}</span>
                  <span className="text-cyan-400">{a.combinedBid.toFixed(3)}</span>
                  <span className="text-green-400">+{(a.edge * 100).toFixed(1)}%</span>
                  <span className="text-gray-500">{Math.floor((Date.now() - a.timestamp) / 1000)}s</span>
                </div>
              ))
            )}
          </div>
        </div>

        {/* edge heatmap */}
        <div className="col-span-8 bg-[#111] border border-gray-800 rounded-lg p-3">
          <div className="text-sm text-gray-400 mb-2">EDGE HEATMAP</div>
          <div className="grid grid-cols-4 gap-2">
            {markets.map(m => {
              const combined = m.upAsk + m.downAsk
              const edge = Math.max(0, 1 - combined)
              const hasEdge = combined < 1
              const bgColor = hasEdge
                ? edge > 0.02 ? 'bg-green-500/20' : edge > 0.01 ? 'bg-yellow-500/20' : 'bg-orange-500/20'
                : 'bg-gray-800/50'

              return (
                <div
                  key={m.coin}
                  onClick={() => setSelectedCoin(m.coin)}
                  className={`${bgColor} rounded p-2 cursor-pointer border ${
                    selectedCoin === m.coin ? 'border-blue-500' : 'border-transparent'
                  } transition-all hover:border-gray-600`}
                >
                  <div className={`text-sm font-semibold uppercase ${coinColors[m.coin]}`}>{m.coin}</div>
                  <div className="text-lg font-medium">{combined.toFixed(3)}</div>
                  <div className={`text-sm ${hasEdge ? 'text-green-400' : 'text-gray-500'}`}>
                    {hasEdge ? `+${(edge * 100).toFixed(2)}%` : '--'}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      </div>

      {markets.length === 0 ? (
        <div className="text-gray-500 text-center py-10">loading markets...</div>
      ) : (
        <div className="grid grid-cols-12 gap-4">
          {/* market cards */}
          <div className="col-span-12 lg:col-span-4">
            <div className="grid grid-cols-2 gap-3">
              {markets.map(m => {
                const spotPrice = prices[m.coin] || 0
                const combined = m.upAsk + m.downAsk
                const isSelected = m.coin === selectedCoin

                return (
                  <div
                    key={m.coin}
                    onClick={() => setSelectedCoin(m.coin)}
                    className={`bg-[#111] border rounded-lg p-3 cursor-pointer transition-all ${
                      isSelected ? 'border-blue-500' : 'border-gray-800 hover:border-gray-700'
                    }`}
                  >
                    <div className="flex justify-between items-center mb-2">
                      <span className={`text-base font-semibold uppercase ${coinColors[m.coin]}`}>{m.coin}</span>
                      <span className="text-base font-medium">${formatPrice(spotPrice, m.coin)}</span>
                    </div>

                    <div className="grid grid-cols-2 gap-2 mb-2">
                      <div className="text-center">
                        <div className="text-xs text-gray-500">UP</div>
                        <div className="text-sm text-green-400">{m.upAsk.toFixed(2)}</div>
                      </div>
                      <div className="text-center">
                        <div className="text-xs text-gray-500">DOWN</div>
                        <div className="text-sm text-red-400">{m.downAsk.toFixed(2)}</div>
                      </div>
                    </div>

                    <div className="h-1 bg-gray-800 rounded overflow-hidden mb-2">
                      <div
                        className="h-full bg-gradient-to-r from-green-500 to-yellow-500 transition-all duration-1000"
                        style={{ width: `${progress}%` }}
                      />
                    </div>

                    <div className="flex justify-between text-xs text-gray-500">
                      <span className={combined < 1 ? 'text-yellow-400' : ''}>Σ {combined.toFixed(3)}</span>
                      <span>{getTimeLeft(m.windowTs)}m</span>
                    </div>
                  </div>
                )
              })}
            </div>

            {/* metadata panel */}
            {selectedMarket && (
              <div className="mt-4 bg-[#111] border border-gray-800 rounded-lg p-3 text-xs">
                <div className="text-gray-400 mb-2">WINDOW METADATA</div>
                <div className="space-y-1 text-gray-300">
                  <div className="flex justify-between">
                    <span className="text-gray-500">Window:</span>
                    <span>{new Date(selectedMarket.windowTs * 1000).toLocaleTimeString()}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">Volume:</span>
                    <span>${selectedMarket.volume.toLocaleString()}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">Liquidity:</span>
                    <span>${selectedMarket.liquidity.toLocaleString()}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">Competitive:</span>
                    <span>{(selectedMarket.competitive * 100).toFixed(2)}%</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">Edge History:</span>
                    <span>{(edgeHistory[selectedMarket.coin] || []).length} pts</span>
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* main panel */}
          <div className="col-span-12 lg:col-span-8 space-y-4">
            {/* window tracker */}
            {selectedMarket && prices[selectedCoin || ''] && (
              <WindowTracker
                coin={selectedMarket.coin}
                windowTs={selectedMarket.windowTs}
                currentPrice={prices[selectedMarket.coin] || 0}
                upAsk={selectedMarket.upAsk}
                downAsk={selectedMarket.downAsk}
                onOpenPriceChange={(price) => setTargetPrices(prev => ({ ...prev, [selectedMarket.coin]: price }))}
              />
            )}

            {selectedCoin && prices[selectedCoin] && (
              <PriceChart
                coin={selectedCoin}
                price={prices[selectedCoin]}
                targetPrice={targetPrices[selectedCoin]}
              />
            )}

            {/* edge history chart */}
            {selectedCoin && (edgeHistory[selectedCoin] || []).length > 0 && (
              <HistoricalEdgeChart
                edgeHistory={edgeHistory[selectedCoin] || []}
                coin={selectedCoin}
                threshold={alertThreshold}
              />
            )}

            {/* depth + simulator row */}
            {selectedMarket && (
              <div className="grid grid-cols-2 gap-4">
                <DepthVisualization
                  upBook={selectedMarket.upBook}
                  downBook={selectedMarket.downBook}
                  upAsk={selectedMarket.upAsk}
                  downAsk={selectedMarket.downAsk}
                />
                <PositionSimulator
                  upAsk={selectedMarket.upAsk}
                  downAsk={selectedMarket.downAsk}
                  edgeHistory={edgeHistory[selectedMarket.coin] || []}
                  spotPrice={prices[selectedMarket.coin] || 0}
                />
              </div>
            )}

            {selectedMarket && (
              <div className="bg-[#111] border border-gray-800 rounded-lg p-4">
                <div className="flex justify-between items-center mb-4">
                  <div className="flex items-center gap-3">
                    <span className={`text-xl font-bold uppercase ${coinColors[selectedMarket.coin]}`}>
                      {selectedMarket.coin}
                    </span>
                    <span className="text-2xl font-medium">
                      ${formatPrice(prices[selectedMarket.coin] || 0, selectedMarket.coin)}
                    </span>
                  </div>
                  <div className="text-right text-sm">
                    <div className="text-gray-500">Vol: <span className="text-gray-300">${selectedMarket.volume.toLocaleString()}</span></div>
                    <div className="text-gray-500">Liq: <span className="text-gray-300">${selectedMarket.liquidity.toLocaleString()}</span></div>
                  </div>
                </div>

                {/* combined spread */}
                <div className="mb-4 p-3 bg-[#0a0a0a] rounded-lg">
                  <div className="flex justify-between items-center">
                    <div>
                      <span className="text-gray-500 text-sm">Combined Ask:</span>
                      <span className={`ml-2 text-lg font-bold ${
                        selectedMarket.upAsk + selectedMarket.downAsk < 1 ? 'text-yellow-400' : 'text-gray-200'
                      }`}>
                        {(selectedMarket.upAsk + selectedMarket.downAsk).toFixed(4)}
                      </span>
                      {selectedMarket.upAsk + selectedMarket.downAsk < 1 && (
                        <span className="ml-2 text-yellow-400 text-sm">
                          ARB +{((1 - selectedMarket.upAsk - selectedMarket.downAsk) * 100).toFixed(2)}%
                        </span>
                      )}
                    </div>
                    <div>
                      <span className="text-gray-500 text-sm">Combined Bid:</span>
                      <span className="ml-2 text-lg font-medium">
                        {(selectedMarket.upBid + selectedMarket.downBid).toFixed(4)}
                      </span>
                    </div>
                  </div>
                </div>

                {/* orderbooks */}
                <div className="grid grid-cols-2 gap-4">
                  <OrderBookPanel book={selectedMarket.upBook} side="up" label="UP ORDERBOOK" />
                  <OrderBookPanel book={selectedMarket.downBook} side="down" label="DOWN ORDERBOOK" />
                </div>

                {/* progress */}
                <div className="mt-4">
                  <div className="flex justify-between text-xs text-gray-500 mb-1">
                    <span>{phase.name}</span>
                    <span>{getTimeLeft(selectedMarket.windowTs)}m remaining</span>
                  </div>
                  <div className="h-2 bg-gray-800 rounded overflow-hidden">
                    <div
                      className="h-full bg-gradient-to-r from-green-500 via-yellow-500 to-red-500 transition-all duration-1000"
                      style={{ width: `${progress}%` }}
                    />
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function OrderBookPanel({ book, side, label }: { book: OrderBook; side: 'up' | 'down'; label: string }) {
  const maxSize = Math.max(...book.bids.map(b => b.size), ...book.asks.map(a => a.size), 1)

  return (
    <div className="bg-[#0a0a0a] rounded-lg p-3">
      <div className={`text-xs font-medium mb-2 ${side === 'up' ? 'text-green-500' : 'text-red-500'}`}>
        {label}
      </div>
      <div className="grid grid-cols-2 gap-2 text-xs">
        <div>
          <div className="text-gray-500 mb-1 flex justify-between">
            <span>BID</span><span>SIZE</span>
          </div>
          {book.bids.length === 0 ? (
            <div className="text-gray-600">-</div>
          ) : (
            book.bids.slice(0, 8).map((b, i) => (
              <div key={i} className="flex justify-between relative py-0.5">
                <div
                  className="absolute inset-0 bg-green-500/10"
                  style={{ width: `${(b.size / maxSize) * 100}%` }}
                />
                <span className="text-green-400 relative">{b.price.toFixed(2)}</span>
                <span className="text-gray-400 relative">{b.size.toFixed(0)}</span>
              </div>
            ))
          )}
        </div>
        <div>
          <div className="text-gray-500 mb-1 flex justify-between">
            <span>ASK</span><span>SIZE</span>
          </div>
          {book.asks.length === 0 ? (
            <div className="text-gray-600">-</div>
          ) : (
            book.asks.slice(0, 8).map((a, i) => (
              <div key={i} className="flex justify-between relative py-0.5">
                <div
                  className="absolute inset-0 bg-red-500/10"
                  style={{ width: `${(a.size / maxSize) * 100}%` }}
                />
                <span className="text-red-400 relative">{a.price.toFixed(2)}</span>
                <span className="text-gray-400 relative">{a.size.toFixed(0)}</span>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  )
}
