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
- [Cookie Rotation](#cookie-rotation)
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

```
Client (OpenAI SDK / curl / requests)
        │
        │  POST /v1/chat/completions
        │  Header: X-Session-ID: <id>  ← opsional, untuk continue mode
        ▼
  ┌──────────────────────────────┐
  │       api_server.py          │  FastAPI + uvicorn
  │                              │
  │  SessionStore                │  ← menyimpan session_id → cookie_file
  │    session_id                │                           + conv_url
  │    cookie_file  ─────────────┼─→ mode: continue (cookie & URL terkunci)
  │    conversation_url          │
  │                              │
  │  CookieRotator               │  ← round-robin, hanya untuk mode: new
  │    account1.json             │
  │    account2.json  ───────────┼─→ mode: new (pilih cookie berikutnya)
  │    account3.json             │
  └──────────────┬───────────────┘
                 │  ScraperPool (semaphore)
                 ▼
  ┌──────────────────────────────┐
  │  QwenScraper (Playwright)    │  ← Chromium headless
  │  load_cookies(cookie_file)   │
  │  goto(conversation_url)?     │  ← hanya jika continue mode
  └──────────────┬───────────────┘
                 │
                 ▼
           chat.qwen.ai
```

**Mode `new`** — cookie dipilih otomatis oleh rotator (round-robin), conversation URL baru dibuat, session baru dikembalikan ke client lewat header `X-Session-ID`.

**Mode `continue`** — client mengirim `X-Session-ID`, server mencari session yang tersimpan, mengunci ke cookie file yang sama, dan menavigasi browser ke conversation URL percakapan sebelumnya.

---

## Struktur Proyek

```
project/
├── api_server.py              ← Server utama (entry point)
├── main.py                    ← CLI scraper (terpisah)
├── config.py                  ← Konfigurasi global
├── requirements_api.txt       ← Dependencies
├── cookies/                   ← Letakkan file cookie di sini
│   ├── account1.json
│   └── account2.json
├── output/                    ← Hasil scrape
├── logs/                      ← Log file
├── examples/
│   ├── chat_cli.py            ← CLI chatbot interaktif
│   └── client_examples.py    ← Contoh berbagai client
└── scrapers/
    ├── base_scraper.py
    ├── qwen_scraper.py
    └── utils.py
```

---

## Instalasi

```bash
pip install -r requirements_api.txt
playwright install chromium
```

---

## Konfigurasi Cookie

### Cara export cookie

1. Login ke [chat.qwen.ai](https://chat.qwen.ai) di browser
2. Install ekstensi **Cookie-Editor** (Chrome/Firefox)
3. Klik ekstensi → **Export** → **Export as JSON**
4. Simpan file ke folder `cookies/`, misal `account1.json`

### Multi-akun (untuk cookie rotation)

```
cookies/
├── account1.json
├── account2.json
└── account3.json
```

Rotasi berjalan **round-robin** dan hanya aktif untuk request **mode `new`**. Request **mode `continue`** selalu menggunakan cookie file yang sama dengan sesi pertama.

---

## Menjalankan Server

```bash
# Default (localhost:8000, 1 worker)
python api_server.py

# Akses dari jaringan lokal
python api_server.py --host 0.0.0.0 --port 8000

# 3 sesi browser bersamaan
python api_server.py --workers 3

# Tampilkan jendela browser (debugging)
python api_server.py --no-headless

# Session TTL 2 jam (default: 1 jam)
python api_server.py --session-ttl 7200

# Log lebih detail
python api_server.py --log-level debug
```

### Verifikasi

```bash
curl http://127.0.0.1:8000/health
# {"status": "ok", "timestamp": 1748000000}
```

---

## Referensi CLI

| Argumen | Default | Keterangan |
|---|---|---|
| `--host` | `127.0.0.1` | Host/IP yang di-bind |
| `--port` | `8000` | Port yang digunakan |
| `--workers` | `1` | Jumlah sesi browser bersamaan |
| `--no-headless` | `False` | Tampilkan jendela browser |
| `--cookies-dir` | `./cookies` | Folder file cookie JSON |
| `--session-ttl` | `3600` | Detik sebelum sesi idle kedaluwarsa |
| `--reload` | `False` | Auto-reload (mode dev) |
| `--log-level` | `info` | `debug/info/warning/error` |

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
curl http://127.0.0.1:8000/v1/sessions
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
curl -X DELETE http://127.0.0.1:8000/v1/sessions/a1b2c3d4...
```

### `POST /v1/chat/completions`
Endpoint utama chat — lihat detail di bawah.

---

## Session & Continue Mode

Setiap percakapan memiliki **session** yang menyimpan:
- `session_id` — pengenal unik sesi
- `cookie_file` — cookie account yang digunakan (dikunci sejak awal sesi)
- `conversation_url` — URL percakapan Qwen yang aktif

### Alur lengkap

**Request pertama (mode new — tanpa `X-Session-ID`):**

```
Client → POST /v1/chat/completions
         (tanpa X-Session-ID)
              ↓
         CookieRotator pilih: account2.json
         Buka browser baru → chat.qwen.ai
         Kirim prompt → tunggu respons
         Simpan URL: chat.qwen.ai/c/xyz789
         Buat session baru
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
         Cari session "abc..." → ditemukan
         Gunakan: account2.json (dikunci, tidak dirotasi)
         Buka browser → goto(chat.qwen.ai/c/xyz789)
         Kirim prompt → tunggu respons
              ↓
Client ← Response + Headers:
         X-Session-ID: abc...          (sama)
         X-Cookie-File: account2.json  (sama)
         X-Conversation-URL: ...       (sama)
```

### Session TTL

Sesi kedaluwarsa otomatis setelah `--session-ttl` detik tidak digunakan (default 1 jam). Jika ID yang dikirim sudah expired, server otomatis membuat sesi baru.

---

## Cookie Rotation

Rotasi berjalan **round-robin** di antara semua file `.json` di folder `cookies/`:

```
Request 1 (new) → account1.json  → Session A terkunci ke account1.json
Request 2 (new) → account2.json  → Session B terkunci ke account2.json
Request 3 (new) → account3.json  → Session C terkunci ke account3.json
Request 4 (new) → account1.json  → (mulai ulang)

Request 5 (continue, Session A) → account1.json  ← dikunci, tidak dirotasi
Request 6 (continue, Session B) → account2.json  ← dikunci, tidak dirotasi
```

Cek cookie yang tersedia:

```bash
curl http://127.0.0.1:8000/
# response.pool.available_cookies: ["account1.json", "account2.json"]
```

---

## Panduan Client

### 1. curl

**Mode new (pertama kali):**

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -D - \
  -d '{"model":"qwen","messages":[{"role":"user","content":"Apa itu asyncio?"}]}'
```

Catat `X-Session-ID` dari response header, lalu:

**Mode continue:**

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-Session-ID: a1b2c3d4..." \
  -d '{"model":"qwen","messages":[{"role":"user","content":"Berikan contoh kode."}]}'
```

---

### 2. Python requests

```python
import requests

BASE = "http://127.0.0.1:8000"
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

BASE = "http://127.0.0.1:8000"
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

BASE = "http://127.0.0.1:8000"
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
    def __init__(self, base_url="http://127.0.0.1:8000"):
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
print(qwen.send("Contoh di Python."))         # continue

qwen.reset()
print(qwen.send("Apa itu Docker?"))          # new session
qwen.info()                                   # cookie & URL baru
```

---

### 6. Async httpx

```python
import asyncio, httpx

BASE = "http://127.0.0.1:8000"

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
| `503` | Pool belum siap | Tunggu beberapa detik |
| `504` | Qwen timeout | Coba lagi |

---

## Tips & Catatan

**Session expired** — Jika session ID tidak ditemukan, server otomatis membuat sesi baru dan mengembalikan `X-Session-ID` baru. Client perlu memperbarui ID yang disimpan.

**Cookie rotation hanya untuk mode new** — Setiap sesi baru dipasangkan ke satu cookie file secara permanen. Rotasi terjadi di antara sesi-sesi baru, bukan di dalam satu sesi yang sama.

**`x_meta` dalam response body** — Berisi `session_id`, `cookie_file`, dan `conversation_url` di dalam body JSON — alternatif bagi client yang tidak bisa membaca response headers.

**Concurrent workers** — Setiap worker membuka satu instance Chromium (~200–400 MB RAM). Jangan set `--workers` terlalu tinggi.

**Swagger UI** — Tersedia di `http://127.0.0.1:8000/docs` selama server berjalan.

**CLI chatbot** — `examples/chat_cli.py` mengelola session secara otomatis dan menampilkan info cookie + session setelah giliran pertama:

```bash
python examples/chat_cli.py --stream
python examples/chat_cli.py --system "Kamu adalah tutor Python."
```