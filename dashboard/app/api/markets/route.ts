import { NextResponse } from 'next/server'

const GAMMA_API = 'https://gamma-api.polymarket.com'

export async function GET() {
  try {
    const res = await fetch(`${GAMMA_API}/events?tag_id=102467&closed=false&limit=20`)
    const events = await res.json()

    const now = Math.floor(Date.now() / 1000)
    const markets = []

    for (const event of events) {
      const slug = event.slug || ''
      const match = slug.toLowerCase().match(/(btc|eth|sol|xrp).*15m-(\d+)/)
      if (!match) continue

      const coin = match[1]
      const windowTs = parseInt(match[2])

      if (!(windowTs <= now && now <= windowTs + 900)) continue

      const mkt = event.markets?.[0]
      if (!mkt) continue

      let tokens = mkt.clobTokenIds
      if (typeof tokens === 'string') tokens = JSON.parse(tokens)
      if (!tokens || tokens.length < 2) continue

      let outcomePrices = mkt.outcomePrices
      if (typeof outcomePrices === 'string') outcomePrices = JSON.parse(outcomePrices)

      markets.push({
        coin,
        windowTs,
        windowStart: new Date(windowTs * 1000).toISOString(),
        windowEnd: new Date((windowTs + 900) * 1000).toISOString(),
        upToken: tokens[0],
        downToken: tokens[1],

        // pricing
        upPrice: outcomePrices?.[0] ? parseFloat(outcomePrices[0]) : null,
        downPrice: outcomePrices?.[1] ? parseFloat(outcomePrices[1]) : null,
        bestBid: mkt.bestBid ? parseFloat(mkt.bestBid) : null,
        bestAsk: mkt.bestAsk ? parseFloat(mkt.bestAsk) : null,
        lastTradePrice: mkt.lastTradePrice ? parseFloat(mkt.lastTradePrice) : null,
        spread: mkt.spread ? parseFloat(mkt.spread) : null,

        // volume/liquidity
        volume: parseFloat(event.volume || mkt.volume || 0),
        liquidity: parseFloat(event.liquidity || mkt.liquidity || 0),

        // metadata
        title: event.title,
        conditionId: mkt.conditionId,
        slug: event.slug,
        resolutionSource: event.resolutionSource,
        competitive: event.competitive,

        // timing
        timeLeftSec: Math.max(0, windowTs + 900 - now),
        progress: Math.min(100, ((now - windowTs) / 900) * 100),
      })
    }

    return NextResponse.json(markets)
  } catch (err) {
    console.error('failed to fetch markets:', err)
    return NextResponse.json([], { status: 500 })
  }
}
