import json
import sqlite3
import urllib.request
from datetime import datetime, timezone

URL = "https://api.geckoterminal.com/api/v2/networks/base/trending_pools"
DB_PATH = "data/radar.db"

MIN_AGE_DAYS = 7
MIN_LIQUIDITY = 100_000
MAX_VOL_LIQ_RATIO = 5
MAX_DROP_24H = -30


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


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


def age_days(created):
    if not created:
        return 0.0
    t = datetime.fromisoformat(created.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - t).total_seconds() / 86400


def evaluate(age, liq, vol, chg24):
    reasons = []
    if age < MIN_AGE_DAYS:
        reasons.append(f"pool terlalu baru ({age:.1f} hari)")
    if liq < MIN_LIQUIDITY:
        reasons.append(f"likuiditas kecil (${liq:,.0f})")
    if liq > 0 and vol / liq > MAX_VOL_LIQ_RATIO:
        reasons.append(f"volume/likuiditas janggal ({vol / liq:.1f}x)")
    if chg24 < MAX_DROP_24H:
        reasons.append(f"turun tajam 24j ({chg24:.1f}%)")
    return ("TOLAK", reasons) if reasons else ("LOLOS", ["lolos semua filter dasar"])


def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute(
        """CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seen_at TEXT,
            pool_address TEXT,
            name TEXT,
            age_days REAL,
            liquidity_usd REAL,
            volume_24h REAL,
            price_usd REAL,
            change_1h REAL,
            change_24h REAL,
            decision TEXT,
            reasons TEXT
        )"""
    )
    return con


def main():
    try:
        data = fetch()
    except Exception as e:
        raise SystemExit(f"Gagal mengambil data: {type(e).__name__}: {e}")

    con = init_db()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    passed = 0

    for pool in data.get("data", []):
        a = pool.get("attributes", {})
        chg = a.get("price_change_percentage") or {}
        age = age_days(a.get("pool_created_at"))
        liq = num(a.get("reserve_in_usd"))
        vol = num((a.get("volume_usd") or {}).get("h24"))
        c1 = num(chg.get("h1"))
        c24 = num(chg.get("h24"))
        price = num(a.get("base_token_price_usd"))
        decision, reasons = evaluate(age, liq, vol, c24)

        con.execute(
            "INSERT INTO observations (seen_at, pool_address, name, age_days,"
            " liquidity_usd, volume_24h, price_usd, change_1h, change_24h,"
            " decision, reasons) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (now, a.get("address"), a.get("name"), age, liq, vol, price,
             c1, c24, decision, "; ".join(reasons)),
        )
        if decision == "LOLOS":
            passed += 1
        print(f"[{decision}] {a.get('name')}")
        print(f"    {'; '.join(reasons)}")

    con.commit()
    total = con.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    con.close()
    print()
    print(f"Lolos: {passed} dari {len(data.get('data', []))} | Total catatan di database: {total}")


main()
