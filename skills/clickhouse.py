#!/usr/bin/env python3
"""
clickhouse query utility
usage: python skills/clickhouse.py "SELECT count(*) FROM clob_events"

SCHEMA:
-------
clob_events: websocket events from polymarket CLOB
  - ts DateTime64(3)
  - window_ts UInt32
  - event_type LowCardinality(String)  # 'price_change', 'last_trade_price', 'book'
  - asset_id String                     # token id
  - market String
  - raw String                          # full JSON payload

rtds_events: chainlink price feed
  - ts DateTime64(3)
  - window_ts UInt32
  - topic LowCardinality(String)        # 'crypto_prices_chainlink'
  - symbol LowCardinality(String)       # 'BTC', 'ETH', etc
  - raw String

gamma_events: gamma api /events responses
  - ts DateTime64(3)
  - raw String

gamma_markets: gamma api /markets?slug= responses
  - ts DateTime64(3)
  - window_ts UInt32
  - coin LowCardinality(String)
  - slug String
  - raw String

crypto_prices: resolution data from polymarket crypto-price api
  - ts DateTime64(3)
  - window_ts UInt32
  - coin LowCardinality(String)
  - raw String                          # contains openPrice, closePrice

token_registry: token ID -> coin/side mapping
  - window_ts UInt32
  - coin LowCardinality(String)
  - side LowCardinality(String)         # 'up' or 'down'
  - token_id String
  - condition_id String
  - slug String
  - created_at DateTime64(3)
"""
import os
import sys
import clickhouse_connect

CH_HOST = os.getenv("CLICKHOUSE_HOST", "n60fu3ciqd.eastus2.azure.clickhouse.cloud")
CH_USER = os.getenv("CLICKHOUSE_USER", "default")
CH_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD")

def get_client():
    if not CH_PASSWORD:
        raise ValueError("CLICKHOUSE_PASSWORD not set")

    return clickhouse_connect.get_client(
        host=CH_HOST,
        port=8443,
        username=CH_USER,
        password=CH_PASSWORD,
        secure=True
    )

def query(sql: str, limit: int = 100):
    """run a query and return results"""
    client = get_client()
    result = client.query(sql)
    return result.result_rows, result.column_names

def query_and_print(sql: str, limit: int = 100):
    """run query and print formatted results"""
    rows, columns = query(sql)

    print(f"\n{'='*60}")
    print(f"Query: {sql[:100]}{'...' if len(sql) > 100 else ''}")
    print(f"Rows: {len(rows)}")
    print(f"{'='*60}\n")

    # print column headers
    print(" | ".join(str(c) for c in columns))
    print("-" * 60)

    # print rows
    for row in rows[:limit]:
        print(" | ".join(str(v) for v in row))

    if len(rows) > limit:
        print(f"... ({len(rows) - limit} more rows)")

    return rows, columns

def show_tables():
    """show all tables"""
    return query_and_print("SHOW TABLES")

def show_schema(table: str):
    """show table schema"""
    return query_and_print(f"DESCRIBE {table}")

def count(table: str):
    """count rows in table"""
    return query_and_print(f"SELECT count(*) as cnt FROM {table}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python skills/clickhouse.py <query>")
        print("       python skills/clickhouse.py tables")
        print("       python skills/clickhouse.py schema <table>")
        print("       python skills/clickhouse.py count <table>")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "tables":
        show_tables()
    elif cmd == "schema" and len(sys.argv) > 2:
        show_schema(sys.argv[2])
    elif cmd == "count" and len(sys.argv) > 2:
        count(sys.argv[2])
    else:
        sql = " ".join(sys.argv[1:])
        query_and_print(sql)
