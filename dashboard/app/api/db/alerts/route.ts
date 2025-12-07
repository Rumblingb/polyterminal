import { NextResponse } from 'next/server'
import { saveAlert, getRecentAlerts, ArbAlert } from '@/app/lib/db'

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url)
  const limit = parseInt(searchParams.get('limit') || '50')

  try {
    const alerts = getRecentAlerts(limit)
    return NextResponse.json(alerts)
  } catch (err) {
    console.error('db error:', err)
    return NextResponse.json({ error: 'db error' }, { status: 500 })
  }
}

export async function POST(request: Request) {
  try {
    const alert: ArbAlert = await request.json()
    saveAlert(alert)
    return NextResponse.json({ success: true })
  } catch (err) {
    console.error('db error:', err)
    return NextResponse.json({ error: 'db error' }, { status: 500 })
  }
}
