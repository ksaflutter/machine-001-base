import json
import os
import sqlite3
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

DB_PATH = "data/radar.db"
BLOCKS_LOOKBACK = 7200
MAX_RESULTS_PER_PAGE = 1000
MAX_PAGES = 5
MIN_NET_FLOW_USD = 2000
MIN_BUYER_USD = 300
MAX_BUYERS_CHECKED_PER_TOKEN = 25
CONTRACT_TX_THRESHOLD = 15


def load_config():
    config = {}
    if os.path.exists(".env"):
        with open(".env") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    config[k] = v
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "BASE_RPC_URL"):
        if os.environ.get(key):
            config[key] = os.environ[key]
    return config


def rpc(url, method, params):
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    ).encode()
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.load(resp)
    if "error" in result:
        raise SystemExit(f"RPC error: {result['error']}")
    return result["result"]


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute(
        """CREATE TABLE IF NOT EXISTS whale_moves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            detected_at TEXT,
            token_address TEXT,
            pool_label TEXT,
            wallet TEXT,
            net_tokens REAL,
            net_usd REAL,
            price_usd_at_detection REAL,
            price_checked_24h INTEGER DEFAULT 0,
            price_change_24h_pct REAL,
            tx_count INTEGER,
            last_seen TEXT,
            is_new_holder INTEGER,
            wallet_age_days REAL,
            wallet_age_tag TEXT
        )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS notified_whales (
            token_address TEXT,
            wallet TEXT,
            first_notified_at TEXT,
            PRIMARY KEY (token_address, wallet)
        )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS token_buyer_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            checked_at TEXT,
            token_address TEXT,
            pool_label TEXT,
            buyers_checked INTEGER,
            fresh_buyers INTEGER,
            fresh_pct REAL
        )"""
    )
    cols = [r[1] for r in con.execute("PRAGMA table_info(whale_moves)")]
    for col, decl in [
        ("wallet_age_days", "REAL"),
        ("wallet_age_tag", "TEXT"),
    ]:
        if col not in cols:
            con.execute(f"ALTER TABLE whale_moves ADD COLUMN {col} {decl}")
    return con


def get_watchlist_tokens(con):
    rows = con.execute(
        """SELECT token_address, name, price_usd, seen_at
           FROM observations
           WHERE decision = 'LOLOS'
           AND token_address IS NOT NULL
           AND seen_at >= datetime('now', '-1 day')
           ORDER BY seen_at DESC"""
    ).fetchall()

    grouped = {}
    for token_address, name, price_usd, seen_at in rows:
        if token_address not in grouped:
            grouped[token_address] = {"pools": set(), "price_usd": price_usd}
        grouped[token_address]["pools"].add(name)

    result = []
    for token_address, info in grouped.items():
        pool_label = " | ".join(sorted(info["pools"]))
        result.append((token_address, pool_label, info["price_usd"]))
    return result


def fetch_all_transfers(url, token_address, from_block):
    all_transfers = []
    page_key = None
    for _ in range(MAX_PAGES):
        params = {
            "fromBlock": from_block,
            "toBlock": "latest",
            "contractAddresses": [token_address],
            "category": ["erc20"],
            "withMetadata": True,
            "maxCount": hex(MAX_RESULTS_PER_PAGE),
            "order": "desc",
        }
        if page_key:
            params["pageKey"] = page_key
        result = rpc(url, "alchemy_getAssetTransfers", [params])
        transfers = result.get("transfers", [])
        all_transfers.extend(transfers)
        page_key = result.get("pageKey")
        if not page_key:
            break
    return all_transfers


def has_prior_activity(url, token_address, wallet, before_block_int):
    """Cek apakah wallet ini pernah bertransaksi TOKEN INI sebelum jendela waktu sekarang."""
    if before_block_int <= 0:
        return False
    to_block = hex(before_block_int - 1)
    for direction in ("fromAddress", "toAddress"):
        params = {
            "fromBlock": "0x0",
            "toBlock": to_block,
            "contractAddresses": [token_address],
            "category": ["erc20"],
            "maxCount": "0x1",
            "order": "desc",
            direction: wallet,
        }
        try:
            result = rpc(url, "alchemy_getAssetTransfers", [params])
        except SystemExit:
            return None  # tidak diketahui, bukan diasumsikan False
        if result.get("transfers"):
            return True
    return False


