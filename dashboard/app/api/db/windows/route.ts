import { NextResponse } from 'next/server'
import { saveWindow, getWindow, getRecentWindows, getAllWindows, getWindowStats, WindowRecord } from '@/app/lib/db'

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url)
  const coin = searchParams.get('coin')
  const windowTs = searchParams.get('windowTs')
  const action = searchParams.get('action') || 'list'

  try {
    if (action === 'stats') {
      const stats = getWindowStats(coin || undefined)
      return NextResponse.json(stats)
    }

    if (action === 'get' && coin && windowTs) {
      const window = getWindow(coin, parseInt(windowTs))
      return NextResponse.json(window)
    }

    if (coin) {
      const windows = getRecentWindows(coin, 20)
      return NextResponse.json(windows)
    }

    const windows = getAllWindows(50)
    return NextResponse.json(windows)
  } catch (err) {
    console.error('db error:', err)
    return NextResponse.json({ error: 'db error' }, { status: 500 })
  }
}

export async function POST(request: Request) {
  try {
    const record: WindowRecord = await request.json()
    saveWindow(record)
    return NextResponse.json({ success: true })
  } catch (err) {
    console.error('db error:', err)
    return NextResponse.json({ error: 'db error' }, { status: 500 })
  }
}
