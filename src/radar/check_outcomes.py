import json
import sqlite3
import time
import urllib.request
from datetime import datetime, timezone

DB_PATH = "data/radar.db"
REQUEST_DELAY_SECONDS = 3.0
MAX_RETRIES = 3

WINDOWS = [
    {"label": "24h", "hours": 24, "checked_col": "price_checked_24h", "pct_col": "price_change_24h_pct"},
    {"label": "3d", "hours": 72, "checked_col": "price_checked_3d", "pct_col": "price_change_3d_pct"},
    {"label": "7d", "hours": 168, "checked_col": "price_checked_7d", "pct_col": "price_change_7d_pct"},
]


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def ensure_columns(con):
    cols = [r[1] for r in con.execute("PRAGMA table_info(whale_moves)")]
    for w in WINDOWS:
        if w["checked_col"] not in cols:
            con.execute(f"ALTER TABLE whale_moves ADD COLUMN {w['checked_col']} INTEGER DEFAULT 0")
        if w["pct_col"] not in cols:
            con.execute(f"ALTER TABLE whale_moves ADD COLUMN {w['pct_col']} REAL")
    con.commit()


def get_current_price(token_address):
    url = f"https://api.geckoterminal.com/api/v2/networks/base/tokens/{token_address}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json;version=20230302",
            "User-Agent": "machine-001-base/0.1",
        },
    )
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.load(resp)
            price = data.get("data", {}).get("attributes", {}).get("price_usd")
            return num(price)
        except Exception as e:
            is_last = attempt == MAX_RETRIES - 1
            if "429" in str(e) and not is_last:
                wait = 10 * (attempt + 1)
                print(f"    kena limit, tunggu {wait} detik lalu coba lagi...")
                time.sleep(wait)
                continue
            print(f"    gagal ambil harga: {type(e).__name__}: {e}")
            return None
    return None


def process_window(con, window, price_cache):
    rows = con.execute(
        f"""SELECT id, token_address, pool_label, wallet, net_usd,
                   price_usd_at_detection, detected_at, is_new_holder
            FROM whale_moves
            WHERE {window['checked_col']} = 0
            AND price_usd_at_detection IS NOT NULL
            AND price_usd_at_detection > 0
            AND detected_at <= datetime('now', '-{window['hours']} hours')
            ORDER BY detected_at ASC"""
    ).fetchall()

    print(f"\n--- Jendela {window['label']}: {len(rows)} siap dicek ---")
    if not rows:
        return 0, 0, 0

    checked = up = down = 0

    for row_id, token_address, pool_label, wallet, net_usd, price_then, detected_at, is_new in rows:
        if token_address not in price_cache:
            print(f"Cek harga: {pool_label} ({token_address[:10]}...)")
            price_cache[token_address] = get_current_price(token_address)
            time.sleep(REQUEST_DELAY_SECONDS)

        price_now = price_cache[token_address]
        if price_now is None or price_now <= 0:
            continue

        pct_change = ((price_now - price_then) / price_then) * 100

        con.execute(
            f"UPDATE whale_moves SET {window['checked_col']} = 1, {window['pct_col']} = ?"
            " WHERE id = ?",
            (pct_change, row_id),
        )
        checked += 1
        if pct_change > 0:
            up += 1
        else:
            down += 1

    con.commit()
    print(f"  [{window['label']}] Diperiksa: {checked} | Naik: {up} | Turun: {down}")
    if checked > 0:
        print(f"  [{window['label']}] Persentase naik: {up / checked * 100:.1f}%")
    return checked, up, down


def main():
    con = sqlite3.connect(DB_PATH)
    ensure_columns(con)

    price_cache = {}
    totals = {}

    for window in WINDOWS:
        checked, up, down = process_window(con, window, price_cache)
        totals[window["label"]] = (checked, up, down)

    con.close()

    print(f"\n=== RINGKASAN SEMUA JENDELA ===")
    for label, (checked, up, down) in totals.items():
        if checked > 0:
            print(f"{label}: {checked} diperiksa | {up} naik ({up/checked*100:.1f}%) | {down} turun")
        else:
            print(f"{label}: tidak ada yang diperiksa kali ini")


main()
