import { NextResponse } from 'next/server'
import { saveEdgeSnapshot, getEdgeSnapshots, getRecentEdgeSnapshots, EdgeSnapshot } from '@/app/lib/db'

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url)
  const coin = searchParams.get('coin')
  const windowTs = searchParams.get('windowTs')

  try {
    if (coin && windowTs) {
      const snapshots = getEdgeSnapshots(coin, parseInt(windowTs))
      return NextResponse.json(snapshots)
    }

    if (coin) {
      const snapshots = getRecentEdgeSnapshots(coin, 900)
      return NextResponse.json(snapshots)
    }

    return NextResponse.json([])
  } catch (err) {
    console.error('db error:', err)
    return NextResponse.json({ error: 'db error' }, { status: 500 })
  }
}

export async function POST(request: Request) {
  try {
    const snapshot: EdgeSnapshot = await request.json()
    saveEdgeSnapshot(snapshot)
    return NextResponse.json({ success: true })
  } catch (err) {
    console.error('db error:', err)
    return NextResponse.json({ error: 'db error' }, { status: 500 })
  }
}
