import json
import sqlite3
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

DB_PATH = "data/radar.db"

MIN_WHALE_USD = 2000
MAX_WHALE_USD = 10000
REQUIRED_AGE_TAG = "SANGAT BARU (<3 hari)"

TAKE_PROFIT_PCT = 15.0
STOP_LOSS_PCT = -10.0
MAX_HOLD_HOURS = 72
ROUND_TRIP_COST_PCT = 2.0
POSITION_SIZE = 5.0

REQUEST_DELAY = 3.0
MAX_RETRIES = 3


def parse_dt(s):
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def load_events(con):
    rows = con.execute(
        """SELECT id, token_address, pool_label, wallet, net_usd,
                  wallet_age_tag, detected_at
           FROM whale_moves
           WHERE token_address IS NOT NULL AND detected_at IS NOT NULL
           ORDER BY detected_at ASC"""
    ).fetchall()
    seen = set()
    events = []
    for _id, token, label, wallet, net_usd, age, det in rows:
        key = (token, wallet)
        if key in seen:
            continue
        seen.add(key)
        events.append({
            "token": token, "label": label, "wallet": wallet,
            "net_usd": net_usd, "age": age, "detected": parse_dt(det),
        })
    return events


def best_pool(con, token):
    row = con.execute(
        "SELECT pool_address FROM observations "
        "WHERE token_address = ? AND pool_address IS NOT NULL "
        "ORDER BY liquidity_usd DESC LIMIT 1",
        (token,),
    ).fetchone()
    return row[0] if row else None


def fetch_candles(pool):
    url = (
        f"https://api.geckoterminal.com/api/v2/networks/base/pools/{pool}"
        "/ohlcv/hour?aggregate=1&limit=1000&currency=usd"
    )
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json;version=20230302",
            "User-Agent": "machine-001-base/0.1",
        },
    )
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.load(resp)
            raw = data["data"]["attributes"]["ohlcv_list"]
            candles = [
                (int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]))
                for c in raw
            ]
            candles.sort()
            return candles
        except Exception as e:
            if "429" in str(e) and attempt < MAX_RETRIES - 1:
                time.sleep(10 * (attempt + 1))
                continue
            print(f"    gagal: {type(e).__name__}: {e}")
            return None
    return None


def simulate(candles, detected_dt):
    det_ts = int(detected_dt.timestamp())
    idx = None
    for i, c in enumerate(candles):
        if c[0] >= det_ts:
            idx = i
            break
    if idx is None:
        return None
    if candles[idx][0] - det_ts > 3 * 3600:
        return None

    entry_ts = candles[idx][0]
    entry = candles[idx][1]
    if entry <= 0:
        return None

    end_ts = entry_ts + MAX_HOLD_HOURS * 3600
    if end_ts > time.time():
        return "INCOMPLETE"

    tp = entry * (1 + TAKE_PROFIT_PCT / 100)
    sl = entry * (1 + STOP_LOSS_PCT / 100)

    exit_price = None
    reason = None
    last_close = entry
    for ts, o, h, l, c in candles[idx:]:
        if ts >= end_ts:
            break
        last_close = c
        if o <= sl:
            exit_price, reason = o, "SL"
            break
        if l <= sl:
            exit_price, reason = sl, "SL"
            break
        if o >= tp:
            exit_price, reason = o, "TP"
            break
        if h >= tp:
            exit_price, reason = tp, "TP"
            break
    if exit_price is None:
        exit_price, reason = last_close, "TIME"

    pnl = (exit_price / entry - 1) * 100 - ROUND_TRIP_COST_PCT
    return {"entry_ts": entry_ts, "pnl": pnl, "reason": reason}


def decorrelate(trades):
    out = []
    last = {}
    for t in sorted(trades, key=lambda x: x["entry_ts"]):
        prev = last.get(t["token"])
        if prev is None or t["entry_ts"] - prev >= 24 * 3600:
            out.append(t)
            last[t["token"]] = t["entry_ts"]
    return out


