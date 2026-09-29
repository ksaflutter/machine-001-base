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
            tx_count INTEGER,
            last_seen TEXT
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


def analyze_token(url, token_address, pool_label, price_usd, from_block):
    transfers = fetch_all_transfers(url, token_address, from_block)
    if not transfers:
        return [], 0

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
        return [], 0
    min_tokens = MIN_NET_FLOW_USD / price_usd

    results = []
    skipped = 0
    for addr, flow in net_flow.items():
        if flow < min_tokens:
            continue
        if tx_count[addr] >= CONTRACT_TX_THRESHOLD:
            skipped += 1
            continue
        results.append({
            "pool": pool_label,
            "token_address": token_address,
            "wallet": addr,
            "net_tokens": flow,
            "net_usd": flow * price_usd,
            "tx_count": tx_count[addr],
            "last_seen": last_seen.get(addr),
        })
    return results, skipped


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
    from_block = hex(max(latest - BLOCKS_LOOKBACK, 0))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    already_notified = {
        (row[0], row[1]) for row in con.execute("SELECT token_address, wallet FROM notified_whales")
    }

    all_whales = []
    for token_address, pool_label, price_usd in tokens:
        print(f"Memindai: {pool_label} ...")
        try:
            whales, skipped = analyze_token(url, token_address, pool_label, price_usd, from_block)
            all_whales.extend(whales)
            print(f"  whale: {len(whales)} | kontrak/router disaring: {skipped}")
        except Exception as e:
            print(f"  gagal: {type(e).__name__}: {e}")

    all_whales.sort(key=lambda w: w["net_usd"], reverse=True)

    new_whales = []
    for w in all_whales:
        con.execute(
            "INSERT INTO whale_moves (detected_at, token_address, pool_label, wallet,"
            " net_tokens, net_usd, tx_count, last_seen) VALUES (?,?,?,?,?,?,?,?)",
            (now, w["token_address"], w["pool"], w["wallet"], w["net_tokens"],
             w["net_usd"], w["tx_count"], w["last_seen"]),
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
        print(f"${w['net_usd']:,.0f}  ({w['net_tokens']:,.2f} token, {w['tx_count']} tx)")
        print(f"  token : {w['pool']}")
        print(f"  wallet: {w['wallet']}")
        print(f"  waktu : {w['last_seen']}")
        print()

    if new_whales:
        lines = ["Machine 001 Whale Watcher: pergerakan baru\n"]
        for w in new_whales[:10]:
            lines.append(
                f"- {w['pool']}\n"
                f"  ${w['net_usd']:,.0f} ({w['net_tokens']:,.2f} token, {w['tx_count']} tx)\n"
                f"  wallet: {w['wallet'][:10]}...{w['wallet'][-6:]}"
            )
        send_telegram(config, "\n".join(lines))


main()
