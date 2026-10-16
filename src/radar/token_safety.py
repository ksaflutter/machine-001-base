import html
import json
import sqlite3
import time
import urllib.request
from datetime import datetime, timezone

DB_PATH = "data/radar.db"
URL = "https://api.honeypot.is/v2/IsHoneypot?address={}&chainID=8453"
_cache = {}


def fetch_safety(token_address):
    req = urllib.request.Request(
        URL.format(token_address),
        headers={"User-Agent": "Mozilla/5.0 (compatible; machine-001)"},
    )
    for _ in range(2):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.load(resp)
        except Exception as e:
            print(f"Cek keamanan gagal ({type(e).__name__}): {e}")
            time.sleep(2)
    return None


def store(token_address, r):
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute(
            "CREATE TABLE IF NOT EXISTS token_safety (token_address TEXT,"
            " checked_at TEXT, ok INTEGER, risk TEXT, risk_level INTEGER,"
            " is_honeypot INTEGER, buy_tax REAL, sell_tax REAL,"
            " open_source INTEGER, flags TEXT)"
        )
        con.execute(
            "INSERT INTO token_safety VALUES (?,?,?,?,?,?,?,?,?,?)",
            (token_address, datetime.now(timezone.utc).isoformat(timespec="seconds"),
             int(r["ok"]), r["risk"], r["risk_level"], int(bool(r["is_honeypot"])),
             r["buy_tax"], r["sell_tax"], int(bool(r["open_source"])),
             json.dumps(r["flags"])),
        )
        con.commit()
        con.close()
    except Exception as e:
        print(f"Gagal simpan keamanan: {type(e).__name__}: {e}")


def check(token_address):
    if token_address in _cache:
        return _cache[token_address]
    d = fetch_safety(token_address)
    result = None
    if d:
        sim = d.get("simulationResult") or {}
        hp = d.get("honeypotResult") or {}
        summ = d.get("summary") or {}
        code = d.get("contractCode") or {}
        names = []
        for f in (summ.get("flags") or d.get("flags") or []):
            if isinstance(f, dict):
                names.append(str(f.get("flag") or f.get("description") or f))
            else:
                names.append(str(f))
        result = {
            "ok": bool(d.get("simulationSuccess")),
            "risk": summ.get("risk"),
            "risk_level": summ.get("riskLevel"),
            "is_honeypot": hp.get("isHoneypot"),
            "buy_tax": sim.get("buyTax"),
            "sell_tax": sim.get("sellTax"),
            "open_source": code.get("openSource"),
            "flags": names,
        }
        store(token_address, result)
    _cache[token_address] = result
    time.sleep(1)
    return result


def _pct(v):
    return f"{v:.1f}" if isinstance(v, (int, float)) else "?"


def safety_line(token_address):
    try:
        if not token_address:
            return "keamanan: alamat tidak ada"
        r = check(token_address)
        if not r:
            return "keamanan: tidak bisa dicek"
        if not r["ok"]:
            return "keamanan: simulasi beli/jual GAGAL (belum bisa dipastikan, waspada)"
        hp = "YA!" if r["is_honeypot"] else "tidak"
        src = "ya" if r["open_source"] else "TIDAK"
        line = (f"keamanan: risiko {html.escape(str(r['risk']))} | honeypot: {hp}"
                f" | pajak beli {_pct(r['buy_tax'])}% jual {_pct(r['sell_tax'])}%"
                f" | open source: {src}")
        if r["flags"]:
            line += " | flag: " + html.escape(", ".join(r["flags"][:3]))
        return line
    except Exception as e:
        return f"keamanan: error {type(e).__name__}"
