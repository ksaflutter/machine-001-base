import sqlite3

DB_PATH = "data/radar.db"


def main():
    con = sqlite3.connect(DB_PATH)

    total_checked = con.execute(
        "SELECT COUNT(*) FROM whale_moves WHERE price_checked_24h = 1"
    ).fetchone()[0]

    pending = con.execute(
        "SELECT COUNT(*) FROM whale_moves WHERE price_checked_24h = 0"
    ).fetchone()[0]

    print("=" * 50)
    print("MACHINE 001 - RINGKASAN HASIL WHALE WATCHER")
    print("=" * 50)
    print(f"\nTotal whale move tercatat: {total_checked + pending}")
    print(f"Sudah dicek hasilnya (>=24 jam): {total_checked}")
    print(f"Masih menunggu (<24 jam): {pending}\n")

    if total_checked == 0:
        print("Belum ada data hasil. Cek lagi setelah check_outcomes.py pernah jalan.")
        con.close()
        return

    up = con.execute(
        "SELECT COUNT(*) FROM whale_moves WHERE price_checked_24h = 1 AND price_change_24h_pct > 0"
    ).fetchone()[0]
    down = total_checked - up

    print("--- KESELURUHAN ---")
    print(f"Naik: {up} ({up/total_checked*100:.1f}%)")
    print(f"Turun: {down} ({down/total_checked*100:.1f}%)")

    avg_change = con.execute(
        "SELECT AVG(price_change_24h_pct) FROM whale_moves WHERE price_checked_24h = 1"
    ).fetchone()[0]
    print(f"Rata-rata perubahan: {avg_change:+.1f}%\n")

    print("--- BERDASARKAN JENIS WALLET ---")
    for label, flag in [("BARU DI TOKEN INI", 1), ("Sudah pegang sebelumnya", 0)]:
        rows = con.execute(
            "SELECT COUNT(*), AVG(price_change_24h_pct), "
            "SUM(CASE WHEN price_change_24h_pct > 0 THEN 1 ELSE 0 END) "
            "FROM whale_moves WHERE price_checked_24h = 1 AND is_new_holder = ?",
            (flag,),
        ).fetchone()
        count, avg, wins = rows
        if count and count > 0:
            print(f"{label}: {count} kasus | naik {wins} ({wins/count*100:.1f}%) | rata-rata {avg:+.1f}%")
        else:
            print(f"{label}: belum ada data")

    print("\n--- BERDASARKAN UKURAN WHALE ---")
    size_bands = [
        ("Kecil ($2rb - $10rb)", 2000, 10000),
        ("Sedang ($10rb - $50rb)", 10000, 50000),
        ("Besar ($50rb - $200rb)", 50000, 200000),
        ("Sangat besar (>$200rb)", 200000, None),
    ]
    for label, lo, hi in size_bands:
        if hi:
            rows = con.execute(
                "SELECT COUNT(*), AVG(price_change_24h_pct), "
                "SUM(CASE WHEN price_change_24h_pct > 0 THEN 1 ELSE 0 END) "
                "FROM whale_moves WHERE price_checked_24h = 1 AND net_usd >= ? AND net_usd < ?",
                (lo, hi),
            ).fetchone()
        else:
            rows = con.execute(
                "SELECT COUNT(*), AVG(price_change_24h_pct), "
                "SUM(CASE WHEN price_change_24h_pct > 0 THEN 1 ELSE 0 END) "
                "FROM whale_moves WHERE price_checked_24h = 1 AND net_usd >= ?",
                (lo,),
            ).fetchone()
        count, avg, wins = rows
        if count and count > 0:
            print(f"{label}: {count} kasus | naik {wins} ({wins/count*100:.1f}%) | rata-rata {avg:+.1f}%")
        else:
            print(f"{label}: belum ada data")

    print("\n--- BERDASARKAN UMUR WALLET ---")
    age_tags = ["SANGAT BARU (<3 hari)", "baru (<14 hari)", "wallet lama", "tidak diketahui"]
    for tag in age_tags:
        rows = con.execute(
            "SELECT COUNT(*), AVG(price_change_24h_pct), "
            "SUM(CASE WHEN price_change_24h_pct > 0 THEN 1 ELSE 0 END) "
            "FROM whale_moves WHERE price_checked_24h = 1 AND wallet_age_tag = ?",
            (tag,),
        ).fetchone()
        count, avg, wins = rows
        if count and count > 0:
            print(f"{tag}: {count} kasus | naik {wins} ({wins/count*100:.1f}%) | rata-rata {avg:+.1f}%")
        else:
            print(f"{tag}: belum ada data")

    print("\n--- 5 WHALE MOVE PALING UNTUNG ---")
    top = con.execute(
        "SELECT pool_label, wallet, net_usd, price_change_24h_pct, is_new_holder "
        "FROM whale_moves WHERE price_checked_24h = 1 "
        "ORDER BY price_change_24h_pct DESC LIMIT 5"
    ).fetchall()
    for pool, wallet, net_usd, pct, is_new in top:
        tag = "BARU" if is_new else "lama"
        print(f"  {pct:+.1f}%  {pool} [{tag}]  (whale ${net_usd:,.0f}, {wallet[:10]}...)")

    print("\n--- 5 WHALE MOVE PALING RUGI ---")
    bottom = con.execute(
        "SELECT pool_label, wallet, net_usd, price_change_24h_pct, is_new_holder "
        "FROM whale_moves WHERE price_checked_24h = 1 "
        "ORDER BY price_change_24h_pct ASC LIMIT 5"
    ).fetchall()
    for pool, wallet, net_usd, pct, is_new in bottom:
        tag = "BARU" if is_new else "lama"
        print(f"  {pct:+.1f}%  {pool} [{tag}]  (whale ${net_usd:,.0f}, {wallet[:10]}...)")

    con.close()


main()