def get_wallet_age_days(url, wallet):
    """Cari transaksi PALING AWAL wallet ini di seluruh riwayat (bukan cuma token ini).
    Return None kalau tidak bisa dipastikan (lebih aman daripada menebak)."""
    earliest_ts = None
    for direction in ("fromAddress", "toAddress"):
        params = {
            "fromBlock": "0x0",
            "toBlock": "latest",
            "category": ["external", "erc20"],
            "withMetadata": True,
            "maxCount": "0x1",
            "order": "asc",
            direction: wallet,
        }
        try:
            result = rpc(url, "alchemy_getAssetTransfers", [params])
        except SystemExit:
            continue
        transfers = result.get("transfers", [])
        if transfers:
            ts = (transfers[0].get("metadata") or {}).get("blockTimestamp")
            if ts:
                try:
                    t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if earliest_ts is None or t < earliest_ts:
                        earliest_ts = t
                except ValueError:
                    pass

    if earliest_ts is None:
        return None
    age = (datetime.now(timezone.utc) - earliest_ts).total_seconds() / 86400
    return age


def classify_wallet_age(age_days):
    if age_days is None:
        return "tidak diketahui"
    if age_days < 3:
        return "SANGAT BARU (<3 hari)"
    if age_days < 14:
        return "baru (<14 hari)"
    return "wallet lama"


