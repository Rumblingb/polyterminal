'use client'

import { useEffect, useRef } from 'react'
import { createChart, ColorType, IChartApi, ISeriesApi, CandlestickData, Time } from 'lightweight-charts'

interface PriceChartProps {
  coin: string
  price: number
  targetPrice?: number | null
}

interface Candle {
  time: Time
  open: number
  high: number
  low: number
  close: number
}

export default function PriceChart({ coin, price, targetPrice }: PriceChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const candlesRef = useRef<Map<number, Candle>>(new Map())
  const lastPriceRef = useRef<number>(0)
  const targetLineRef = useRef<any>(null)

  useEffect(() => {
    if (!chartContainerRef.current) return

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#0a0a0a' },
        textColor: '#888',
      },
      grid: {
        vertLines: { color: '#1a1a1a' },
        horzLines: { color: '#1a1a1a' },
      },
      width: chartContainerRef.current.clientWidth,
      height: 300,
      timeScale: {
        timeVisible: true,
        secondsVisible: true,
        borderColor: '#333',
      },
      rightPriceScale: {
        borderColor: '#333',
      },
      crosshair: {
        horzLine: { color: '#555' },
        vertLine: { color: '#555' },
      },
    })

    const series = chart.addCandlestickSeries({
      upColor: '#4ade80',
      downColor: '#f87171',
      borderUpColor: '#4ade80',
      borderDownColor: '#f87171',
      wickUpColor: '#4ade80',
      wickDownColor: '#f87171',
    })

    chartRef.current = chart
    seriesRef.current = series
    targetLineRef.current = null

    const handleResize = () => {
      if (chartContainerRef.current) {
        chart.applyOptions({ width: chartContainerRef.current.clientWidth })
      }
    }
    window.addEventListener('resize', handleResize)

    return () => {
      window.removeEventListener('resize', handleResize)
      chart.remove()
    }
  }, [])

  useEffect(() => {
    if (!price || !seriesRef.current) return
    if (price === lastPriceRef.current) return
    lastPriceRef.current = price

    const now = Math.floor(Date.now() / 1000)
    const candleTime = (now - (now % 5)) as Time

    const candles = candlesRef.current
    const existing = candles.get(candleTime as number)

    if (existing) {
      existing.high = Math.max(existing.high, price)
      existing.low = Math.min(existing.low, price)
      existing.close = price
    } else {
      const times = Array.from(candles.keys()).sort((a, b) => b - a)
      const prevClose = times.length > 0 ? candles.get(times[0])?.close || price : price

      candles.set(candleTime as number, {
        time: candleTime,
        open: prevClose,
        high: price,
        low: price,
        close: price,
      })

      if (candles.size > 200) {
        const oldest = Math.min(...candles.keys())
        candles.delete(oldest)
      }
    }

    const sortedCandles = Array.from(candles.values()).sort(
      (a, b) => (a.time as number) - (b.time as number)
    )
    seriesRef.current.setData(sortedCandles as CandlestickData[])

    // auto-scale to include target price if set
    if (targetPrice && chartRef.current) {
      const allPrices = sortedCandles.flatMap(c => [c.high, c.low])
      if (allPrices.length > 0) {
        const minPrice = Math.min(...allPrices, targetPrice)
        const maxPrice = Math.max(...allPrices, targetPrice)
        const padding = (maxPrice - minPrice) * 0.1 || maxPrice * 0.001
        chartRef.current.priceScale('right').applyOptions({
          autoScale: false,
        })
        seriesRef.current.applyOptions({
          autoscaleInfoProvider: () => ({
            priceRange: {
              minValue: minPrice - padding,
              maxValue: maxPrice + padding,
            },
          }),
        })
      }
    }
  }, [price, targetPrice])

  // update target price line
  useEffect(() => {
    if (!seriesRef.current || !targetPrice) {
      if (targetLineRef.current) {
        seriesRef.current?.removePriceLine(targetLineRef.current)
        targetLineRef.current = null
      }
      return
    }

    if (targetLineRef.current) {
      seriesRef.current.removePriceLine(targetLineRef.current)
    }

    targetLineRef.current = seriesRef.current.createPriceLine({
      price: targetPrice,
      color: '#f59e0b',
      lineWidth: 2,
      lineStyle: 0, // solid
      axisLabelVisible: true,
      title: 'TARGET',
    })
  }, [targetPrice])

  const diff = targetPrice ? price - targetPrice : 0
  const isAbove = diff >= 0

  return (
    <div className="bg-[#111] border border-gray-800 rounded-lg p-4">
      <div className="flex justify-between items-center mb-3">
        <span className="text-sm text-gray-400">{coin.toUpperCase()}/USD Chainlink (5s candles)</span>
        <div className="flex items-center gap-4">
          {targetPrice && (
            <div className="flex items-center gap-2">
              <span className="text-xs text-gray-500">TARGET</span>
              <span className="text-sm font-mono text-amber-500">
                ${targetPrice.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </span>
              <span className={`text-xs font-mono px-1.5 py-0.5 rounded ${
                isAbove ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
              }`}>
                {isAbove ? '↑' : '↓'} ${Math.abs(diff).toFixed(2)}
              </span>
            </div>
          )}
          <span className="text-lg font-medium text-orange-500">
            ${price?.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) || '-'}
          </span>
        </div>
      </div>
      <div ref={chartContainerRef} />
    </div>
  )
}
