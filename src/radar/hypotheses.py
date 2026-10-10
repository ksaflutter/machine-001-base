import json
import math
import sqlite3
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

DB_PATH = "data/radar.db"

TAKE_PROFIT_PCT = 15.0
STOP_LOSS_PCT = -10.0
MAX_HOLD_HOURS = 72
ROUND_TRIP_COST_PCT = 2.0

H1_MIN_NEW_WALLETS = 3
H2_MIN_FRESH_PCT = 30.0
H2_MIN_BUYERS = 10
H3_MIN_PRIOR_24H = -5.0
H3_MAX_PRIOR_24H = 10.0

MIN_INDEPENDENT = 30
MIN_T_STAT = 2.4
MIN_EDGE_VS_CONTROL = 1.0

REQUEST_DELAY = 3.0
MAX_RETRIES = 3


def parse_dt(s):
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def load_universe(con):
    rows = con.execute(
        """SELECT token_address, MAX(pool_label), detected_at,
                  COUNT(DISTINCT CASE WHEN is_new_holder = 1 THEN wallet END)
           FROM whale_moves
           WHERE token_address IS NOT NULL AND detected_at IS NOT NULL
           GROUP BY token_address, detected_at"""
    ).fetchall()

    fresh = {}
    has_table = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='token_buyer_stats'"
    ).fetchone()
    if has_table:
        for token, checked, buyers, pct in con.execute(
            "SELECT token_address, checked_at, buyers_checked, fresh_pct FROM token_buyer_stats"
        ):
            fresh[(token, checked)] = (buyers, pct)

    events = []
    for token, label, det, new_w in rows:
        buyers, pct = fresh.get((token, det), (None, None))
        events.append({
            "token": token, "label": label, "det": parse_dt(det),
            "new_w": new_w or 0, "buyers": buyers, "fresh": pct,
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


def simulate(candles, det_dt):
    det_ts = int(det_dt.timestamp())
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

    prior24 = None
    target = entry_ts - 24 * 3600
    for ts, o, h, l, c in reversed(candles[:idx]):
        if ts <= target:
            if target - ts <= 6 * 3600 and c > 0:
                prior24 = (entry / c - 1) * 100
            break

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
    return {"entry_ts": entry_ts, "pnl": pnl, "reason": reason, "prior24": prior24}


def decorrelate(trades):
    out = []
    last = {}
    for t in sorted(trades, key=lambda x: x["entry_ts"]):
        prev = last.get(t["token"])
        if prev is None or t["entry_ts"] - prev >= 24 * 3600:
            out.append(t)
            last[t["token"]] = t["entry_ts"]
    return out


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def analyze(trades):
    ind = decorrelate(trades)
    n = len(ind)
    res = {"n": n, "avg": None, "t": None, "avg_rest": None, "h1": None,
           "h2": None, "win": 0, "tokens": 0, "best_name": ""}
    if n == 0:
        return res

    pnls = [t["pnl"] for t in ind]
    avg = sum(pnls) / n
    t_stat = None
    if n > 1:
        var = sum((p - avg) ** 2 for p in pnls) / (n - 1)
        if var > 0:
            t_stat = avg / math.sqrt(var / n)

    by_token = defaultdict(float)
    names = {}
    for t in ind:
        by_token[t["token"]] += t["pnl"]
        names[t["token"]] = t["label"]
    best = max(by_token, key=by_token.get)
    rest = [t["pnl"] for t in ind if t["token"] != best]

    ordered = sorted(ind, key=lambda x: x["entry_ts"])
    half = n // 2

    res.update({
        "avg": avg, "t": t_stat, "avg_rest": mean(rest),
        "h1": mean([t["pnl"] for t in ordered[:half]]),
        "h2": mean([t["pnl"] for t in ordered[half:]]),
        "win": sum(1 for p in pnls if p > 0) / n * 100,
        "tokens": len(by_token), "best_name": names[best],
    })
    return res


def fmt(x):
    return f"{x:+.1f}%" if x is not None else "n/a"


def show(name, a):
    print(f"\n{name}")
    if a["n"] == 0:
        print("  tidak ada trade")
        return
    t = f"{a['t']:.2f}" if a["t"] is not None else "n/a"
    print(f"  {a['n']} trade independen | {a['tokens']} token | menang {a['win']:.0f}% "
          f"| rata-rata {fmt(a['avg'])} | t-stat {t}")
    print(f"  tanpa token terbaik ({a['best_name'][:20]}): {fmt(a['avg_rest'])} "
          f"| paruh awal {fmt(a['h1'])} | paruh akhir {fmt(a['h2'])}")


def check(a, control_avg):
    edge = None
    if a["avg"] is not None and control_avg is not None:
        edge = a["avg"] - control_avg
    checks = [
        (f"trade independen >= {MIN_INDEPENDENT} (ada {a['n']})", a["n"] >= MIN_INDEPENDENT),
        ("rata-rata bersih > 0 setelah biaya", a["avg"] is not None and a["avg"] > 0),
        (f"t-stat >= {MIN_T_STAT}", a["t"] is not None and a["t"] >= MIN_T_STAT),
        ("tetap > 0 tanpa token terbaik", a["avg_rest"] is not None and a["avg_rest"] > 0),
        ("kedua paruh waktu > 0",
         a["h1"] is not None and a["h2"] is not None and a["h1"] > 0 and a["h2"] > 0),
        (f"unggul >= {MIN_EDGE_VS_CONTROL} poin dari kontrol",
         edge is not None and edge >= MIN_EDGE_VS_CONTROL),
    ]
    for text, ok in checks:
        print(f"  [{'LULUS' if ok else 'BELUM'}] {text}")
    return all(ok for _, ok in checks)


def main():
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    events = load_universe(con)
    if not events:
        print("Tidak ada data whale untuk diuji.")
        return

    tokens = sorted({e["token"] for e in events})
    pools = {}
    for t in tokens:
        p = best_pool(con, t)
        if p:
            pools[t] = p
    con.close()

    first = min(e["det"] for e in events)
    last = max(e["det"] for e in events)
    print(f"Sinyal unik (token + waktu pemindaian): {len(events)} | token: {len(tokens)}")
    print(f"Rentang: {first:%Y-%m-%d} s/d {last:%Y-%m-%d}\n")

    candles_by_token = {}
    for i, t in enumerate(tokens, 1):
        pool = pools.get(t)
        if not pool:
            continue
        print(f"[{i}/{len(tokens)}] ambil candle {t[:10]}...")
        candles_by_token[t] = fetch_candles(pool)
        time.sleep(REQUEST_DELAY)

    trades = []
    incomplete = 0
    nodata = 0
    for e in events:
        candles = candles_by_token.get(e["token"])
        if not candles:
            nodata += 1
            continue
        res = simulate(candles, e["det"])
        if res == "INCOMPLETE":
            incomplete += 1
            continue
        if res is None:
            nodata += 1
            continue
        res.update(e)
        trades.append(res)

    print(f"\nTersimulasi: {len(trades)} | data kurang: {nodata} | jendela 72 jam belum selesai: {incomplete}")
    print(f"Biaya {ROUND_TRIP_COST_PCT}% | TP +{TAKE_PROFIT_PCT}% | SL {STOP_LOSS_PCT}% | maks {MAX_HOLD_HOURS} jam")

    control = analyze(trades)
    show("KONTROL: semua sinyal whale", control)

    hypotheses = [
        (f"H1. Pembelian berkelompok (>= {H1_MIN_NEW_WALLETS} wallet baru di token itu, satu pemindaian)",
         [t for t in trades if t["new_w"] >= H1_MIN_NEW_WALLETS]),
        (f"H2. Fresh Buyer >= {H2_MIN_FRESH_PCT:.0f}% (min {H2_MIN_BUYERS} pembeli dicek)",
         [t for t in trades
          if t["buyers"] is not None and t["buyers"] >= H2_MIN_BUYERS
          and t["fresh"] is not None and t["fresh"] >= H2_MIN_FRESH_PCT]),
        (f"H3. Tidak mengejar (harga 24 jam sebelumnya {H3_MIN_PRIOR_24H:+.0f}% s/d {H3_MAX_PRIOR_24H:+.0f}%)",
         [t for t in trades
          if t["prior24"] is not None
          and H3_MIN_PRIOR_24H <= t["prior24"] <= H3_MAX_PRIOR_24H]),
    ]

    passed = []
    for name, subset in hypotheses:
        a = analyze(subset)
        show(name, a)
        if check(a, control["avg"]):
            passed.append(name.split(".")[0])

    print("\n=== KESIMPULAN ===")
    if passed:
        print(f"Lolos penyaringan: {', '.join(passed)}")
        print("Ini BELUM berarti boleh pakai uang asli. Lanjut ke uji ke depan 2 minggu dengan aturan beku.")
    else:
        print("Tidak ada hipotesis yang lolos penyaringan.")


main()
