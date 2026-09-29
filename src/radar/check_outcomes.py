import json
import sqlite3
import urllib.request
from datetime import datetime, timezone

DB_PATH = "data/radar.db"


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def get_current_price(token_address):
    url = f"https://api.geckoterminal.com/api/v2/networks/base/tokens/{token_address}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json;version=20230302",
            "User-Agent": "machine-001-base/0.1",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.load(resp)
        price = data.get("data", {}).get("attributes", {}).get("price_usd")
        return num(price)
    except Exception as e:
        print(f"    gagal ambil harga: {type(e).__name__}: {e}")
        return None


def main():
    con = sqlite3.connect(DB_PATH)

    rows = con.execute(
        """SELECT id, token_address, pool_label, wallet, net_usd,
                  price_usd_at_detection, detected_at, is_new_holder
           FROM whale_moves
           WHERE price_checked_24h = 0
           AND price_usd_at_detection IS NOT NULL
           AND price_usd_at_detection > 0
           AND detected_at <= datetime('now', '-24 hours')
           ORDER BY detected_at ASC"""
    ).fetchall()

    print(f"Whale move yang siap dicek (umur >= 24 jam): {len(rows)}\n")

    if not rows:
        print("Belum ada yang siap dicek. Coba lagi nanti.")
        con.close()
        return

    price_cache = {}
    checked = 0
    up_count = 0
    down_count = 0

    for row_id, token_address, pool_label, wallet, net_usd, price_then, detected_at, is_new in rows:
        if token_address not in price_cache:
            print(f"Cek harga: {pool_label} ({token_address[:10]}...)")
            price_cache[token_address] = get_current_price(token_address)

        price_now = price_cache[token_address]
        if price_now is None or price_now <= 0:
            continue

        pct_change = ((price_now - price_then) / price_then) * 100

        con.execute(
            "UPDATE whale_moves SET price_checked_24h = 1, price_change_24h_pct = ?"
            " WHERE id = ?",
            (pct_change, row_id),
        )
        checked += 1
        if pct_change > 0:
            up_count += 1
        else:
            down_count += 1

        tag = "BARU" if is_new else "lama"
        arrow = "NAIK" if pct_change > 0 else "TURUN"
        print(f"  [{tag}] {pool_label}: {pct_change:+.1f}% ({arrow}) | whale saat itu ${net_usd:,.0f}")

    con.commit()
    con.close()

    print(f"\n=== RINGKASAN ===")
    print(f"Diperiksa: {checked} | Naik: {up_count} | Turun: {down_count}")
    if checked > 0:
        print(f"Persentase naik: {up_count / checked * 100:.1f}%")


main()
