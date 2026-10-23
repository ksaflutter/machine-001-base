"""H5 (dicatat 2026-10-23, SEBELUM diuji pada data baru).

Sinyal: pada satu pemindaian, sebuah token dibeli >= 3 wallet berulang
(wallet berulang = sudah tercatat di >= 3 pemindaian sebelumnya).
Kontrol: sinyal whale lain (di bawah 3 wallet berulang).
Hasil: perubahan harga 24 jam, dikurangi biaya 2%. 1 sinyal/token/24 jam.
Sampel konfirmasi: detected_at >= 2026-10-23 (UTC).
Lulus jika: >= 10 sinyal per kelompok, p < 0.01 (Bonferroni 5 hipotesis),
median sinyal > median kontrol, dan rata-rata sinyal setelah biaya > 0.
Dijalankan SATU kali pada/sesudah 2026-11-06. Sebelum itu hanya hitung jumlah.
Kriteria tidak boleh diubah setelah melihat hasil.
"""
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from statistics import mean, median

DB_PATH = "data/radar.db"
CUTOFF = "2026-10-23"
EARLIEST_RUN = "2026-11-06"
K_REPEAT_WALLETS = 3
MIN_PRIOR_SCANS = 3
MIN_GROUP = 10
ALPHA = 0.01
COST_PCT = 2.0
PERMS = 5000


def build():
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT wallet, token_address, detected_at, price_checked_24h,"
        " price_change_24h_pct FROM whale_moves"
        " WHERE wallet IS NOT NULL AND token_address IS NOT NULL"
        " AND detected_at IS NOT NULL"
        " AND wallet NOT LIKE '0x0000000000000000%'"
        " ORDER BY detected_at ASC"
    ).fetchall()
    con.close()
    by_scan = {}
    for w, t, d, chk, pct in rows:
        by_scan.setdefault(d, []).append((w, t, chk, pct))
    seen_count = {}
    recs = []
    for d in sorted(by_scan):
        per_token = {}
        for w, t, chk, pct in by_scan[d]:
            e = per_token.setdefault(t, {"wallets": set(), "pct": None})
            e["wallets"].add(w)
            if e["pct"] is None and chk and pct is not None:
                e["pct"] = pct
        for t, e in per_token.items():
            n = sum(1 for w in e["wallets"]
                    if seen_count.get(w, 0) >= MIN_PRIOR_SCANS)
            if e["pct"] is not None:
                recs.append({"dt": d, "token": t, "n": n, "pct": e["pct"]})
        for w in {x[0] for x in by_scan[d]}:
            seen_count[w] = seen_count.get(w, 0) + 1
    return recs


def decorrelate(recs):
    out = []
    last = {}
    for r in sorted(recs, key=lambda x: x["dt"]):
        t = datetime.fromisoformat(r["dt"])
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        prev = last.get(r["token"])
        if prev is None or t - prev >= timedelta(hours=24):
            out.append(r)
            last[r["token"]] = t
    return out


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
    sig = [r["pct"] for r in recs if r["n"] >= K_REPEAT_WALLETS]
    ctl = [r["pct"] for r in recs if r["n"] < K_REPEAT_WALLETS]
    print(f"\n{title}")
    for name, g in (("sinyal H5", sig), ("kontrol", ctl)):
        if g:
            print(f"  {name}: n={len(g)} | rata-rata {mean(g):+.1f}% | median {median(g):+.1f}%")
        else:
            print(f"  {name}: n=0")
    if len(sig) < MIN_GROUP or len(ctl) < MIN_GROUP:
        print(f"  -> belum cukup data (butuh >= {MIN_GROUP} per kelompok)")
        return
    p = perm_p(sig, ctl)
    net = mean(sig) - COST_PCT
    ok = p < ALPHA and median(sig) > median(ctl) and net > 0
    print(f"  rata-rata sinyal setelah biaya {COST_PCT}%: {net:+.1f}%")
    print(f"  p (permutasi, 1 arah) = {p:.4f} | ambang {ALPHA}")
    print(f"  -> {'LULUS' if ok else 'BELUM LULUS'}")


def main():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    thinned = decorrelate(build())
    conf = [r for r in thinned if r["dt"] >= CUTOFF]
    n_sig = sum(1 for r in conf if r["n"] >= K_REPEAT_WALLETS)
    n_ctl = len(conf) - n_sig
    print(f"Sampel konfirmasi sejak {CUTOFF}: sinyal H5 {n_sig} | kontrol {n_ctl}")
    if today < EARLIEST_RUN:
        print(f"Belum waktunya. Aturan: jalankan SATU kali pada/sesudah {EARLIEST_RUN}.")
        print("Hasil sengaja tidak ditampilkan.")
        return
    report("KONFIRMASI (keputusan)", conf)
    report("EKSPLORASI (semua data, bukan keputusan)", thinned)


main()
