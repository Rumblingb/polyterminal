#!/usr/bin/env python3
import urllib.request
import json
import time
import concurrent.futures
from datetime import datetime
import sys
import os

GOLDSKY = "https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/orderbook-subgraph/0.0.1/gn"
WALLET = "0x6031b6eed1c97e853c6e0f03ad3ce3529351f96d"
seven_days_ago = int(time.time()) - 7 * 86400
OUT_FILE = '/Users/ishan/Desktop/polyterminal/data/gabagool_trades.json'

HEADERS = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0", "Accept": "application/json", "Origin": "https://polymarket.com", "Referer": "https://polymarket.com/"}

CONCURRENT = 10
BATCH = 1000

def query(skip, role):
    q = f'''{{ orderFilledEvents(where: {{ {role}: "{WALLET}", timestamp_gte: "{seven_days_ago}" }} first: 1000 skip: {skip} orderBy: timestamp orderDirection: desc) {{ id timestamp makerAmountFilled takerAmountFilled makerAssetId takerAssetId fee maker }} }}'''
    for attempt in range(5):
        try:
            req = urllib.request.Request(GOLDSKY, data=json.dumps({"query": q}).encode(), headers=HEADERS)
            r = json.loads(urllib.request.urlopen(req, timeout=30).read())
            return r.get('data', {}).get('orderFilledEvents', [])
        except Exception as e:
            wait = 2 ** attempt
            time.sleep(wait)
    return []

def decode(e):
    is_maker = e['maker'].lower() == WALLET.lower()
    ma, ta = int(e['makerAmountFilled']) / 1e6, int(e['takerAmountFilled']) / 1e6
    if is_maker:
        if e['makerAssetId'] == '0':
            return {'id': e['id'], 'ts': int(e['timestamp']), 'side': 'BUY', 'token': e['takerAssetId'][:16], 'shares': ta, 'usdc': ma, 'price': ma/ta if ta else 0, 'fee': int(e['fee'])/1e6, 'is_maker': True}
        return {'id': e['id'], 'ts': int(e['timestamp']), 'side': 'SELL', 'token': e['makerAssetId'][:16], 'shares': ma, 'usdc': ta, 'price': ta/ma if ma else 0, 'fee': int(e['fee'])/1e6, 'is_maker': True}
    if e['takerAssetId'] == '0':
        return {'id': e['id'], 'ts': int(e['timestamp']), 'side': 'BUY', 'token': e['makerAssetId'][:16], 'shares': ma, 'usdc': ta, 'price': ta/ma if ma else 0, 'fee': int(e['fee'])/1e6, 'is_maker': False}
    return {'id': e['id'], 'ts': int(e['timestamp']), 'side': 'SELL', 'token': e['takerAssetId'][:16], 'shares': ta, 'usdc': ma, 'price': ma/ta if ta else 0, 'fee': int(e['fee'])/1e6, 'is_maker': False}

if __name__ == '__main__':
    start = time.time()

    # load existing
    all_trades = []
    seen = set()
    if os.path.exists(OUT_FILE):
        with open(OUT_FILE) as f:
            all_trades = json.load(f)
            seen = {t['id'] for t in all_trades}
        print(f"Resuming from {len(all_trades)} existing trades")

    for role, max_skip in [('maker', 620000), ('taker', 160000)]:
        print(f"\n=== {role.upper()} ===")
        skip = 0
        wave = 0
        consecutive_empty = 0

        while skip < max_skip and consecutive_empty < 3:
            wave += 1
            skips = list(range(skip, min(skip + CONCURRENT * BATCH, max_skip), BATCH))

            with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENT) as ex:
                futures = {ex.submit(query, s, role): s for s in skips}
                results = {}
                for f in concurrent.futures.as_completed(futures):
                    results[futures[f]] = f.result()

            wave_new = 0
            for s in sorted(results.keys()):
                for e in results[s]:
                    if e['id'] not in seen:
                        seen.add(e['id'])
                        all_trades.append(decode(e))
                        wave_new += 1

            if wave_new == 0:
                consecutive_empty += 1
            else:
                consecutive_empty = 0

            all_trades.sort(key=lambda x: x['ts'])
            with open(OUT_FILE, 'w') as f:
                json.dump(all_trades, f)

            elapsed = time.time() - start
            print(f"  wave {wave}: skip {skip}-{skip+len(skips)*BATCH} | +{wave_new} | total: {len(all_trades)} | {elapsed:.0f}s")
            sys.stdout.flush()

            skip += len(skips) * BATCH
            time.sleep(0.5)

    elapsed = time.time() - start
    print(f"\n=== DONE in {elapsed:.0f}s ===")
    print(f"Total: {len(all_trades)}")

    if all_trades:
        times = [t['ts'] for t in all_trades]
        print(f"Range: {datetime.fromtimestamp(min(times))} to {datetime.fromtimestamp(max(times))}")
        print(f"Span: {(max(times)-min(times))/86400:.2f} days")
        buys = sum(1 for t in all_trades if t['side'] == 'BUY')
        print(f"BUY: {buys}, SELL: {len(all_trades)-buys}")
        makers = sum(1 for t in all_trades if t['is_maker'])
        print(f"Maker: {makers}, Taker: {len(all_trades)-makers}")
