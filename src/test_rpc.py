import json
import urllib.request

env = {}
with open(".env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key] = value

url = env.get("BASE_RPC_URL")
if not url:
    raise SystemExit("BASE_RPC_URL belum terisi di .env")


def rpc(method):
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": []}
    ).encode()
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)["result"]


try:
    chain_id = int(rpc("eth_chainId"), 16)
    block = int(rpc("eth_blockNumber"), 16)
except Exception as e:
    raise SystemExit(f"Gagal terhubung: {type(e).__name__}")

print("Chain ID:", chain_id, "(Base Mainnet harus 8453)")
print("Blok terbaru:", block)
print("BERHASIL" if chain_id == 8453 else "CHAIN SALAH, cek pilihan network di Alchemy")
