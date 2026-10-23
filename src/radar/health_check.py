import json
import os
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone

DB_PATH = "data/radar.db"
MAX_STALE_HOURS = 10


def load_env():
    cfg = {k: os.environ.get(k) for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")}
    if os.path.exists(".env"):
        for line in open(".env"):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                if k in cfg and not cfg[k]:
                    cfg[k] = v.strip().strip('"').strip("'")
    return cfg


def send(cfg, text):
    if not cfg["TELEGRAM_BOT_TOKEN"] or not cfg["TELEGRAM_CHAT_ID"]:
        print("Lewati notifikasi: token/chat id belum diset")
        return
    payload = json.dumps({"chat_id": cfg["TELEGRAM_CHAT_ID"], "text": text}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{cfg['TELEGRAM_BOT_TOKEN']}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            json.load(resp)
        print("Notifikasi terkirim")
    except Exception as e:
        print(f"Gagal kirim Telegram: {type(e).__name__}: {e}")


def main():
    cfg = load_env()
    if "--test" in sys.argv:
        send(cfg, "Machine 001: tes pemantau kesehatan (abaikan)")
        return
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        row = con.execute("SELECT MAX(seen_at) FROM observations").fetchone()
        con.close()
        last = row[0] if row else None
    except Exception as e:
        send(cfg, f"Machine 001 ALERT: database tidak bisa dibaca ({type(e).__name__}). Cek tab Actions di GitHub.")
        return
    if not last:
        send(cfg, "Machine 001 ALERT: database kosong, belum ada catatan radar. Cek tab Actions di GitHub.")
        return
    dt = datetime.fromisoformat(last)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    age_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    print(f"Catatan terakhir: {last} ({age_h:.1f} jam lalu)")
    if age_h > MAX_STALE_HOURS:
        send(cfg, f"Machine 001 ALERT: radar tidak mencatat data baru selama {age_h:.0f} jam (terakhir {last}). Cek tab Actions di GitHub.")
    else:
        print("OK: radar sehat")


main()
