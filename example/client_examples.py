#!/usr/bin/env python3
"""
examples/client_examples.py
──────────────────────────────────────────────────────────────────
Contoh penggunaan AIChatScraper API Server dengan berbagai klien.

Jalankan api_server.py terlebih dahulu, lalu jalankan file ini:
  python api_server.py
  python examples/client_examples.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# ─── 1. Native HTTP (requests / httpx) ───────────────────────────────────────

def example_requests_basic():
    """Contoh paling sederhana menggunakan library requests."""
    import requests

    print("\n" + "="*60)
    print("Example 1: requests – basic (non-streaming)")
    print("="*60)

    response = requests.post(
        "http://127.0.0.1:12345/v1/chat/completions",
        json={
            "model": "qwen",
            "messages": [
                {"role": "user", "content": "menapa IHSG menurun?"},
            ],
        },
        timeout=120,
    )
    response.raise_for_status()
    data = response.json()

    content = data["choices"][0]["message"]["content"]
    usage   = data["usage"]
    print(f"Response:\n{content[:400]}{'...' if len(content) > 400 else ''}")
    print(f"\nUsage: {usage}")

def check_server():
    """Periksa apakah server berjalan."""
    try:
        import requests
        r = requests.get("http://127.0.0.1:12345/health", timeout=5)
        r.raise_for_status()
        print("✅ Server is running:", r.json())
        return True
    except Exception as e:
        print(f"❌ Server not reachable: {e}")
        print("   Pastikan api_server.py sudah dijalankan.")
        return False


def list_models():
    """List available models dari server."""
    try:
        import requests
        r = requests.get("http://127.0.0.1:12345/v1/models", timeout=5)
        r.raise_for_status()
        models = r.json()["data"]
        print("\nAvailable models:")
        for m in models:
            print(f"  - {m['id']} (owned_by: {m['owned_by']})")
    except Exception as e:
        print(f"Failed to list models: {e}")

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    if not check_server():
        sys.exit(1)

    list_models()

    # Run examples (comment out any you don't want)
    example_requests_basic()

    print("\n✅ All examples completed!")


if __name__ == "__main__":
    main()
