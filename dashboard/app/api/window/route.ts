import { NextResponse } from 'next/server'

const GAMMA_API = 'https://gamma-api.polymarket.com'

// proxy polymarket's crypto-price API for PRICE TO BEAT
async function fetchOpenPrice(symbol: string, startTime: string, endTime: string) {
  try {
    const url = `https://polymarket.com/api/crypto/crypto-price?symbol=${symbol.toUpperCase()}&eventStartTime=${startTime}&variant=fifteen&endDate=${endTime}`
    const res = await fetch(url)
    if (!res.ok) return null
    return await res.json()
  } catch {
    return null
  }
}

// get market by slug
async function fetchMarketBySlug(slug: string) {
  try {
    const res = await fetch(`${GAMMA_API}/markets/slug/${slug}`)
    if (!res.ok) return null
    return await res.json()
  } catch {
    return null
  }
}

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url)
  const coin = searchParams.get('coin') || 'btc'
  const windowTs = searchParams.get('windowTs')

  if (!windowTs) {
    return NextResponse.json({ error: 'windowTs required' }, { status: 400 })
  }

  const ts = parseInt(windowTs)
  const slug = `${coin}-updown-15m-${ts}`

  // fetch market data
  const market = await fetchMarketBySlug(slug)
  if (!market) {
    return NextResponse.json({ error: 'market not found' }, { status: 404 })
  }

  // parse dates
  const startTime = market.eventStartTime || market.events?.[0]?.startTime
  const endTime = market.endDate

  // fetch open price (PRICE TO BEAT)
  const priceData = startTime && endTime
    ? await fetchOpenPrice(coin, startTime, endTime)
    : null

  // parse token IDs
  let clobTokenIds = market.clobTokenIds
  if (typeof clobTokenIds === 'string') {
    clobTokenIds = JSON.parse(clobTokenIds)
  }

  // parse outcome prices
  let outcomePrices = market.outcomePrices
  if (typeof outcomePrices === 'string') {
    outcomePrices = JSON.parse(outcomePrices)
  }

  return NextResponse.json({
    slug,
    coin,
    windowTs: ts,

    // timing
    eventStartTime: startTime,
    endDate: endTime,
    windowStart: new Date(ts * 1000).toISOString(),
    windowEnd: new Date((ts + 900) * 1000).toISOString(),

    // price to beat
    openPrice: priceData?.openPrice || null,
    closePrice: priceData?.closePrice || null,
    completed: priceData?.completed || false,

    // tokens
    upToken: clobTokenIds?.[0] || null,
    downToken: clobTokenIds?.[1] || null,

    // current prices
    upPrice: outcomePrices?.[0] ? parseFloat(outcomePrices[0]) : null,
    downPrice: outcomePrices?.[1] ? parseFloat(outcomePrices[1]) : null,
    bestBid: market.bestBid ? parseFloat(market.bestBid) : null,
    bestAsk: market.bestAsk ? parseFloat(market.bestAsk) : null,
    lastTradePrice: market.lastTradePrice ? parseFloat(market.lastTradePrice) : null,
    spread: market.spread ? parseFloat(market.spread) : null,

    // volume
    volume: parseFloat(market.volume || 0),
    liquidity: parseFloat(market.liquidity || 0),

    // metadata
    title: market.question,
    conditionId: market.conditionId,
    seriesSlug: market.events?.[0]?.seriesSlug,
    competitive: market.competitive,
    closed: market.closed,
    active: market.active,
  })
}
