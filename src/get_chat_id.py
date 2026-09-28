import json
import urllib.request

token = None
with open(".env") as f:
    for line in f:
        if line.startswith("TELEGRAM_BOT_TOKEN="):
            token = line.strip().split("=", 1)[1]

if not token:
    raise SystemExit("Token belum terisi di .env")

url = f"https://api.telegram.org/bot{token}/getUpdates"
with urllib.request.urlopen(url) as resp:
    data = json.load(resp)

found = False
for update in data.get("result", []):
    msg = update.get("message")
    if msg:
        chat = msg["chat"]
        print("Chat ID:", chat["id"], "| Nama:", chat.get("first_name", ""))
        found = True

if not found:
    print("Belum ada pesan. Kirim 'halo' ke botmu di Telegram, lalu jalankan ulang.")
