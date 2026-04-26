# AIChatScraper – VPS WebSocket Proxy

Menjalankan `api_server.py` di VPS secara publik, sementara semua proses scraping tetap berjalan di lokal (Windows 10).

```
[Client] → HTTP → [VPS: vps_server.py] ←→ WebSocket ←→ [Windows: local_worker.py]
                                                                ↓
                                                         QwenScraper (browser)
```

---

## Requirements

| Mesin | Kebutuhan |
|---|---|
| VPS Ubuntu 22.xx | Python 3.10+, pip |
| Windows 10 (Lokal) | Python 3.14, pip, project AIChatScraper sudah bisa jalan |

---

## Instalasi

### VPS

```bash
# Upload vps_server.py ke VPS, lalu:
pip install fastapi uvicorn websockets
```

### Windows (Lokal)

```bash
# Taruh local_worker.py di folder yang sama dengan api_server.py
pip install websockets
```

> Dependensi lainnya (playwright, dll) sudah terpasang karena project ini sudah berjalan sebelumnya.

---

## Menjalankan

### 1. Jalankan VPS terlebih dahulu

```bash
python vps_server.py --host 0.0.0.0 --port 8000 --token rahasia123
```

### 2. Jalankan Worker di Windows

```bash
python local_worker.py --vps ws://YOUR_VPS_IP:8000/ws/worker --token rahasia123
```

Ganti `YOUR_VPS_IP` dengan IP publik VPS kamu.

Jika berhasil konek, terminal Windows akan menampilkan:

```
✅ Terhubung ke VPS!
```

### 3. Test

```bash
curl http://YOUR_VPS_IP:8000/health
```

```bash
curl -X POST http://YOUR_VPS_IP:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen","messages":[{"role":"user","content":"Halo!"}]}'
```

---

## Opsi CLI

### `vps_server.py`

| Argumen | Default | Keterangan |
|---|---|---|
| `--host` | `0.0.0.0` | Bind host |
| `--port` | `8000` | Port server |
| `--token` | _(kosong)_ | Token autentikasi worker |
| `--request-timeout` | `300` | Timeout tunggu hasil dari worker (detik) |
| `--worker-timeout` | `30` | Timeout tunggu worker idle (detik) |
| `--log-level` | `info` | Level log (`debug` / `info` / `warning` / `error`) |

### `local_worker.py`

| Argumen | Default | Keterangan |
|---|---|---|
| `--vps` | _(wajib)_ | WebSocket URL VPS |
| `--token` | _(kosong)_ | Token autentikasi (harus sama dengan VPS) |
| `--workers` | `1` | Jumlah task yang bisa diproses bersamaan |
| `--no-headless` | _(off)_ | Tampilkan jendela browser |
| `--cookies-dir` | dari config | Folder cookie |
| `--session-ttl` | `3600` | Masa aktif session (detik) |
| `--reconnect-delay` | `5.0` | Jeda sebelum reconnect jika putus (detik) |

---

## Firewall VPS

```bash
sudo ufw allow 8000/tcp
sudo ufw allow 22/tcp
sudo ufw enable
```

---

## Catatan

- **Session** disimpan di Windows. Session ID diteruskan via header `X-Session-ID` seperti biasa.
- **Auto-reconnect** — worker akan otomatis konek ulang jika koneksi ke VPS terputus.
- **Tanpa Nginx** — tidak perlu reverse proxy, semua dihandle Python.
- Jika ingin HTTPS, pasang Nginx + Certbot di depan `vps_server.py` (opsional).
