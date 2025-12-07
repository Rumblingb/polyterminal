'use client'

import { useEffect, useState, useRef } from 'react'

interface WindowRecord {
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

interface WindowTrackerProps {
  coin: string
  windowTs: number
  currentPrice: number
  upAsk: number
  downAsk: number
  onWindowChange?: (windowTs: number) => void
  onOpenPriceChange?: (price: number | null) => void
}

interface WindowData {
  openPrice: number | null
  closePrice: number | null
  completed: boolean
  upPrice: number | null
  downPrice: number | null
}

export default function WindowTracker({
  coin,
  windowTs,
  currentPrice,
  upAsk,
  downAsk,
  onWindowChange,
  onOpenPriceChange
}: WindowTrackerProps) {
  const [windowData, setWindowData] = useState<WindowData | null>(null)
  const [timeLeft, setTimeLeft] = useState({ mins: 0, secs: 0 })
  const [recentWindows, setRecentWindows] = useState<WindowRecord[]>([])

  // use refs to avoid callback recreation
  const propsRef = useRef({ upAsk, downAsk, onOpenPriceChange })
  useEffect(() => {
    propsRef.current = { upAsk, downAsk, onOpenPriceChange }
  }, [upAsk, downAsk, onOpenPriceChange])

  // fetch window data only when coin/windowTs changes
  useEffect(() => {
    let cancelled = false

    const fetchWindowData = async () => {
      try {
        const res = await fetch(`/api/window?coin=${coin}&windowTs=${windowTs}`)
        if (!res.ok || cancelled) return
        const data = await res.json()
        if (cancelled) return

        if (data.openPrice !== undefined) {
          setWindowData(prev => ({
            openPrice: data.openPrice ?? prev?.openPrice ?? null,
            closePrice: data.closePrice ?? prev?.closePrice ?? null,
            completed: data.completed ?? prev?.completed ?? false,
            upPrice: data.upPrice ?? prev?.upPrice ?? null,
            downPrice: data.downPrice ?? prev?.downPrice ?? null,
          }))
          propsRef.current.onOpenPriceChange?.(data.openPrice || null)
        }
      } catch {}
    }

    setWindowData(null) // reset on window change
    fetchWindowData()
    const interval = setInterval(fetchWindowData, 30000)

    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [coin, windowTs])

  // load recent windows from sqlite
  useEffect(() => {
    fetch(`/api/db/windows?coin=${coin}`)
      .then(res => res.json())
      .then(data => setRecentWindows(data.slice(0, 6)))
      .catch(() => {})
  }, [coin, windowTs])

  // countdown timer
  useEffect(() => {
    const update = () => {
      const now = Math.floor(Date.now() / 1000)
      const endTs = windowTs + 900
      const left = Math.max(0, endTs - now)
      setTimeLeft({
        mins: Math.floor(left / 60),
        secs: left % 60
      })
    }
    update()
    const interval = setInterval(update, 1000)
    return () => clearInterval(interval)
  }, [windowTs])

  const priceToBeat = windowData?.openPrice
  const priceDiff = priceToBeat && currentPrice ? currentPrice - priceToBeat : 0
  const isUp = priceDiff >= 0
  const isResolved = windowData?.completed

  // generate window tabs (current + next 3)
  const now = Math.floor(Date.now() / 1000)
  const currentWindowStart = now - (now % 900)
  const windowTabs = [0, 1, 2, 3].map(i => {
    const ts = currentWindowStart + (i * 900)
    const date = new Date(ts * 1000)
    const hours = date.getHours()
    const mins = date.getMinutes()
    const ampm = hours >= 12 ? 'PM' : 'AM'
    const h = hours % 12 || 12
    return {
      ts,
      label: `${h}:${mins.toString().padStart(2, '0')}${ampm}`,
      isCurrent: ts === windowTs,
      isPast: ts + 900 < now,
    }
  })

  return (
    <div className="bg-[#111] border border-gray-800 rounded-lg p-4">
      {/* header with price to beat */}
      <div className="flex justify-between items-start mb-4">
        <div>
          <div className="text-xs text-gray-500 uppercase tracking-wide">price to beat</div>
          <div className="text-2xl font-mono text-gray-200">
            {priceToBeat
              ? `$${priceToBeat.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
              : '--'
            }
          </div>
        </div>

        <div className="text-right">
          <div className="text-xs text-gray-500 uppercase tracking-wide">current</div>
          <div className={`text-2xl font-mono ${isUp ? 'text-green-400' : 'text-red-400'}`}>
            ${currentPrice.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </div>
          {priceToBeat && (
            <div className={`text-sm font-mono ${isUp ? 'text-green-500' : 'text-red-500'}`}>
              {isUp ? '↑' : '↓'} ${Math.abs(priceDiff).toFixed(2)}
            </div>
          )}
        </div>

        {/* countdown */}
        <div className="text-right">
          <div className="text-xs text-gray-500 uppercase tracking-wide">
            {isResolved ? 'resolved' : 'remaining'}
          </div>
          {isResolved ? (
            <div className="text-lg font-mono text-yellow-400">
              {windowData?.closePrice && priceToBeat
                ? (windowData.closePrice >= priceToBeat ? 'UP ✓' : 'DOWN ✓')
                : '--'
              }
            </div>
          ) : (
            <div className="text-2xl font-mono text-orange-400">
              {timeLeft.mins.toString().padStart(2, '0')}:{timeLeft.secs.toString().padStart(2, '0')}
            </div>
          )}
        </div>
      </div>

      {/* current odds */}
      <div className="flex gap-4 mb-4">
        <div className="flex-1 bg-green-500/10 border border-green-500/30 rounded-lg p-3 text-center">
          <div className="text-xs text-gray-500 uppercase">UP</div>
          <div className="text-2xl font-mono text-green-400">{upAsk.toFixed(2)}¢</div>
          <div className="text-xs text-gray-500">{(upAsk * 100).toFixed(0)}%</div>
        </div>
        <div className="flex-1 bg-red-500/10 border border-red-500/30 rounded-lg p-3 text-center">
          <div className="text-xs text-gray-500 uppercase">DOWN</div>
          <div className="text-2xl font-mono text-red-400">{downAsk.toFixed(2)}¢</div>
          <div className="text-xs text-gray-500">{(downAsk * 100).toFixed(0)}%</div>
        </div>
        <div className={`flex-1 rounded-lg p-3 text-center ${
          upAsk + downAsk < 1
            ? 'bg-yellow-500/10 border border-yellow-500/30'
            : 'bg-gray-800 border border-gray-700'
        }`}>
          <div className="text-xs text-gray-500 uppercase">COMBINED</div>
          <div className={`text-2xl font-mono ${upAsk + downAsk < 1 ? 'text-yellow-400' : 'text-gray-300'}`}>
            {(upAsk + downAsk).toFixed(3)}
          </div>
          {upAsk + downAsk < 1 && (
            <div className="text-xs text-green-400">+{((1 - upAsk - downAsk) * 100).toFixed(2)}% edge</div>
          )}
        </div>
      </div>

      {/* direction indicator */}
      {priceToBeat && !isResolved && (
        <div className={`mb-4 p-2 rounded text-center text-sm font-medium ${
          isUp ? 'bg-green-500/10 text-green-400 border border-green-500/30'
               : 'bg-red-500/10 text-red-400 border border-red-500/30'
        }`}>
          {isUp ? '↑ ABOVE OPEN — UP WINNING' : '↓ BELOW OPEN — DOWN WINNING'}
        </div>
      )}

      {/* window tabs */}
      <div className="flex gap-1 mb-3 overflow-x-auto">
        {windowTabs.map(tab => (
          <button
            key={tab.ts}
            onClick={() => onWindowChange?.(tab.ts)}
            className={`px-3 py-1.5 text-xs font-mono rounded transition-all whitespace-nowrap ${
              tab.isCurrent
                ? 'bg-blue-500/20 text-blue-400 border border-blue-500/50'
                : tab.isPast
                  ? 'bg-gray-800/50 text-gray-600 border border-gray-700/50'
                  : 'bg-gray-800/30 text-gray-400 border border-gray-700/30 hover:border-gray-600'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* recent windows history */}
      {recentWindows.length > 0 && (
        <div className="border-t border-gray-800 pt-3 mt-3">
          <div className="text-xs text-gray-500 mb-2">recent {coin.toUpperCase()} windows</div>
          <div className="flex gap-1 flex-wrap">
            {recentWindows.slice(0, 6).map(w => {
              const time = new Date(w.windowTs * 1000)
              const label = `${time.getHours() % 12 || 12}:${time.getMinutes().toString().padStart(2, '0')}`
              return (
                <div
                  key={w.windowTs}
                  className={`px-2 py-1 text-xs font-mono rounded ${
                    w.resolved
                      ? w.outcome === 'up'
                        ? 'bg-green-500/10 text-green-400'
                        : 'bg-red-500/10 text-red-400'
                      : 'bg-gray-800 text-gray-500'
                  }`}
                >
                  {label} {w.resolved ? (w.outcome === 'up' ? '↑' : '↓') : '?'}
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
