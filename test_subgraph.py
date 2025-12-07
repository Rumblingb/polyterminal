#!/usr/bin/env python3
"""test subgraph query"""
import requests
import json

SUBGRAPH = 'https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/orderbook-subgraph/0.0.1/gn'
WALLET = '0x6031b6eed1c97e853c6e0f03ad3ce3529351f96d'

query = '''
{
  orderFilledEvents(
    where: { maker: "%s" }
    first: 10
    orderBy: timestamp
    orderDirection: desc
  ) {
    id
    timestamp
    makerAmountFilled
    takerAmountFilled
  }
}
''' % WALLET

print('Testing subgraph...')
print(f'Wallet: {WALLET}')

try:
    resp = requests.post(SUBGRAPH, json={'query': query}, timeout=120)
    print(f'Status: {resp.status_code}')
    data = resp.json()

    if 'errors' in data:
        print(f'Errors: {data["errors"]}')

    events = data.get('data', {}).get('orderFilledEvents', [])
    print(f'Events: {len(events)}')

    for e in events[:5]:
        print(f'  {e}')

except Exception as e:
    print(f'Error: {e}')
