import json
import urllib.request

env = {}
with open(".env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key] = value

token = env.get("TELEGRAM_BOT_TOKEN")
chat_id = env.get("TELEGRAM_CHAT_ID")

if not token or not chat_id:
    raise SystemExit("Token atau Chat ID belum terisi di .env")

payload = json.dumps({
    "chat_id": chat_id,
    "text": "Machine 001 Base: koneksi Telegram berhasil. Sistem aman, trading NONAKTIF.",
}).encode()

req = urllib.request.Request(
    f"https://api.telegram.org/bot{token}/sendMessage",
    data=payload,
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(req) as resp:
    result = json.load(resp)

print("Terkirim!" if result.get("ok") else "Gagal:", result)