def stats(trades, label):
    n = len(trades)
    if n == 0:
        print(f"  {label}: tidak ada trade")
        return {"n": 0, "avg": None, "avg_rest": None}
    pnls = [t["pnl"] for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    avg = sum(pnls) / n
    total = sum(POSITION_SIZE * p / 100 for p in pnls)
    tokens = {t["token"] for t in trades}
    reasons = defaultdict(int)
    by_token = defaultdict(float)
    for t in trades:
        reasons[t["reason"]] += 1
        by_token[t["token"]] += t["pnl"]
    best = max(by_token, key=by_token.get)
    rest = [t["pnl"] for t in trades if t["token"] != best]
    avg_rest = sum(rest) / len(rest) if rest else None
    best_name = next(t["label"] for t in trades if t["token"] == best)
    rest_txt = f"{avg_rest:+.1f}%" if avg_rest is not None else "n/a"
    print(
        f"  {label}: {n} trade | {len(tokens)} token | menang {wins / n * 100:.0f}% "
        f"| rata-rata {avg:+.1f}% | total ${total:+.2f} (posisi $5)"
    )
    print(
        f"      keluar: TP {reasons['TP']} / SL {reasons['SL']} / waktu {reasons['TIME']} "
        f"| tanpa token terbaik ({best_name[:20]}): {rest_txt}"
    )
    return {"n": n, "avg": avg, "avg_rest": avg_rest}


def main():
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    events = load_events(con)
    if not events:
        print("Tidak ada whale move untuk di-backtest.")
        return

    tokens = sorted({e["token"] for e in events})
    pools = {}
    for t in tokens:
        p = best_pool(con, t)
        if p:
            pools[t] = p
    con.close()

    first = min(e["detected"] for e in events)
    last = max(e["detected"] for e in events)
    print(f"Event unik (token+wallet): {len(events)} | token: {len(tokens)}")
    print(f"Rentang deteksi: {first:%Y-%m-%d} s/d {last:%Y-%m-%d}\n")

    candles_by_token = {}
    for i, t in enumerate(tokens, 1):
        pool = pools.get(t)
        if not pool:
            continue
        print(f"[{i}/{len(tokens)}] ambil candle {t[:10]}...")
        candles_by_token[t] = fetch_candles(pool)
        time.sleep(REQUEST_DELAY)

    trades = []
    skipped_incomplete = 0
    skipped_nodata = 0
    for e in events:
        candles = candles_by_token.get(e["token"])
        if not candles:
            skipped_nodata += 1
            continue
        res = simulate(candles, e["detected"])
        if res == "INCOMPLETE":
            skipped_incomplete += 1
            continue
        if res is None:
            skipped_nodata += 1
            continue
        res.update(
            token=e["token"], label=e["label"], net_usd=e["net_usd"],
            age=e["age"], wallet=e["wallet"],
        )
        trades.append(res)

    print(f"\nTrade tersimulasi: {len(trades)} | dilewati (data kurang): {skipped_nodata} "
          f"| dilewati (jendela 72 jam belum selesai): {skipped_incomplete}")
    print(f"Biaya per trade: {ROUND_TRIP_COST_PCT}% | TP +{TAKE_PROFIT_PCT}% | "
          f"SL {STOP_LOSS_PCT}% | maks {MAX_HOLD_HOURS} jam")

    def in_size(t):
        return t["net_usd"] is not None and MIN_WHALE_USD <= t["net_usd"] <= MAX_WHALE_USD

    def is_new(t):
        return t["age"] == REQUIRED_AGE_TAG

    groups = [
        ("A. STRATEGI KETAT (whale $2rb-$10rb + wallet sangat baru)",
         lambda t: in_size(t) and is_new(t)),
        ("B. KONTROL: semua whale", lambda t: True),
        ("C. Hanya wallet sangat baru (semua ukuran)", is_new),
        ("D. Hanya whale $2rb-$10rb (semua umur)", in_size),
    ]

    results = {}
    for name, fn in groups:
        subset = [t for t in trades if fn(t)]
        print(f"\n{name}")
        stats(subset, "semua trade        ")
        results[name] = stats(decorrelate(subset), "1 posisi/token/24j ")

    a = results[groups[0][0]]
    b = results[groups[1][0]]
    print("\n=== KRITERIA LULUS (versi 1 posisi/token/24j) ===")
    checks = [
        (f"trade independen >= 30 (ada {a['n']})", a["n"] >= 30),
        ("rata-rata bersih > 0 setelah biaya",
         a["avg"] is not None and a["avg"] > 0),
        ("tetap > 0 tanpa token terbaik",
         a["avg_rest"] is not None and a["avg_rest"] > 0),
        ("unggul >= 1 poin persen dari kontrol",
         a["avg"] is not None and b["avg"] is not None and a["avg"] - b["avg"] >= 1.0),
    ]
    for text, ok in checks:
        print(f"  [{'LULUS' if ok else 'BELUM'}] {text}")


main()
