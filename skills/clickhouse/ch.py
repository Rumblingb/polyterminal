#!/usr/bin/env python3
"""
clickhouse client - base module for querying
"""
import os
import clickhouse_connect

CH_HOST = os.getenv("CLICKHOUSE_HOST", "n60fu3ciqd.eastus2.azure.clickhouse.cloud")
CH_USER = os.getenv("CLICKHOUSE_USER", "default")
CH_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD")

_client = None

def get_client():
    global _client
    if _client is None:
        if not CH_PASSWORD:
            raise ValueError("CLICKHOUSE_PASSWORD not set")
        _client = clickhouse_connect.get_client(
            host=CH_HOST, port=8443, username=CH_USER,
            password=CH_PASSWORD, secure=True
        )
    return _client

def query(sql: str):
    """run query, return (rows, columns)"""
    result = get_client().query(sql)
    return result.result_rows, result.column_names

def query_df(sql: str):
    """run query, return pandas dataframe"""
    import pandas as pd
    rows, cols = query(sql)
    return pd.DataFrame(rows, columns=cols)
