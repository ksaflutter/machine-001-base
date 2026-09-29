import json
import os
import sqlite3
import urllib.request
from datetime import datetime, timezone

URL = "https://api.geckoterminal.com/api/v2/networks/base/trending_pools"
DB_PATH = "data/radar.db"

MIN_AGE_DAYS = 7
MIN_LIQUIDITY = 100_000
MAX_VOL_LIQ_RATIO = 5
MAX_DROP_24H = -30


def load_config():
    config = {}
    if os.path.exists(".env"):
        with open(".env") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    config[k] = v
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        if os.environ.get(key):
            config[key] = os.environ[key]
    return config


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
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
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
    con.execute(
        """CREATE TABLE IF NOT EXISTS notified_pools (
            pool_address TEXT PRIMARY KEY,
            name TEXT,
            first_notified_at TEXT
        )"""
    )
    return con


def send_telegram(config, text):
    token = config.get("TELEGRAM_BOT_TOKEN")
    chat_id = config.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Lewati notifikasi: TELEGRAM_BOT_TOKEN/CHAT_ID belum diset")
        return
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            json.load(resp)
    except Exception as e:
        print(f"Gagal kirim Telegram: {type(e).__name__}: {e}")


def main():
    config = load_config()
    try:
        data = fetch()
    except Exception as e:
        raise SystemExit(f"Gagal mengambil data: {type(e).__name__}: {e}")

    con = init_db()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    passed = 0
    new_passes = []

    already_notified = {
        row[0] for row in con.execute("SELECT pool_address FROM notified_pools")
    }

    for pool in data.get("data", []):
        a = pool.get("attributes", {})
        chg = a.get("price_change_percentage") or {}
        age = age_days(a.get("pool_created_at"))
        liq = num(a.get("reserve_in_usd"))
        vol = num((a.get("volume_usd") or {}).get("h24"))
        c1 = num(chg.get("h1"))
        c24 = num(chg.get("h24"))
        price = num(a.get("base_token_price_usd"))
        address = a.get("address")
        name = a.get("name")
        decision, reasons = evaluate(age, liq, vol, c24)

        con.execute(
            "INSERT INTO observations (seen_at, pool_address, name, age_days,"
            " liquidity_usd, volume_24h, price_usd, change_1h, change_24h,"
            " decision, reasons) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (now, address, name, age, liq, vol, price, c1, c24, decision,
             "; ".join(reasons)),
        )

        if decision == "LOLOS":
            passed += 1
            if address not in already_notified:
                new_passes.append((address, name, liq, vol, c24))

        print(f"[{decision}] {name}")
        print(f"    {'; '.join(reasons)}")

    for address, name, liq, vol, c24 in new_passes:
        con.execute(
            "INSERT OR IGNORE INTO notified_pools (pool_address, name,"
            " first_notified_at) VALUES (?,?,?)",
            (address, name, now),
        )

    con.commit()
    total = con.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    con.close()

    print()
    print(f"Lolos: {passed} dari {len(data.get('data', []))} | Total catatan: {total}")
    print(f"Baru (belum pernah dinotifikasi): {len(new_passes)}")

    if new_passes:
        lines = ["Machine 001 Radar: token baru lolos filter\n"]
        for _, name, liq, vol, c24 in new_passes:
            lines.append(
                f"- {name}\n  Likuiditas ${liq:,.0f} | Volume24j ${vol:,.0f} | 24j {c24:.1f}%"
            )
        send_telegram(config, "\n".join(lines))


main()
