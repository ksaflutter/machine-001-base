"""Eksplorasi kualitas wallet (read-only).

H4 (dicatat 2026-10-17, sebelum data baru diuji):
Wallet yang rekam jejaknya (rata-rata perubahan harga 24 jam dari event yang
SUDAH selesai) > 0 akan punya perubahan harga 24 jam event berikutnya lebih
tinggi daripada wallet lain.
Sampel konfirmasi: event terdeteksi pada/sesudah 2026-10-17.
Lulus jika: n >= 10 per kelompok, p < 0.0125 (Bonferroni 4 hipotesis),
dan median kelompok positif > median kelompok lain.
Kriteria tidak boleh diubah setelah melihat hasil.
"""
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from statistics import mean, median

DB_PATH = "data/radar.db"
SPLIT = datetime(2026, 10, 17, tzinfo=timezone.utc)
MIN_GROUP = 10
PERMS = 5000
ALPHA = 0.0125


def parse_dt(s):
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def load_events():
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT wallet, token_address, detected_at, price_checked_24h,"
        " price_change_24h_pct FROM whale_moves"
        " WHERE wallet IS NOT NULL AND token_address IS NOT NULL"
        " AND detected_at IS NOT NULL ORDER BY detected_at ASC"
    ).fetchall()
    con.close()
    seen = set()
    events = []
    for w, t, d, chk, pct in rows:
        if (w, t) in seen:
            continue
        seen.add((w, t))
        resolved = bool(chk) and pct is not None
        events.append({"wallet": w, "token": t, "dt": parse_dt(d),
                       "pct": pct if resolved else None})
    return events


def build_records(events):
    hist = {}
    recs = []
    for e in events:
        h = hist.setdefault(e["wallet"], [])
        if e["pct"] is not None:
            prior = [x["pct"] for x in h if x["pct"] is not None
                     and x["dt"] + timedelta(hours=24) <= e["dt"]]
            if prior:
                recs.append((e["dt"], mean(prior), e["pct"]))
        h.append(e)
    return recs


def perm_p(a, b):
    obs = mean(a) - mean(b)
    pool = a + b
    na = len(a)
    rnd = random.Random(42)
    cnt = 0
    for _ in range(PERMS):
        rnd.shuffle(pool)
        if mean(pool[:na]) - mean(pool[na:]) >= obs:
            cnt += 1
    return (cnt + 1) / (PERMS + 1)


def report(title, recs):
    pos = [o for _, pa, o in recs if pa > 0]
    neg = [o for _, pa, o in recs if pa <= 0]
    print(f"\n{title}")
    for name, g in (("rekam jejak positif", pos), ("lainnya", neg)):
        if g:
            print(f"  {name}: n={len(g)} | rata-rata {mean(g):+.1f}% | median {median(g):+.1f}%")
        else:
            print(f"  {name}: n=0")
    if len(pos) < MIN_GROUP or len(neg) < MIN_GROUP:
        print(f"  -> belum cukup data (butuh >= {MIN_GROUP} per kelompok)")
        return
    p = perm_p(pos, neg)
    ok = p < ALPHA and median(pos) > median(neg)
    print(f"  p (permutasi, 1 arah) = {p:.4f} | ambang {ALPHA}")
    print(f"  -> {'LULUS' if ok else 'BELUM LULUS'}")


def wallet_table(events):
    by = {}
    for e in events:
        by.setdefault(e["wallet"], []).append(e)
    rows = []
    for w, es in by.items():
        res = [x["pct"] for x in es if x["pct"] is not None]
        if len(es) >= 2:
            rows.append((w, len(es), len(res), sum(1 for p in res if p > 0),
                         mean(res) if res else None))
    rows.sort(key=lambda r: (-r[1], -(r[4] if r[4] is not None else -999)))
    print(f"\nWallet dengan >= 2 event: {len(rows)} dari {len(by)} wallet unik")
    for w, n, nr, up, avg in rows[:15]:
        a = f"{avg:+.1f}%" if avg is not None else "n/a"
        print(f"  {w[:8]}...{w[-6:]} | event {n} | selesai {nr} | naik {up} | rata-rata 24j {a}")


def main():
    events = load_events()
    resolved = sum(1 for e in events if e["pct"] is not None)
    print(f"Event unik (wallet+token): {len(events)} | sudah ada hasil 24 jam: {resolved}")
    recs = build_records(events)
    report("EKSPLORASI (semua data, bukan keputusan)", recs)
    report("KONFIRMASI (event sejak 2026-10-17)", [r for r in recs if r[0] >= SPLIT])
    wallet_table(events)
    print("\nCatatan: event pada token yang sama dan waktu berdekatan tidak sepenuhnya independen.")


main()
