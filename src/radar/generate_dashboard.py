import sqlite3
from datetime import datetime, timezone

DB_PATH = "data/radar.db"
OUTPUT_PATH = "docs/index.html"


def esc(s):
    if s is None:
        return ""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def main():
    con = sqlite3.connect(DB_PATH)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    watchlist = con.execute(
        """SELECT name, liquidity_usd, volume_24h, change_24h, price_usd, seen_at
           FROM observations
           WHERE decision = 'LOLOS'
           AND seen_at >= datetime('now', '-1 day')
           ORDER BY seen_at DESC"""
    ).fetchall()

    whales = con.execute(
        """SELECT pool_label, wallet, net_usd, net_tokens, tx_count,
                  is_new_holder, wallet_age_tag, detected_at,
                  price_checked_24h, price_change_24h_pct
           FROM whale_moves
           ORDER BY detected_at DESC
           LIMIT 40"""
    ).fetchall()

    wallet_watch = con.execute(
        """SELECT wallet,
                  COUNT(*) as appearances,
                  COUNT(DISTINCT token_address) as unique_tokens,
                  SUM(net_usd) as total_usd,
                  GROUP_CONCAT(DISTINCT pool_label) as tokens,
                  MAX(detected_at) as last_seen
           FROM whale_moves
           GROUP BY wallet
           HAVING COUNT(*) > 1
           ORDER BY appearances DESC, total_usd DESC
           LIMIT 20"""
    ).fetchall()

    total_whales = con.execute("SELECT COUNT(*) FROM whale_moves").fetchone()[0]
    checked = con.execute(
        "SELECT COUNT(*) FROM whale_moves WHERE price_checked_24h = 1"
    ).fetchone()[0]
    up = con.execute(
        "SELECT COUNT(*) FROM whale_moves WHERE price_checked_24h = 1 AND price_change_24h_pct > 0"
    ).fetchone()[0]

    con.close()

    watch_rows = ""
    for name, liq, vol, chg24, price, seen_at in watchlist:
        color = "green" if (chg24 or 0) >= 0 else "red"
        watch_rows += f"""
        <tr>
          <td>{esc(name)}</td>
          <td>${liq:,.0f}</td>
          <td>${vol:,.0f}</td>
          <td class="{color}">{chg24:+.1f}%</td>
          <td>{esc(seen_at)}</td>
        </tr>"""

    whale_rows = ""
    for pool, wallet, net_usd, net_tokens, tx_count, is_new, age_tag, detected_at, checked_flag, pct in whales:
        new_badge = '<span class="badge new">BARU</span>' if is_new else '<span class="badge old">lama</span>'
        if checked_flag and pct is not None:
            color = "green" if pct >= 0 else "red"
            outcome = f'<span class="{color}">{pct:+.1f}%</span>'
        else:
            outcome = '<span class="pending">menunggu</span>'
        short_wallet = f"{wallet[:8]}...{wallet[-6:]}"
        whale_rows += f"""
        <tr>
          <td>{esc(pool)}</td>
          <td>${net_usd:,.0f}</td>
          <td>{new_badge}</td>
          <td>{esc(age_tag)}</td>
          <td>{outcome}</td>
          <td class="mono">{short_wallet}</td>
          <td>{esc(detected_at)}</td>
        </tr>"""

    wallet_rows = ""
    for wallet, appearances, unique_tokens, total_usd, tokens, last_seen in wallet_watch:
        short_wallet = f"{wallet[:8]}...{wallet[-6:]}"
        tokens_short = tokens if len(tokens) < 60 else tokens[:57] + "..."
        wallet_rows += f"""
        <tr>
          <td class="mono">{short_wallet}</td>
          <td>{appearances}x</td>
          <td>{unique_tokens}</td>
          <td>${total_usd:,.0f}</td>
          <td>{esc(tokens_short)}</td>
          <td>{esc(last_seen)}</td>
        </tr>"""

    pct_up = (up / checked * 100) if checked > 0 else 0

    html = f"""<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Machine 001 Base - Dashboard</title>
<style>
  body {{ font-family: -apple-system, sans-serif; background: #0d1117; color: #c9d1d9; margin: 0; padding: 16px; }}
  h1 {{ color: #58a6ff; font-size: 20px; }}
  h2 {{ color: #58a6ff; font-size: 16px; margin-top: 24px; }}
  .updated {{ color: #8b949e; font-size: 12px; margin-bottom: 16px; }}
  .stats {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }}
  .stat-box {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 12px 16px; flex: 1; min-width: 120px; }}
  .stat-box .num {{ font-size: 22px; font-weight: bold; color: #58a6ff; }}
  .stat-box .label {{ font-size: 11px; color: #8b949e; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12px; margin-bottom: 24px; }}
  th {{ text-align: left; padding: 6px 8px; background: #161b22; color: #8b949e; border-bottom: 1px solid #30363d; }}
  td {{ padding: 6px 8px; border-bottom: 1px solid #21262d; }}
  .green {{ color: #3fb950; }}
  .red {{ color: #f85149; }}
  .pending {{ color: #8b949e; }}
  .mono {{ font-family: monospace; font-size: 11px; }}
  .badge {{ padding: 2px 6px; border-radius: 4px; font-size: 10px; font-weight: bold; }}
  .badge.new {{ background: #1f6feb33; color: #58a6ff; }}
  .badge.old {{ background: #30363d; color: #8b949e; }}
  .scroll {{ overflow-x: auto; }}
</style>
</head>
<body>
  <h1>Machine 001 Base — Radar &amp; Whale Watcher</h1>
  <div class="updated">Terakhir diperbarui: {now}</div>

  <div class="stats">
    <div class="stat-box"><div class="num">{len(watchlist)}</div><div class="label">Token watchlist (24 jam)</div></div>
    <div class="stat-box"><div class="num">{total_whales}</div><div class="label">Total whale move tercatat</div></div>
    <div class="stat-box"><div class="num">{checked}</div><div class="label">Sudah dicek hasil 24 jam</div></div>
    <div class="stat-box"><div class="num">{pct_up:.0f}%</div><div class="label">Persentase naik (dari yang dicek)</div></div>
  </div>

  <h2>Watchlist Token (24 jam terakhir)</h2>
  <div class="scroll">
  <table>
    <tr><th>Token</th><th>Likuiditas</th><th>Volume 24j</th><th>Harga 24j</th><th>Terdeteksi</th></tr>
    {watch_rows if watch_rows else '<tr><td colspan="5">Belum ada data</td></tr>'}
  </table>
  </div>

  <h2>Wallet Watch — Wallet yang Berulang Muncul</h2>
  <div class="scroll">
  <table>
    <tr><th>Wallet</th><th>Muncul</th><th>Token Unik</th><th>Total Nilai</th><th>Token</th><th>Terakhir Terlihat</th></tr>
    {wallet_rows if wallet_rows else "<tr><td colspan='6'>Belum ada wallet yang muncul berulang</td></tr>"}
  </table>
  </div>

  <h2>Whale Move Terbaru (40 terakhir)</h2>
  <div class="scroll">
  <table>
    <tr><th>Token</th><th>Nilai</th><th>Jenis</th><th>Umur Wallet</th><th>Hasil 24j</th><th>Wallet</th><th>Waktu</th></tr>
    {whale_rows if whale_rows else '<tr><td colspan="7">Belum ada data</td></tr>'}
  </table>
  </div>
</body>
</html>"""

    with open(OUTPUT_PATH, "w") as f:
        f.write(html)
    print(f"Dashboard dibuat: {OUTPUT_PATH}")


main()
