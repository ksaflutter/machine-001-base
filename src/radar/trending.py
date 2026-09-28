import json
import urllib.request
from datetime import datetime, timezone

URL = "https://api.geckoterminal.com/api/v2/networks/base/trending_pools"


def fetch():
    req = urllib.request.Request(
        URL,
        headers={
            "Accept": "application/json;version=20230302",
            "User-Agent": "machine-001-base/0.1",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def age_text(created):
    if not created:
        return "?"
    t = datetime.fromisoformat(created.replace("Z", "+00:00"))
    hours = (datetime.now(timezone.utc) - t).total_seconds() / 3600
    return f"{hours:.0f} jam" if hours < 48 else f"{hours / 24:.0f} hari"


def main():
    try:
        data = fetch()
    except Exception as e:
        raise SystemExit(f"Gagal mengambil data: {type(e).__name__}: {e}")

    for i, pool in enumerate(data.get("data", [])[:10], 1):
        a = pool.get("attributes", {})
        tx = (a.get("transactions") or {}).get("h24") or {}
        vol = (a.get("volume_usd") or {}).get("h24")
        chg = a.get("price_change_percentage") or {}
        print(f"{i}. {a.get('name')}")
        print(f"   Umur pool : {age_text(a.get('pool_created_at'))}")
        print(f"   Likuiditas: ${num(a.get('reserve_in_usd')):,.0f}")
        print(f"   Volume 24j: ${num(vol):,.0f}")
        print(f"   Harga     : 1j {chg.get('h1', '?')}% | 24j {chg.get('h24', '?')}%")
        print(f"   Transaksi : beli {tx.get('buys', '?')} | jual {tx.get('sells', '?')}")
        print()


main()
