# AIChatScraper – VPS WebSocket Proxy

Menjalankan `vps_server.py` di VPS secara publik, sementara semua proses scraping tetap
berjalan di lokal (Windows 10).

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
| Windows 10 (Lokal) | Python 3.10+, pip, project AIChatScraper sudah bisa jalan |

---

## Instalasi

### VPS

```bash
# Upload vps_server.py ke VPS, lalu:
pip install fastapi uvicorn websockets
```

### Windows (Lokal)

```bash
# Taruh local_worker.py di folder yang sama dengan project AIChatScraper
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
Worker#0 ✅ Terhubung ke VPS! (max_concurrent=4)
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
| `--worker-timeout` | `30` | Timeout tunggu worker tersedia (detik) |
| `--log-level` | `info` | Level log (`debug` / `info` / `warning` / `error`) |

### `local_worker.py`

| Argumen | Default | Keterangan |
|---|---|---|
| `--vps` | _(wajib)_ | WebSocket URL VPS |
| `--token` | _(kosong)_ | Token autentikasi (harus sama dengan VPS) |
| `--workers` | `4` | Jumlah task concurrent yang bisa diterima worker ini |
| `--no-headless` | _(off)_ | Tampilkan jendela browser |
| `--cookies-dir` | dari config | Folder cookie |
| `--session-ttl` | `3600` | Masa aktif session (detik) |
| `--reconnect-delay` | `5.0` | Jeda sebelum reconnect jika koneksi putus (detik) |

---

## Model Concurrency

Worker tidak lagi single-slot. Satu worker bisa menangani **beberapa task bersamaan** dengan
aturan berikut:

| Tipe Task | Perilaku |
|---|---|
| **NEW** (tanpa `X-Session-ID`) | Langsung dikirim ke worker dengan slot paling kosong. Tidak perlu menunggu task lain selesai. |
| **CONTINUE** (dengan `X-Session-ID`) | Dikirim ke worker yang sedang memegang session tersebut (sticky routing). Dua request CONTINUE untuk session yang **sama** mengantri — tidak pernah berjalan paralel. |

Kapasitas tiap worker dilaporkan ke VPS saat pertama konek melalui pesan registrasi
`{"type": "register", "max_concurrent": N}`. VPS memilih worker berdasarkan `active_tasks`
terkecil untuk task NEW.

---

## Session & Header

Session disimpan di Windows (dalam memori `local_worker.py`). Untuk melanjutkan percakapan
yang sama, kirim header `X-Session-ID` dengan nilai yang dikembalikan dari response sebelumnya.

```bash
# Request pertama (NEW) — simpan session ID dari response header
curl -X POST http://YOUR_VPS_IP:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -D headers.txt \
  -d '{"model":"qwen","messages":[{"role":"user","content":"Halo!"}]}'

# Request berikutnya (CONTINUE) — gunakan X-Session-ID dari headers.txt
curl -X POST http://YOUR_VPS_IP:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-Session-ID: <session_id_dari_response>" \
  -d '{"model":"qwen","messages":[{"role":"user","content":"Lanjutkan..."}]}'
```

Response selalu menyertakan header berikut:

| Header | Isi |
|---|---|
| `X-Session-ID` | ID session untuk request CONTINUE berikutnya |
| `X-Cookie-File` | Nama file cookie yang digunakan |
| `X-Conversation-URL` | URL conversation Qwen yang aktif |

---

## Firewall VPS

```bash
sudo ufw allow 8000/tcp
sudo ufw allow 22/tcp
sudo ufw enable
```

---

## Catatan

- **Auto-reconnect** — worker otomatis konek ulang ke VPS jika koneksi terputus.
- **Load balancing** — jika ada beberapa worker konek, VPS mendistribusikan task NEW ke worker
  dengan beban terkecil.
- **Tanpa Nginx** — tidak perlu reverse proxy, semua dihandle Python secara langsung.
- Jika ingin HTTPS, pasang Nginx + Certbot di depan `vps_server.py` (opsional).
- **Think mode** pada mode `continue` tidak dapat diubah setelah percakapan dimulai karena
  Qwen menyembunyikan dropdown tersebut di halaman conversation.