#!/usr/bin/env python3
"""
status - check collector health and data stats

usage:
    python skills/status.py
"""
import json
from datetime import datetime

from ch import query

def get_status():
    """show overall data status"""
    # table counts
    counts, _ = query('''
    SELECT 'clob_events' as tbl, count(*) FROM clob_events
    UNION ALL SELECT 'rtds_events', count(*) FROM rtds_events
    UNION ALL SELECT 'token_registry', count(*) FROM token_registry
    UNION ALL SELECT 'crypto_prices', count(*) FROM crypto_prices
    ''')

    print('Table Counts')
    print('-' * 30)
    for tbl, cnt in counts:
        print(f'{tbl:<20} {cnt:>10,}')
    print()

    # window stats
    stats, _ = query('''
    SELECT
        count(DISTINCT window_ts) as windows,
        min(window_ts) as first,
        max(window_ts) as last
    FROM clob_events
    WHERE window_ts > 0
    ''')

    windows, first, last = stats[0]
    first_dt = datetime.utcfromtimestamp(first).strftime('%m/%d %H:%M UTC')
    last_dt = datetime.utcfromtimestamp(last).strftime('%m/%d %H:%M UTC')

    print('Collection Stats')
    print('-' * 30)
    print(f'Windows collected:  {windows}')
    print(f'First window:       {first_dt}')
    print(f'Last window:        {last_dt}')
    print()

    # check for gaps
    gaps, _ = query('''
    SELECT
        window_ts,
        window_ts - lagInFrame(window_ts, 1) OVER (ORDER BY window_ts) as gap
    FROM (SELECT DISTINCT window_ts FROM clob_events WHERE window_ts > 0)
    ORDER BY window_ts
    ''')

    big_gaps = [(ts, gap) for ts, gap in gaps if gap and gap > 900]
    if big_gaps:
        print(f'Gaps detected: {len(big_gaps)}')
        for ts, gap in big_gaps[-5:]:
            dt = datetime.utcfromtimestamp(ts).strftime('%m/%d %H:%M')
            hours = gap / 3600
            print(f'  {dt}: {hours:.1f}h gap')
        print()

    # recent windows
    recent, _ = query('''
    SELECT
        window_ts,
        count(*) as events,
        countIf(event_type='last_trade_price') as trades
    FROM clob_events
    WHERE window_ts > 0
    GROUP BY window_ts
    ORDER BY window_ts DESC
    LIMIT 5
    ''')

    print('Recent Windows')
    print('-' * 40)
    print(f'{"Window":<20} {"Events":>10} {"Trades":>8}')
    for window_ts, events, trades in recent:
        dt = datetime.utcfromtimestamp(window_ts).strftime('%m/%d %H:%M UTC')
        print(f'{dt:<20} {events:>10,} {trades:>8,}')
    print()

    # check latest window health
    latest_ts = recent[0][0]
    latest_events = recent[0][1]
    avg_events = sum(e for _, e, _ in recent) / len(recent)

    if latest_events < avg_events * 0.5:
        print(f'WARNING: Latest window has {latest_events:,} events (avg: {avg_events:,.0f})')
        print('         May indicate partial collection or connection issues')

if __name__ == '__main__':
    get_status()
