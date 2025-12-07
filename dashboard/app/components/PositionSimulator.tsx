'use client'

import { useState, useMemo } from 'react'

interface EdgePoint {
  timestamp: number
  combinedAsk: number
  combinedBid: number
  upAsk: number
  downAsk: number
}

interface PositionSimulatorProps {
  upAsk: number
  downAsk: number
  edgeHistory: EdgePoint[]
  spotPrice: number
}

export default function PositionSimulator({ upAsk, downAsk, edgeHistory, spotPrice }: PositionSimulatorProps) {
  const [upBidInput, setUpBidInput] = useState('')
  const [downBidInput, setDownBidInput] = useState('')
  const [shares, setShares] = useState('100')

  const upBid = parseFloat(upBidInput) || 0
  const downBid = parseFloat(downBidInput) || 0
  const shareCount = parseFloat(shares) || 100

  // calculate metrics
  const metrics = useMemo(() => {
    if (!upBid || !downBid) return null

    const combined = upBid + downBid
    const edge = 1 - combined
    const totalCost = combined * shareCount
    const guaranteedReturn = shareCount // one side always wins $1
    const profit = guaranteedReturn - totalCost

    // fill probability estimate based on historical spread
    // if our bid is below current ask, estimate how often price dips that low
    const upFillProb = edgeHistory.length > 10
      ? edgeHistory.filter(p => p.upAsk <= upBid).length / edgeHistory.length
      : upBid >= upAsk ? 0.9 : 0.3

    const downFillProb = edgeHistory.length > 10
      ? edgeHistory.filter(p => p.downAsk <= downBid).length / edgeHistory.length
      : downBid >= downAsk ? 0.9 : 0.3

    const bothFillProb = Math.min(upFillProb, downFillProb)

    // risk scenarios
    const onlyUpFillsLoss = upBid * shareCount // lose cost if DOWN wins
    const onlyDownFillsLoss = downBid * shareCount // lose cost if UP wins
    const onlyUpFillsWin = (1 - upBid) * shareCount // profit if UP wins
    const onlyDownFillsWin = (1 - downBid) * shareCount // profit if DOWN wins

    return {
      combined,
      edge,
      totalCost,
      profit,
      upFillProb,
      downFillProb,
      bothFillProb,
      onlyUpFillsLoss,
      onlyDownFillsLoss,
      onlyUpFillsWin,
      onlyDownFillsWin,
    }
  }, [upBid, downBid, shareCount, upAsk, downAsk, edgeHistory])

  // auto-suggest arb bids
  const suggestArb = () => {
    // suggest bids that sum to ~0.97 (3% edge)
    const targetCombined = 0.97
    const upSuggest = Math.min(upAsk - 0.01, targetCombined / 2)
    const downSuggest = Math.min(downAsk - 0.01, targetCombined / 2)
    setUpBidInput(upSuggest.toFixed(2))
    setDownBidInput(downSuggest.toFixed(2))
  }

  const suggestAggressive = () => {
    // bid at current ask prices (market take)
    setUpBidInput(upAsk.toFixed(2))
    setDownBidInput(downAsk.toFixed(2))
  }

  return (
    <div className="bg-[#111] border border-gray-800 rounded-lg p-4">
      <div className="flex justify-between items-center mb-3">
        <span className="text-sm text-gray-400">POSITION SIMULATOR</span>
        <div className="flex gap-2">
          <button
            onClick={suggestArb}
            className="text-xs px-2 py-1 bg-yellow-500/20 text-yellow-400 rounded hover:bg-yellow-500/30"
          >
            suggest arb
          </button>
          <button
            onClick={suggestAggressive}
            className="text-xs px-2 py-1 bg-red-500/20 text-red-400 rounded hover:bg-red-500/30"
          >
            market take
          </button>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-3 mb-4">
        <div>
          <label className="text-xs text-gray-500 block mb-1">UP Bid</label>
          <input
            type="number"
            value={upBidInput}
            onChange={e => setUpBidInput(e.target.value)}
            placeholder={upAsk.toFixed(2)}
            className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-sm"
            step={0.01}
            min={0}
            max={1}
          />
          <div className="text-xs text-gray-500 mt-1">ask: {upAsk.toFixed(2)}</div>
        </div>
        <div>
          <label className="text-xs text-gray-500 block mb-1">DOWN Bid</label>
          <input
            type="number"
            value={downBidInput}
            onChange={e => setDownBidInput(e.target.value)}
            placeholder={downAsk.toFixed(2)}
            className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-sm"
            step={0.01}
            min={0}
            max={1}
          />
          <div className="text-xs text-gray-500 mt-1">ask: {downAsk.toFixed(2)}</div>
        </div>
        <div>
          <label className="text-xs text-gray-500 block mb-1">Shares</label>
          <input
            type="number"
            value={shares}
            onChange={e => setShares(e.target.value)}
            className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-sm"
            step={10}
            min={1}
          />
        </div>
      </div>

      {metrics && (
        <>
          {/* summary */}
          <div className={`p-3 rounded-lg mb-3 ${metrics.edge > 0 ? 'bg-green-500/10' : 'bg-red-500/10'}`}>
            <div className="grid grid-cols-3 gap-4 text-sm">
              <div>
                <div className="text-gray-500 text-xs">Combined</div>
                <div className={metrics.edge > 0 ? 'text-green-400' : 'text-red-400'}>
                  {metrics.combined.toFixed(4)}
                </div>
              </div>
              <div>
                <div className="text-gray-500 text-xs">Edge</div>
                <div className={metrics.edge > 0 ? 'text-green-400' : 'text-red-400'}>
                  {metrics.edge > 0 ? '+' : ''}{(metrics.edge * 100).toFixed(2)}%
                </div>
              </div>
              <div>
                <div className="text-gray-500 text-xs">Profit (if both fill)</div>
                <div className={metrics.profit > 0 ? 'text-green-400' : 'text-red-400'}>
                  ${metrics.profit.toFixed(2)}
                </div>
              </div>
            </div>
          </div>

          {/* fill probability */}
          <div className="mb-3">
            <div className="text-xs text-gray-500 mb-2">FILL PROBABILITY (est.)</div>
            <div className="grid grid-cols-3 gap-2 text-sm">
              <div className="bg-gray-800/50 rounded p-2">
                <div className="text-gray-500 text-xs">UP fills</div>
                <div className="text-green-400">{(metrics.upFillProb * 100).toFixed(0)}%</div>
              </div>
              <div className="bg-gray-800/50 rounded p-2">
                <div className="text-gray-500 text-xs">DOWN fills</div>
                <div className="text-red-400">{(metrics.downFillProb * 100).toFixed(0)}%</div>
              </div>
              <div className="bg-gray-800/50 rounded p-2">
                <div className="text-gray-500 text-xs">BOTH fill</div>
                <div className="text-yellow-400">{(metrics.bothFillProb * 100).toFixed(0)}%</div>
              </div>
            </div>
          </div>

          {/* risk scenarios */}
          <div>
            <div className="text-xs text-gray-500 mb-2">PARTIAL FILL RISK</div>
            <div className="grid grid-cols-2 gap-2 text-xs">
              <div className="bg-gray-800/30 rounded p-2">
                <div className="text-gray-500 mb-1">Only UP fills:</div>
                <div className="flex justify-between">
                  <span className="text-red-400">if DOWN wins: -${metrics.onlyUpFillsLoss.toFixed(2)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-green-400">if UP wins: +${metrics.onlyUpFillsWin.toFixed(2)}</span>
                </div>
              </div>
              <div className="bg-gray-800/30 rounded p-2">
                <div className="text-gray-500 mb-1">Only DOWN fills:</div>
                <div className="flex justify-between">
                  <span className="text-red-400">if UP wins: -${metrics.onlyDownFillsLoss.toFixed(2)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-green-400">if DOWN wins: +${metrics.onlyDownFillsWin.toFixed(2)}</span>
                </div>
              </div>
            </div>
          </div>
        </>
      )}

      {!metrics && (
        <div className="text-gray-500 text-sm text-center py-4">
          enter bid prices to simulate
        </div>
      )}
    </div>
  )
}
