import json
import math
import sqlite3
import urllib.request
from datetime import datetime, timezone

DB_PATH = "data/radar.db"
_rate = {"v": None, "done": False}


def get_idr_rate():
    if _rate["done"]:
        return _rate["v"]
    _rate["done"] = True
    sources = [
        ("https://api.frankfurter.dev/v1/latest?base=USD&symbols=IDR", lambda d: d["rates"]["IDR"]),
        ("https://open.er-api.com/v6/latest/USD", lambda d: d["rates"]["IDR"]),
    ]
    for url, pick in sources:
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (compatible; machine-001)"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                v = float(pick(json.load(resp)))
            if v > 1000:
                _rate["v"] = v
                return v
        except Exception as e:
            print(f"Kurs gagal ({type(e).__name__}): {e}")
    return None


def usd_str(p):
    if p >= 1:
        return f"${p:,.2f}"
    d = min(12, 3 - math.floor(math.log10(p)))
    return f"${p:.{d}f}"


def _id_sep(s):
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def idr_str(x):
    if x >= 100:
        s = f"{x:,.0f}"
    elif x >= 1:
        s = f"{x:,.2f}"
    else:
        d = min(12, 3 - math.floor(math.log10(x)))
        s = f"{x:.{d}f}"
    return "Rp " + _id_sep(s)


def latest_price(token_address):
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        row = con.execute(
            "SELECT price_usd, seen_at FROM observations WHERE token_address=?"
            " AND price_usd IS NOT NULL AND price_usd>0"
            " ORDER BY seen_at DESC LIMIT 1",
            (token_address,),
        ).fetchone()
        con.close()
        return row
    except Exception:
        return None


def price_line(token_address):
    try:
        row = latest_price(token_address) if token_address else None
        if not row:
            return "harga: tidak tersedia"
        p, seen = row
        s = f"harga: {usd_str(p)}"
        rate = get_idr_rate()
        if rate:
            s += f" (≈ {idr_str(p * rate)})"
        try:
            age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(seen)).total_seconds() / 3600
            if age_h > 2:
                s += f" [harga lama {age_h:.0f} jam]"
        except Exception:
            pass
        return s
    except Exception as e:
        return f"harga: error {type(e).__name__}"


def price_html(p):
    try:
        if not p or p <= 0:
            return "-"
        s = usd_str(p)
        rate = get_idr_rate()
        if rate:
            s += "<br>" + idr_str(p * rate)
        return s
    except Exception:
        return "-"
