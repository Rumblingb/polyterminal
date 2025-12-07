'use client'

interface OrderLevel {
  price: number
  size: number
}

interface OrderBook {
  bids: OrderLevel[]
  asks: OrderLevel[]
}

interface DepthVisualizationProps {
  upBook: OrderBook
  downBook: OrderBook
  upAsk: number
  downAsk: number
}

// show combined depth - what you'd pay for UP + DOWN at each level
export default function DepthVisualization({ upBook, downBook, upAsk, downAsk }: DepthVisualizationProps) {
  // build combined ask levels
  // for arb, we care about what we pay: combine asks
  const combinedLevels: { combined: number; upPrice: number; downPrice: number; availableSize: number }[] = []

  // simple approach: pair up asks by index
  const maxLevels = Math.min(upBook.asks.length, downBook.asks.length, 8)

  for (let i = 0; i < maxLevels; i++) {
    const upLevel = upBook.asks[i]
    const downLevel = downBook.asks[i]
    if (!upLevel || !downLevel) continue

    const combined = upLevel.price + downLevel.price
    const availableSize = Math.min(upLevel.size, downLevel.size)

    combinedLevels.push({
      combined,
      upPrice: upLevel.price,
      downPrice: downLevel.price,
      availableSize,
    })
  }

  const maxSize = Math.max(...combinedLevels.map(l => l.availableSize), 1)
  const currentCombined = upAsk + downAsk
  const hasEdge = currentCombined < 1

  return (
    <div className="bg-[#111] border border-gray-800 rounded-lg p-4">
      <div className="flex justify-between items-center mb-3">
        <span className="text-sm text-gray-400">COMBINED DEPTH</span>
        <div className="text-sm">
          <span className="text-gray-500">Best Combined: </span>
          <span className={hasEdge ? 'text-yellow-400 font-medium' : 'text-gray-200'}>
            {currentCombined.toFixed(4)}
          </span>
          {hasEdge && (
            <span className="text-green-400 ml-2">+{((1 - currentCombined) * 100).toFixed(2)}%</span>
          )}
        </div>
      </div>

      {combinedLevels.length === 0 ? (
        <div className="text-gray-600 text-sm text-center py-4">no orderbook data</div>
      ) : (
        <div className="space-y-1">
          <div className="flex justify-between text-xs text-gray-500 mb-2">
            <span>COMBINED</span>
            <span>UP + DOWN</span>
            <span>SIZE</span>
            <span>EDGE</span>
          </div>

          {combinedLevels.map((level, i) => {
            const edge = 1 - level.combined
            const isArb = level.combined < 1

            return (
              <div
                key={i}
                className={`relative flex justify-between items-center py-1.5 px-2 rounded text-sm ${
                  isArb ? 'bg-yellow-500/10' : 'bg-gray-800/30'
                }`}
              >
                {/* depth bar */}
                <div
                  className={`absolute left-0 top-0 h-full rounded ${
                    isArb ? 'bg-green-500/20' : 'bg-gray-700/30'
                  }`}
                  style={{ width: `${(level.availableSize / maxSize) * 100}%` }}
                />

                <span className={`relative font-mono ${isArb ? 'text-yellow-400' : 'text-gray-300'}`}>
                  {level.combined.toFixed(4)}
                </span>
                <span className="relative text-gray-500 text-xs">
                  {level.upPrice.toFixed(2)} + {level.downPrice.toFixed(2)}
                </span>
                <span className="relative text-gray-400">
                  {level.availableSize.toFixed(0)}
                </span>
                <span className={`relative ${isArb ? 'text-green-400' : 'text-gray-600'}`}>
                  {isArb ? `+${(edge * 100).toFixed(2)}%` : '--'}
                </span>
              </div>
            )
          })}
        </div>
      )}

      {/* legend */}
      <div className="mt-3 pt-3 border-t border-gray-800 text-xs text-gray-500">
        <div className="flex justify-between">
          <span>Size = min(UP ask size, DOWN ask size)</span>
          <span className="text-yellow-500">Yellow = arb zone (&lt;1.00)</span>
        </div>
      </div>
    </div>
  )
}
