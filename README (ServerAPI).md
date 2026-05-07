# AIChatScraper – OpenAI-Compatible API Server

> Drop-in local API server yang mengekspos Qwen AI (`chat.qwen.ai`) melalui antarmuka OpenAI Chat Completions — kompatibel dengan **OpenAI Python SDK**, `curl`, `httpx`, dan klien apapun yang mendukung format OpenAI.

---

## Daftar Isi

- [Cara Kerja](#cara-kerja)
- [Struktur Proyek](#struktur-proyek)
- [Instalasi](#instalasi)
- [Konfigurasi Cookie](#konfigurasi-cookie)
- [Menjalankan Server](#menjalankan-server)
- [Referensi CLI](#referensi-cli)
- [Endpoint API](#endpoint-api)
- [Session & Continue Mode](#session--continue-mode)
- [Cookie Rotation & BrowserPool](#cookie-rotation--browserpool)
- [Panduan Client](#panduan-client)
  - [curl](#1-curl)
  - [Python requests](#2-python-requests)
  - [OpenAI Python SDK](#3-openai-python-sdk)
  - [Streaming](#4-streaming)
  - [Percakapan Continue Mode](#5-percakapan-continue-mode)
  - [Async httpx](#6-async-httpx)
- [Format Request & Response](#format-request--response)
- [Penanganan Error](#penanganan-error)
- [Tips & Catatan](#tips--catatan)

---

## Cara Kerja

### Arsitektur Distributed Worker (public.py)

```
Client (OpenAI SDK / curl / requests)
        │
        │  POST /v1/chat/completions
        │  Header: X-Session-ID: <id>  ← opsional, untuk continue mode
        ▼
  ┌──────────────────────────────────────┐
  │           vps_server.py              │  FastAPI + uvicorn (di VPS)
  │                                      │
  │  SessionRouter                       │
  │    session_id → worker_id (sticky)   │  ← CONTINUE dirouting ke worker
  │                                      │     yang sama dengan saat NEW
  └──────────────────┬───────────────────┘
                     │  WebSocket
                     ▼
  ┌──────────────────────────────────────┐
  │           public.py                  │  Local Worker (di mesin lokal)
  │                                      │
  │  SessionStore                        │
  │    session_id → cookie_file (Path)   │  ← cookie dikunci sejak NEW
  │                 conv_url             │
  │                                      │
  │  BrowserPool                         │
  │    Slot #0: account1.json [IDLE]     │
  │    Slot #1: account2.json [IDLE]     │  ← pre-warmed, tidak ada cold-start
  │    Slot #2: account1.json [BUSY]     │
  │    Slot #N: accountN.json [IDLE]     │
  │                                      │
  │  acquire(preferred_cookie=...)       │
  │    mode NEW      → slot idle mana saja
  │    mode CONTINUE → slot dengan cookie │  ← cookie-pinned, tunggu jika busy
  │                    yang sama          │
  └──────────────────────────────────────┘
                     │
                     ▼
               chat.qwen.ai
```

**Mode `new`** — slot idle dipilih otomatis dari pool (prioritas: paling lama idle). Cookie file yang dipakai slot tersebut dikunci ke session baru dan dikembalikan ke client lewat header `X-Session-ID`.

**Mode `continue`** — client mengirim `X-Session-ID`, worker mencari session yang tersimpan, lalu pool **hanya** memilih slot dengan cookie file yang sama dengan saat NEW pertama kali. Browser di slot tersebut kemudian navigate ke `conversation_url` yang tersimpan sebelum mengirim prompt.

---

## Struktur Proyek

```
project/
├── vps_server.py              ← Server VPS (entry point di VPS)
├── public.py                  ← Local Worker (entry point di mesin lokal)
├── browser_pool.py            ← BrowserPool – manajemen slot browser
├── main.py                    ← CLI scraper standalone (terpisah)
├── config.py                  ← Konfigurasi global
├── requirements.txt
├── cookies/                   ← Letakkan file cookie di sini
│   ├── account1.json
│   └── account2.json
├── output/                    ← Hasil scrape
├── logs/                      ← Log file
└── scrapers/
    ├── base_scraper.py
    ├── qwen_scraper.py
    └── utils.py
```

---

## Instalasi

```bash
pip install -r requirements.txt
playwright install chromium
```

---

## Konfigurasi Cookie

### Cara export cookie

1. Login ke [chat.qwen.ai](https://chat.qwen.ai) di browser
2. Install ekstensi **Cookie-Editor** (Chrome/Firefox)
3. Klik ekstensi → **Export** → **Export as JSON**
4. Simpan file ke folder `cookies/`, misal `account1.json`

### Multi-akun

```
cookies/
├── account1.json
├── account2.json
└── account3.json
```

Setiap slot di BrowserPool mendapat **satu cookie file secara dedicated**. Jika jumlah cookie file lebih sedikit dari `--workers`, cookie di-wrap secara round-robin (misal 3 cookie + 6 worker = 2 slot per akun).

Request **mode `continue`** selalu menggunakan cookie file yang sama dengan sesi pertama — pool akan menunggu slot dengan cookie tersebut idle, tidak akan berpindah ke akun lain.

---

## Menjalankan Server

### VPS Server

```bash
# Di VPS — jalankan server penerima request
python vps_server.py --port 9000 --token YOUR_SECRET_TOKEN

# Akses publik
python vps_server.py --host 0.0.0.0 --port 9000 --token YOUR_SECRET_TOKEN
```

### Local Worker

```bash
# Di mesin lokal — jalankan worker yang konek ke VPS
python public.py --vps ws://YOUR_VPS_IP:9000/ws/worker --workers 20 --token YOUR_SECRET_TOKEN

# Tampilkan jendela browser (debugging)
python public.py --vps ws://... --workers 4 --no-headless

# Session TTL 2 jam
python public.py --vps ws://... --workers 20 --session-ttl 7200

# Override think mode default
python public.py --vps ws://... --workers 20 --think-mode fast
```

> Token harus sama antara `vps_server.py` dan `public.py`.

### Verifikasi

```bash
curl http://YOUR_VPS_IP:9000/health
# {"status": "ok", "timestamp": 1748000000}
```

---

## Referensi CLI

### vps_server.py

| Argumen | Default | Keterangan |
|---|---|---|
| `--host` | `127.0.0.1` | Host/IP yang di-bind |
| `--port` | `9000` | Port yang digunakan |
| `--token` | `None` | Token autentikasi worker |
| `--log-level` | `info` | `debug/info/warning/error` |

### public.py (Local Worker)

| Argumen | Default | Keterangan |
|---|---|---|
| `--vps` | *(wajib)* | WebSocket URL VPS, contoh: `ws://1.2.3.4:9000/ws/worker` |
| `--token` | `None` | Token autentikasi (harus sama dengan VPS) |
| `--workers` | `4` | Jumlah slot browser di BrowserPool |
| `--no-headless` | `False` | Tampilkan jendela browser |
| `--cookies-dir` | `./cookies` | Folder file cookie JSON |
| `--session-ttl` | `3600` | Session TTL dalam detik |
| `--reconnect-delay` | `5.0` | Jeda sebelum reconnect ke VPS (detik) |
| `--think-mode` | dari config | Default think mode: `auto`, `thinking`, atau `fast` |

---

## Endpoint API

### `GET /`
Info server, statistik pool, dan jumlah sesi aktif.

### `GET /health`
Health check minimal.

### `GET /v1/models`
Daftar model yang tersedia (format OpenAI).

### `GET /v1/sessions`
Daftar semua sesi continue-mode yang aktif.

```bash
curl http://YOUR_VPS_IP:9000/v1/sessions
```

```json
{
  "object": "list",
  "count": 2,
  "data": [
    {
      "session_id": "a1b2c3d4...",
      "cookie_file": "account1.json",
      "conversation_url": "https://chat.qwen.ai/c/abc123",
      "created_at": "2025-01-01T10:00:00",
      "last_used": "2025-01-01T10:05:00",
      "turn_count": 3
    }
  ]
}
```

### `DELETE /v1/sessions/{session_id}`
Hapus sesi. Request berikutnya dengan ID ini akan memulai percakapan baru.

```bash
curl -X DELETE http://YOUR_VPS_IP:9000/v1/sessions/a1b2c3d4...
```

### `POST /v1/chat/completions`
Endpoint utama chat — lihat detail di bawah.

---

## Session & Continue Mode

Setiap percakapan memiliki **session** yang menyimpan:
- `session_id` — pengenal unik sesi
- `cookie_file` — `Path` lengkap ke cookie file yang digunakan (dikunci sejak request NEW pertama)
- `conversation_url` — URL percakapan Qwen yang aktif

### Alur lengkap

**Request pertama (mode new — tanpa `X-Session-ID`):**

```
Client → POST /v1/chat/completions
         (tanpa X-Session-ID)
              ↓
         VPS terima request → kirim ke worker via WebSocket
         Worker: BrowserPool.acquire() → pilih slot idle mana saja
         Slot #1 (account2.json) dipilih
         Browser sudah warm → langsung kirim prompt (tanpa cold-start)
         Simpan URL: chat.qwen.ai/c/xyz789
         Buat session: {cookie_file: account2.json, conv_url: ...}
              ↓
Client ← Response + Headers:
         X-Session-ID: abc...
         X-Cookie-File: account2.json
         X-Conversation-URL: https://chat.qwen.ai/c/xyz789
```

**Request berikutnya (mode continue — dengan `X-Session-ID`):**

```
Client → POST /v1/chat/completions
         Header: X-Session-ID: abc...
              ↓
         VPS: session "abc..." terikat ke worker#1 → routing ke worker#1
         Worker#1: lookup session "abc..." → cookie_file = account2.json
         BrowserPool.acquire(preferred_cookie="account2.json")
           → tunggu slot dengan account2.json idle (tidak ambil slot lain)
         Slot idle ditemukan → goto(chat.qwen.ai/c/xyz789)
         Kirim prompt → tunggu respons
              ↓
Client ← Response + Headers:
         X-Session-ID: abc...          (sama)
         X-Cookie-File: account2.json  (sama)
         X-Conversation-URL: ...       (sama atau diperbarui)
```

### Session TTL

Sesi kedaluwarsa otomatis setelah `--session-ttl` detik tidak digunakan (default 1 jam). Jika ID yang dikirim sudah expired, server otomatis membuat sesi baru.

---

## Cookie Rotation & BrowserPool

### Perbedaan dari versi lama

Versi lama menggunakan `CookieRotator` yang spawn browser baru per task (cold-start ~5–15 detik). Versi ini menggunakan **BrowserPool** dengan slot browser yang sudah warm sejak startup:

| | Versi lama | BrowserPool |
|---|---|---|
| Browser launch | Setiap task | Sekali saat startup |
| Cold-start per request | ~5–15 detik | ~0 detik |
| Konsistensi akun CONTINUE | ❌ Bisa salah slot | ✅ Cookie-pinned |
| Respawn otomatis | ❌ | ✅ (maks 3x per slot) |

### Distribusi cookie ke slot

```
# 3 cookie file, 6 workers (wrap round-robin):
Slot #0 → account1.json  ┐
Slot #1 → account2.json  │ dedicated per slot
Slot #2 → account3.json  │
Slot #3 → account1.json  ┘ wrap
Slot #4 → account2.json
Slot #5 → account3.json

# Request CONTINUE ke session yang pakai account2.json
# → pool menunggu Slot #1 atau Slot #4 idle
# → tidak akan diberikan ke Slot #0 (account1.json)
```

### Status slot

Setiap slot memiliki status: `STARTING → IDLE → BUSY → DEAD`. Slot yang crash otomatis masuk ke antrian respawn. Status pool ter-log setiap 60 detik:

```
Pool status: total=20 idle=17 busy=3 dead=0 starting=0
```

---

## Panduan Client

### 1. curl

**Mode new (pertama kali):**

```bash
curl http://YOUR_VPS_IP:9000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -D - \
  -d '{"model":"qwen","messages":[{"role":"user","content":"Apa itu asyncio?"}]}'
```

Catat `X-Session-ID` dari response header, lalu:

**Mode continue:**

```bash
curl http://YOUR_VPS_IP:9000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-Session-ID: a1b2c3d4..." \
  -d '{"model":"qwen","messages":[{"role":"user","content":"Berikan contoh kode."}]}'
```

---

### 2. Python requests

```python
import requests

BASE = "http://YOUR_VPS_IP:9000"
session_id = None

def chat(prompt: str) -> str:
    global session_id
    headers = {}
    if session_id:
        headers["X-Session-ID"] = session_id

    r = requests.post(
        f"{BASE}/v1/chat/completions",
        headers=headers,
        json={"model": "qwen", "messages": [{"role": "user", "content": prompt}]},
        timeout=120,
    )
    r.raise_for_status()
    session_id = r.headers.get("X-Session-ID", session_id)
    return r.json()["choices"][0]["message"]["content"]

print(chat("Jelaskan list comprehension."))
print(chat("Dan generator expression?"))   # continue mode otomatis
```

---

### 3. OpenAI Python SDK

Gunakan `x_meta.session_id` dari body response untuk membaca session tanpa akses langsung ke response headers:

```python
import requests

BASE = "http://YOUR_VPS_IP:9000"
session_id = None

def chat(prompt: str) -> str:
    global session_id
    headers = {}
    if session_id:
        headers["X-Session-ID"] = session_id

    r = requests.post(
        f"{BASE}/v1/chat/completions",
        headers=headers,
        json={"model": "qwen", "messages": [{"role": "user", "content": prompt}]},
        timeout=120,
    )
    r.raise_for_status()
    data = r.json()

    # Ambil session dari body (x_meta) atau header
    session_id = data.get("x_meta", {}).get("session_id") or \
                 r.headers.get("X-Session-ID", session_id)
    return data["choices"][0]["message"]["content"]

print(chat("Apa itu context manager?"))
print(chat("Contoh custom context manager?"))
```

---

### 4. Streaming

```python
import json, requests

BASE = "http://YOUR_VPS_IP:9000"
session_id = None

def chat_stream(prompt: str) -> str:
    global session_id
    headers = {}
    if session_id:
        headers["X-Session-ID"] = session_id

    full_text = ""
    with requests.post(
        f"{BASE}/v1/chat/completions",
        headers=headers,
        json={"model": "qwen", "stream": True,
              "messages": [{"role": "user", "content": prompt}]},
        stream=True, timeout=180,
    ) as resp:
        resp.raise_for_status()
        session_id = resp.headers.get("X-Session-ID", session_id)
        for raw in resp.iter_lines():
            if not raw:
                continue
            line = raw.decode() if isinstance(raw, bytes) else raw
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            delta = json.loads(line[6:])["choices"][0]["delta"].get("content", "")
            print(delta, end="", flush=True)
            full_text += delta
    print()
    return full_text

print(chat_stream("Jelaskan asyncio event loop."))
print(chat_stream("Contoh kode sederhana?"))   # continue
```

---

### 5. Percakapan Continue Mode

Contoh kelas wrapper lengkap:

```python
import requests

class QwenChat:
    def __init__(self, base_url="http://YOUR_VPS_IP:9000"):
        self.base_url = base_url
        self.session_id = None
        self.cookie_file = None
        self.conv_url = None

    def send(self, prompt: str) -> str:
        headers = {}
        if self.session_id:
            headers["X-Session-ID"] = self.session_id

        r = requests.post(
            f"{self.base_url}/v1/chat/completions",
            headers=headers,
            json={"model": "qwen", "messages": [{"role": "user", "content": prompt}]},
            timeout=180,
        )
        r.raise_for_status()

        self.session_id = r.headers.get("X-Session-ID", self.session_id)
        self.cookie_file = r.headers.get("X-Cookie-File", self.cookie_file)
        self.conv_url = r.headers.get("X-Conversation-URL", self.conv_url)
        return r.json()["choices"][0]["message"]["content"]

    def info(self):
        print(f"Session  : {self.session_id}")
        print(f"Cookie   : {self.cookie_file}")
        print(f"Conv URL : {self.conv_url}")

    def reset(self):
        """Hapus sesi, mulai percakapan baru."""
        if self.session_id:
            requests.delete(f"{self.base_url}/v1/sessions/{self.session_id}")
        self.session_id = None
        self.cookie_file = None
        self.conv_url = None

# Penggunaan
qwen = QwenChat()

print(qwen.send("Apa itu design pattern?"))
qwen.info()

print(qwen.send("Jelaskan Singleton."))      # continue
print(qwen.send("Contoh di Python."))        # continue

qwen.reset()
print(qwen.send("Apa itu Docker?"))          # new session
qwen.info()                                  # cookie & URL baru
```

---

### 6. Async httpx

```python
import asyncio, httpx

BASE = "http://YOUR_VPS_IP:9000"

async def ask_qwen(prompt: str, session_id: str | None = None) -> tuple[str, str]:
    headers = {}
    if session_id:
        headers["X-Session-ID"] = session_id

    async with httpx.AsyncClient(timeout=180) as client:
        r = await client.post(
            f"{BASE}/v1/chat/completions",
            headers=headers,
            json={"model": "qwen", "messages": [{"role": "user", "content": prompt}]},
        )
        r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"], r.headers.get("X-Session-ID", "")

async def main():
    reply1, sid = await ask_qwen("Apa itu Rust?")
    print(f"[{sid[:8]}] {reply1[:200]}")

    reply2, sid = await ask_qwen("Mengapa memory-safe?", session_id=sid)
    print(f"[{sid[:8]}] {reply2[:200]}")

asyncio.run(main())
```

---

## Format Request & Response

### Request headers

| Header | Keterangan |
|---|---|
| `Content-Type: application/json` | Wajib |
| `X-Session-ID` | Opsional — kirim untuk mode continue |

### Request body

```json
{
  "model": "qwen",
  "messages": [
    {"role": "system", "content": "Instruksi sistem (opsional)"},
    {"role": "user",   "content": "Pertanyaan kamu di sini"}
  ],
  "stream": false
}
```

### Response headers

| Header | Keterangan |
|---|---|
| `X-Session-ID` | **Simpan ini** — kirim kembali untuk continue mode |
| `X-Cookie-File` | Nama cookie file yang dikunci ke sesi ini |
| `X-Conversation-URL` | URL percakapan Qwen yang aktif |

### Response body (non-streaming)

```json
{
  "id": "chatcmpl-a1b2c3d4e5f6",
  "object": "chat.completion",
  "created": 1748000000,
  "model": "qwen",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "Jawaban dari Qwen..."},
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 12,
    "completion_tokens": 148,
    "total_tokens": 160
  },
  "x_meta": {
    "session_id": "a1b2c3d4...",
    "cookie_file": "account1.json",
    "conversation_url": "https://chat.qwen.ai/c/xyz789",
    "account_used": "account1"
  }
}
```

> `x_meta` adalah ekstensi non-standar — berguna untuk client yang tidak bisa membaca response headers secara langsung (misal beberapa OpenAI SDK wrapper).

---

## Penanganan Error

| HTTP Status | Penyebab | Solusi |
|---|---|---|
| `400` | Tidak ada pesan `user` | Pastikan ada `{"role":"user","content":"..."}` |
| `404` | Session ID tidak ditemukan | Session expired atau ID salah |
| `500` | Error internal | Cek `logs/scraper.log` |
| `502` | Scraper gagal | Cek koneksi dan cookie |
| `503` | Pool belum siap / semua slot dead | Tunggu respawn atau restart worker |
| `504` | Qwen timeout | Coba lagi |

---

## Tips & Catatan

**Session expired** — Jika session ID tidak ditemukan, server otomatis membuat sesi baru dan mengembalikan `X-Session-ID` baru. Client perlu memperbarui ID yang disimpan.

**Cookie-pinned untuk CONTINUE** — Setiap sesi dikunci ke satu cookie file. Pool menjamin slot dengan cookie yang tepat dipakai untuk request CONTINUE — tidak akan berpindah akun di tengah percakapan.

**Startup lebih lama, request lebih cepat** — BrowserPool warm-up semua browser saat `public.py` pertama dijalankan. Ini butuh beberapa detik (tergantung `--workers`), tapi setelah itu setiap request tidak ada cold-start sama sekali.

**Respawn otomatis** — Slot yang crash akan di-respawn otomatis di background (maks 3 percobaan). Selama respawn berlangsung, slot tersebut tidak tersedia untuk request baru.

**`x_meta` dalam response body** — Berisi `session_id`, `cookie_file`, dan `conversation_url` di dalam body JSON — alternatif bagi client yang tidak bisa membaca response headers.

**RAM per slot** — Setiap slot browser memakan ~200–400 MB RAM. Sesuaikan `--workers` dengan kapasitas mesin lokal yang menjalankan `public.py`.

**Reconnect otomatis** — Jika koneksi WebSocket antara `public.py` dan VPS terputus, worker akan reconnect otomatis setiap `--reconnect-delay` detik (default 5 detik) tanpa perlu restart manual.