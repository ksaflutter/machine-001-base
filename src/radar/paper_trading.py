import json
import sqlite3
import urllib.request
from datetime import datetime, timezone

DB_PATH = "data/radar.db"

MIN_WHALE_USD = 2000
MAX_WHALE_USD = 10000
REQUIRED_AGE_TAG = "SANGAT BARU (<3 hari)"

STARTING_CAPITAL = 50.0
POSITION_SIZE = 5.0
MAX_OPEN_POSITIONS = 10

TAKE_PROFIT_PCT = 15.0
STOP_LOSS_PCT = -10.0
MAX_HOLD_HOURS = 72


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def get_current_price(token_address):
    url = f"https://api.geckoterminal.com/api/v2/networks/base/tokens/{token_address}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json;version=20230302",
            "User-Agent": "machine-001-base/0.1",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.load(resp)
        price = data.get("data", {}).get("attributes", {}).get("price_usd")
        return num(price)
    except Exception:
        return None


def init_db(con):
    con.execute(
        """CREATE TABLE IF NOT EXISTS paper_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            whale_move_id INTEGER,
            token_address TEXT,
            pool_label TEXT,
            wallet TEXT,
            entry_price REAL,
            entry_time TEXT,
            position_size_usd REAL,
            status TEXT DEFAULT 'OPEN',
            exit_price REAL,
            exit_time TEXT,
            exit_reason TEXT,
            pnl_usd REAL,
            pnl_pct REAL
        )"""
    )
    con.commit()


def open_new_positions(con):
    already_traded = {
        row[0] for row in con.execute("SELECT whale_move_id FROM paper_positions")
    }

    open_count = con.execute(
        "SELECT COUNT(*) FROM paper_positions WHERE status = 'OPEN'"
    ).fetchone()[0]

    slots_available = MAX_OPEN_POSITIONS - open_count
    if slots_available <= 0:
        print(f"Semua {MAX_OPEN_POSITIONS} slot posisi penuh, tidak buka posisi baru.")
        return 0

    candidates = con.execute(
        """SELECT id, token_address, pool_label, wallet, price_usd_at_detection, detected_at
           FROM whale_moves
           WHERE net_usd >= ? AND net_usd <= ?
           AND wallet_age_tag = ?
           AND price_usd_at_detection IS NOT NULL
           AND price_usd_at_detection > 0
           ORDER BY detected_at ASC""",
        (MIN_WHALE_USD, MAX_WHALE_USD, REQUIRED_AGE_TAG),
    ).fetchall()

    opened = 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for move_id, token_address, pool_label, wallet, price, detected_at in candidates:
        if opened >= slots_available:
            break
        if move_id in already_traded:
            continue

        con.execute(
            """INSERT INTO paper_positions
               (whale_move_id, token_address, pool_label, wallet, entry_price,
                entry_time, position_size_usd, status)
               VALUES (?,?,?,?,?,?,?, 'OPEN')""",
            (move_id, token_address, pool_label, wallet, price, now, POSITION_SIZE),
        )
        print(f"BUKA POSISI: {pool_label} @ ${price:.8f} (whale move #{move_id})")
        opened += 1

    con.commit()
    return opened


def check_open_positions(con):
    open_positions = con.execute(
        """SELECT id, token_address, pool_label, entry_price, entry_time, position_size_usd
           FROM paper_positions WHERE status = 'OPEN'"""
    ).fetchall()

    if not open_positions:
        print("Tidak ada posisi terbuka untuk dicek.")
        return

    price_cache = {}
    now = datetime.now(timezone.utc)

    for pos_id, token_address, pool_label, entry_price, entry_time, size in open_positions:
        if token_address not in price_cache:
            price_cache[token_address] = get_current_price(token_address)

        current_price = price_cache[token_address]
        if current_price is None or current_price <= 0:
            print(f"  Posisi #{pos_id} ({pool_label}): gagal ambil harga, lewati")
            continue

        pct_change = ((current_price - entry_price) / entry_price) * 100
        entry_dt = datetime.fromisoformat(entry_time)
        hours_held = (now - entry_dt).total_seconds() / 3600

        exit_reason = None
        if pct_change >= TAKE_PROFIT_PCT:
            exit_reason = "TAKE_PROFIT"
        elif pct_change <= STOP_LOSS_PCT:
            exit_reason = "STOP_LOSS"
        elif hours_held >= MAX_HOLD_HOURS:
            exit_reason = "TIME_LIMIT"

        if exit_reason:
            pnl_usd = size * (pct_change / 100)
            now_str = now.isoformat(timespec="seconds")
            con.execute(
                """UPDATE paper_positions
                   SET status = 'CLOSED', exit_price = ?, exit_time = ?,
                       exit_reason = ?, pnl_usd = ?, pnl_pct = ?
                   WHERE id = ?""",
                (current_price, now_str, exit_reason, pnl_usd, pct_change, pos_id),
            )
            print(f"  TUTUP #{pos_id} ({pool_label}): {exit_reason} | {pct_change:+.1f}% | PnL ${pnl_usd:+.2f}")
        else:
            print(f"  Posisi #{pos_id} ({pool_label}): masih terbuka, {pct_change:+.1f}% ({hours_held:.1f} jam)")

    con.commit()


def print_portfolio_summary(con):
    closed = con.execute(
        "SELECT COUNT(*), SUM(pnl_usd), AVG(pnl_pct) FROM paper_positions WHERE status = 'CLOSED'"
    ).fetchone()
    closed_count, total_pnl, avg_pct = closed
    total_pnl = total_pnl or 0.0

    wins = con.execute(
        "SELECT COUNT(*) FROM paper_positions WHERE status = 'CLOSED' AND pnl_usd > 0"
    ).fetchone()[0]

    open_count = con.execute(
        "SELECT COUNT(*) FROM paper_positions WHERE status = 'OPEN'"
    ).fetchone()[0]

    current_equity = STARTING_CAPITAL + total_pnl

    print(f"\n=== PORTFOLIO PAPER TRADING ===")
    print(f"Modal awal: ${STARTING_CAPITAL:.2f}")
    print(f"Posisi terbuka: {open_count}")
    print(f"Posisi tertutup: {closed_count}")
    if closed_count and closed_count > 0:
        print(f"Menang: {wins} ({wins/closed_count*100:.1f}%)")
        print(f"Rata-rata hasil per posisi: {avg_pct:+.1f}%")
    print(f"Total PnL (realized): ${total_pnl:+.2f}")
    print(f"Equity saat ini (modal + PnL realized): ${current_equity:.2f}")


def main():
    con = sqlite3.connect(DB_PATH)
    init_db(con)

    print("--- Mengecek posisi terbuka ---")
    check_open_positions(con)

    print("\n--- Membuka posisi baru (jika ada kandidat) ---")
    opened = open_new_positions(con)
    print(f"Posisi baru dibuka: {opened}")

    print_portfolio_summary(con)
    con.close()


main()