def analyze_token(url, token_address, pool_label, price_usd, from_block, from_block_int):
    transfers = fetch_all_transfers(url, token_address, from_block)
    if not transfers:
        return [], 0, {"buyers_checked": 0, "fresh_buyers": 0}

    net_flow = defaultdict(float)
    last_seen = {}
    tx_count = defaultdict(int)

    for t in transfers:
        value = t.get("value") or 0
        frm, to = t.get("from"), t.get("to")
        ts = (t.get("metadata") or {}).get("blockTimestamp", "?")
        if frm:
            net_flow[frm] -= value
            last_seen[frm] = ts
            tx_count[frm] += 1
        if to:
            net_flow[to] += value
            last_seen[to] = ts
            tx_count[to] += 1

    if not price_usd or price_usd <= 0:
        return [], 0, {"buyers_checked": 0, "fresh_buyers": 0}

    min_whale_tokens = MIN_NET_FLOW_USD / price_usd
    min_buyer_tokens = MIN_BUYER_USD / price_usd

    candidates = [
        (addr, flow) for addr, flow in net_flow.items()
        if flow >= min_buyer_tokens and tx_count[addr] < CONTRACT_TX_THRESHOLD
    ]
    candidates.sort(key=lambda x: x[1], reverse=True)
    candidates = candidates[:MAX_BUYERS_CHECKED_PER_TOKEN]

    skipped_contracts = sum(
        1 for addr, flow in net_flow.items()
        if flow >= min_buyer_tokens and tx_count[addr] >= CONTRACT_TX_THRESHOLD
    )

    whales = []
    buyers_checked = 0
    fresh_buyers = 0

    for addr, flow in candidates:
        is_new = has_prior_activity(url, token_address, addr, from_block_int)
        is_new = (is_new is False)  # None (tidak diketahui) dianggap TIDAK fresh, aman
        buyers_checked += 1
        if is_new:
            fresh_buyers += 1

        net_usd = flow * price_usd
        if net_usd >= MIN_NET_FLOW_USD:
            age_days = get_wallet_age_days(url, addr)
            age_tag = classify_wallet_age(age_days)
            whales.append({
                "pool": pool_label,
                "token_address": token_address,
                "wallet": addr,
                "net_tokens": flow,
                "net_usd": net_usd,
                "price_usd_at_detection": price_usd,
                "tx_count": tx_count[addr],
                "is_new_holder": is_new,
                "wallet_age_days": age_days,
                "wallet_age_tag": age_tag,
                "last_seen": last_seen.get(addr),
            })

    stats = {"buyers_checked": buyers_checked, "fresh_buyers": fresh_buyers}
    return whales, skipped_contracts, stats


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
    url = config.get("BASE_RPC_URL")
    if not url:
        raise SystemExit("BASE_RPC_URL belum terisi di .env")

    con = init_db()
    tokens = get_watchlist_tokens(con)
    print(f"Token unik di watchlist (24 jam terakhir): {len(tokens)}\n")

    if not tokens:
        print("Belum ada token watchlist. Jalankan scan.py dulu.")
        con.close()
        return

    latest = int(rpc(url, "eth_blockNumber", []), 16)
    from_block_int = max(latest - BLOCKS_LOOKBACK, 0)
    from_block = hex(from_block_int)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    already_notified = {
        (row[0], row[1]) for row in con.execute("SELECT token_address, wallet FROM notified_whales")
    }

    all_whales = []
    for token_address, pool_label, price_usd in tokens:
        print(f"Memindai: {pool_label} ...")
        try:
            whales, skipped, stats = analyze_token(
                url, token_address, pool_label, price_usd, from_block, from_block_int
            )
            all_whales.extend(whales)

            if stats["buyers_checked"] > 0:
                fresh_pct = stats["fresh_buyers"] / stats["buyers_checked"] * 100
                con.execute(
                    "INSERT INTO token_buyer_stats (checked_at, token_address, pool_label,"
                    " buyers_checked, fresh_buyers, fresh_pct) VALUES (?,?,?,?,?,?)",
                    (now, token_address, pool_label, stats["buyers_checked"],
                     stats["fresh_buyers"], fresh_pct),
                )
                print(f"  whale: {len(whales)} | kontrak disaring: {skipped} | "
                      f"Fresh Buyer: {stats['fresh_buyers']}/{stats['buyers_checked']} ({fresh_pct:.0f}%)")
            else:
                print(f"  whale: {len(whales)} | kontrak disaring: {skipped} | tidak ada buyer untuk dicek")
        except Exception as e:
            print(f"  gagal: {type(e).__name__}: {e}")

    con.commit()
    all_whales.sort(key=lambda w: w["net_usd"], reverse=True)

    new_whales = []
    for w in all_whales:
        con.execute(
            "INSERT INTO whale_moves (detected_at, token_address, pool_label, wallet,"
            " net_tokens, net_usd, price_usd_at_detection, tx_count, is_new_holder,"
            " wallet_age_days, wallet_age_tag, last_seen)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (now, w["token_address"], w["pool"], w["wallet"], w["net_tokens"],
             w["net_usd"], w["price_usd_at_detection"], w["tx_count"],
             int(w["is_new_holder"]), w["wallet_age_days"], w["wallet_age_tag"],
             w["last_seen"]),
        )
        key = (w["token_address"], w["wallet"])
        if key not in already_notified:
            new_whales.append(w)
            con.execute(
                "INSERT OR IGNORE INTO notified_whales (token_address, wallet,"
                " first_notified_at) VALUES (?,?,?)",
                (w["token_address"], w["wallet"], now),
            )

    con.commit()
    total = con.execute("SELECT COUNT(*) FROM whale_moves").fetchone()[0]
    con.close()

    print(f"\n=== RINGKASAN: {len(all_whales)} whale move | Total catatan: {total} ===")
    print(f"Baru (belum pernah dinotifikasi): {len(new_whales)}\n")

    for w in all_whales[:20]:
        tag = "BARU DI TOKEN INI" if w["is_new_holder"] else "sudah pegang sebelumnya"
        print(f"${w['net_usd']:,.0f}  ({w['net_tokens']:,.2f} token, {w['tx_count']} tx)")
        print(f"  [{tag}] | umur wallet: {w['wallet_age_tag']}")
        print(f"  token : {w['pool']}")
        print(f"  wallet: {w['wallet']}")
        print(f"  waktu : {w['last_seen']}")
        print()

    if new_whales:
        lines = ["Machine 001 Whale Watcher: pergerakan baru\n"]
        for w in new_whales[:10]:
            tag = "BARU" if w["is_new_holder"] else "nambah"
            lines.append(
                f"- {w['pool']} [{tag}, {w['wallet_age_tag']}]\n"
                f"  ${w['net_usd']:,.0f} ({w['net_tokens']:,.2f} token, {w['tx_count']} tx)\n"
                f"  wallet: {w['wallet'][:10]}...{w['wallet'][-6:]}"
            )
        send_telegram(config, "\n".join(lines))


main()
