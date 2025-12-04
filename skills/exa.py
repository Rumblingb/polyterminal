#!/usr/bin/env python3
"""
exa search utility
usage: python scripts/exa.py "polymarket api rate limits"
"""
import os
import sys
import json
import httpx
from typing import Optional

EXA_API_KEY = os.getenv("EXA_API_KEY")
BASE_URL = "https://api.exa.ai"

def search(
    query: str,
    num_results: int = 10,
    type: str = "auto",
    category: Optional[str] = None,
    include_domains: Optional[list] = None,
    text: bool = True,
    highlights: bool = False,
) -> dict:
    """search exa and return results"""
    if not EXA_API_KEY:
        raise ValueError("EXA_API_KEY not set")

    payload = {
        "query": query,
        "numResults": num_results,
        "type": type,
        "contents": {
            "text": {"maxCharacters": 2000} if text else False,
            "highlights": {"numSentences": 2, "highlightsPerUrl": 3} if highlights else None,
        }
    }

    if category:
        payload["category"] = category
    if include_domains:
        payload["includeDomains"] = include_domains

    # clean up None values
    payload["contents"] = {k: v for k, v in payload["contents"].items() if v is not None}

    resp = httpx.post(
        f"{BASE_URL}/search",
        headers={"x-api-key": EXA_API_KEY, "Content-Type": "application/json"},
        json=payload,
        timeout=30.0
    )
    resp.raise_for_status()
    return resp.json()

def search_and_print(query: str, **kwargs):
    """search and print formatted results"""
    results = search(query, **kwargs)

    print(f"\n{'='*60}")
    print(f"Query: {query}")
    print(f"Results: {len(results.get('results', []))}")
    print(f"{'='*60}\n")

    for i, r in enumerate(results.get("results", []), 1):
        print(f"[{i}] {r.get('title', 'No title')}")
        print(f"    URL: {r.get('url', '')}")
        if r.get("publishedDate"):
            print(f"    Date: {r['publishedDate'][:10]}")
        if r.get("text"):
            text = r["text"][:500].replace("\n", " ")
            print(f"    {text}...")
        print()

    return results

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python scripts/exa.py <query>")
        sys.exit(1)

    query = " ".join(sys.argv[1:])
    search_and_print(query)
