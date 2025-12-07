'use client'

import { useEffect, useRef } from 'react'
import { createChart, ColorType, IChartApi, ISeriesApi, LineStyle, Time } from 'lightweight-charts'

interface EdgePoint {
  timestamp: number
  combinedAsk: number
  combinedBid: number
  upAsk: number
  downAsk: number
}

interface HistoricalEdgeChartProps {
  edgeHistory: EdgePoint[]
  coin: string
  threshold: number
}

export default function HistoricalEdgeChart({ edgeHistory, coin, threshold }: HistoricalEdgeChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const askSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const bidSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)

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
      height: 200,
      timeScale: {
        timeVisible: true,
        secondsVisible: true,
        borderColor: '#333',
      },
      rightPriceScale: {
        borderColor: '#333',
        autoScale: false,
        scaleMargins: { top: 0.1, bottom: 0.1 },
      },
      crosshair: {
        horzLine: { color: '#555' },
        vertLine: { color: '#555' },
      },
    })

    // break-even line at 1.00
    const breakEvenSeries = chart.addLineSeries({
      color: '#666',
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      priceLineVisible: false,
    })

    // threshold line
    const thresholdSeries = chart.addLineSeries({
      color: '#f59e0b',
      lineWidth: 1,
      lineStyle: LineStyle.Dotted,
      priceLineVisible: false,
    })

    // combined ask line
    const askSeries = chart.addLineSeries({
      color: '#f87171',
      lineWidth: 2,
      priceLineVisible: false,
      title: 'Ask',
    })

    // combined bid line
    const bidSeries = chart.addLineSeries({
      color: '#4ade80',
      lineWidth: 2,
      priceLineVisible: false,
      title: 'Bid',
    })

    chartRef.current = chart
    askSeriesRef.current = askSeries
    bidSeriesRef.current = bidSeries

    // set static reference lines
    const now = Math.floor(Date.now() / 1000)
    const refData = [
      { time: (now - 900) as Time, value: 1.00 },
      { time: now as Time, value: 1.00 },
    ]
    breakEvenSeries.setData(refData)
    thresholdSeries.setData([
      { time: (now - 900) as Time, value: threshold },
      { time: now as Time, value: threshold },
    ])

    // set price scale range
    chart.priceScale('right').applyOptions({
      autoScale: false,
      scaleMargins: { top: 0.05, bottom: 0.05 },
    })

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
  }, [threshold])

  // update data when edgeHistory changes
  useEffect(() => {
    if (!askSeriesRef.current || !bidSeriesRef.current || edgeHistory.length === 0) return

    const askData = edgeHistory.map(p => ({
      time: Math.floor(p.timestamp / 1000) as Time,
      value: p.combinedAsk,
    }))

    const bidData = edgeHistory.map(p => ({
      time: Math.floor(p.timestamp / 1000) as Time,
      value: p.combinedBid,
    }))

    askSeriesRef.current.setData(askData)
    bidSeriesRef.current.setData(bidData)

    // auto-scale to visible data range
    if (chartRef.current && edgeHistory.length > 0) {
      const minVal = Math.min(
        ...edgeHistory.map(p => Math.min(p.combinedAsk, p.combinedBid))
      )
      const maxVal = Math.max(
        ...edgeHistory.map(p => Math.max(p.combinedAsk, p.combinedBid)),
        1.02
      )
      chartRef.current.priceScale('right').applyOptions({
        autoScale: true,
      })
    }
  }, [edgeHistory])

  const latestAsk = edgeHistory.length > 0 ? edgeHistory[edgeHistory.length - 1].combinedAsk : null
  const latestBid = edgeHistory.length > 0 ? edgeHistory[edgeHistory.length - 1].combinedBid : null
  const hasEdge = latestAsk !== null && latestAsk < 1

  return (
    <div className="bg-[#111] border border-gray-800 rounded-lg p-4">
      <div className="flex justify-between items-center mb-3">
        <span className="text-sm text-gray-400">{coin.toUpperCase()} Edge History</span>
        <div className="flex gap-4 text-sm">
          <span className="text-red-400">Ask: {latestAsk?.toFixed(4) || '-'}</span>
          <span className="text-green-400">Bid: {latestBid?.toFixed(4) || '-'}</span>
          {hasEdge && (
            <span className="text-yellow-400">Edge: +{((1 - latestAsk!) * 100).toFixed(2)}%</span>
          )}
        </div>
      </div>
      <div ref={chartContainerRef} />
      <div className="flex justify-between text-xs text-gray-500 mt-2">
        <span>-- Break-even (1.00)</span>
        <span className="text-yellow-500">·· Threshold ({threshold})</span>
        <span>{edgeHistory.length} pts</span>
      </div>
    </div>
  )
}
