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
- [Panduan Client](#panduan-client)
  - [curl](#1-curl)
  - [Python requests](#2-python-requests)
  - [OpenAI Python SDK](#3-openai-python-sdk)
  - [Streaming](#4-streaming)
  - [Percakapan Multi-turn](#5-percakapan-multi-turn)
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
        ▼
  ┌─────────────────┐
  │   api_server.py │  ← FastAPI + uvicorn
  │   (port 8000)   │
  └────────┬────────┘
           │  ScraperPool (semaphore)
           ▼
  ┌─────────────────┐
  │  QwenScraper    │  ← Playwright (Chromium headless)
  │  base_scraper   │
  └────────┬────────┘
           │  Browser automation
           ▼
     chat.qwen.ai
```

Server menerima request format OpenAI, menerjemahkannya ke browser automation via Playwright, lalu mengembalikan response dalam format OpenAI yang sama.

---

## Struktur Proyek

```
project/
├── api_server.py              ← Server utama (entry point)
├── main.py                    ← CLI scraper (terpisah dari server)
├── config.py                  ← Konfigurasi global
├── requirements_api.txt       ← Dependencies server
├── cookies/                   ← Letakkan file cookie di sini
│   ├── account1.json
│   └── account2.json
├── output/                    ← Hasil scrape tersimpan di sini
├── logs/                      ← Log file
└── scrapers/
    ├── base_scraper.py
    ├── qwen_scraper.py
    └── utils.py
```

---

## Instalasi

### 1. Clone / salin proyek

```bash
git clone <repo-url>
cd project
```

### 2. Install Python dependencies

```bash
pip install -r requirements_api.txt
```

Isi `requirements_api.txt`:

```
playwright>=1.44.0
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
pydantic>=2.7.0

# Untuk client examples (opsional)
openai>=1.30.0
requests>=2.31.0
httpx>=0.27.0
```

### 3. Install browser Playwright

```bash
playwright install chromium
```

---

## Konfigurasi Cookie

Server memerlukan cookie dari akun Qwen AI yang sudah login agar bisa mengirim prompt.

### Cara export cookie

1. Login ke [chat.qwen.ai](https://chat.qwen.ai) di browser
2. Install ekstensi **Cookie-Editor** (Chrome/Firefox)
3. Klik ekstensi → **Export** → **Export as JSON**
4. Simpan file ke folder `cookies/` dengan nama bebas, misal `account1.json`

### Multi-akun (rotasi otomatis)

Simpan beberapa file cookie di folder `cookies/`:

```
cookies/
├── account1.json
├── account2.json
└── account3.json
```

Server akan otomatis berotasi ke akun berikutnya jika terkena rate limit atau session expired.

---

## Menjalankan Server

### Perintah dasar

```bash
python api_server.py
```

Server berjalan di `http://127.0.0.1:8000` secara default.

### Dengan opsi lengkap

```bash
# Akses dari jaringan lokal (semua interface)
python api_server.py --host 0.0.0.0 --port 8000

# Tampilkan jendela browser (non-headless, untuk debugging)
python api_server.py --no-headless

# Izinkan 3 request bersamaan
python api_server.py --workers 3

# Gunakan folder cookie kustom
python api_server.py --cookies-dir /path/to/cookies

# Log lebih detail
python api_server.py --log-level debug

# Dev mode (auto-reload saat kode berubah)
python api_server.py --reload
```

### Verifikasi server berjalan

```bash
curl http://127.0.0.1:8000/health
```

Response:
```json
{"status": "ok", "timestamp": 1748000000}
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
| `--reload` | `False` | Auto-reload (mode dev) |
| `--log-level` | `info` | Level log: `debug/info/warning/error` |

---

## Endpoint API

### `GET /`
Info server dan statistik pool.

```bash
curl http://127.0.0.1:8000/
```

```json
{
  "status": "ok",
  "service": "AIChatScraper – OpenAI-Compatible API",
  "backend": "Qwen AI (chat.qwen.ai)",
  "pool": {
    "max_workers": 1,
    "active_sessions": 0,
    "total_requests": 42
  }
}
```

---

### `GET /health`
Health check minimal.

```bash
curl http://127.0.0.1:8000/health
```

```json
{"status": "ok", "timestamp": 1748000000}
```

---

### `GET /v1/models`
Daftar model yang tersedia (format OpenAI).

```bash
curl http://127.0.0.1:8000/v1/models
```

```json
{
  "object": "list",
  "data": [
    {"id": "qwen", "object": "model", "owned_by": "qwen-ai"},
    {"id": "qwen-turbo", "object": "model", "owned_by": "qwen-ai"}
  ]
}
```

---

### `POST /v1/chat/completions`
Endpoint utama. Menerima format OpenAI Chat Completions.

**Request body:**

| Field | Tipe | Wajib | Keterangan |
|---|---|---|---|
| `model` | string | ✅ | Gunakan `"qwen"` |
| `messages` | array | ✅ | Array objek `{role, content}` |
| `stream` | boolean | ❌ | `true` untuk streaming SSE |
| `temperature` | float | ❌ | Diterima tapi diabaikan |
| `max_tokens` | int | ❌ | Diterima tapi diabaikan |

**Role yang didukung:**
- `"system"` — instruksi sistem (digabung ke prompt)
- `"user"` — pesan pengguna (prompt yang dikirim)
- `"assistant"` — riwayat jawaban (mengaktifkan mode `continue`)

---

## Panduan Client

### 1. curl

**Non-streaming:**

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen",
    "messages": [
      {"role": "user", "content": "Jelaskan apa itu recursion dalam pemrograman."}
    ]
  }'
```

**Streaming:**

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -N \
  -d '{
    "model": "qwen",
    "stream": true,
    "messages": [
      {"role": "user", "content": "Tulis fungsi Python untuk bubble sort."}
    ]
  }'
```

> `-N` menonaktifkan buffering agar output streaming langsung tampil.

---

### 2. Python `requests`

**Non-streaming:**

```python
import requests

response = requests.post(
    "http://127.0.0.1:8000/v1/chat/completions",
    json={
        "model": "qwen",
        "messages": [
            {"role": "user", "content": "Apa itu decorator di Python?"}
        ],
    },
    timeout=120,
)

data = response.json()
print(data["choices"][0]["message"]["content"])
print("Token digunakan:", data["usage"]["total_tokens"])
```

**Streaming:**

```python
import json
import requests

with requests.post(
    "http://127.0.0.1:8000/v1/chat/completions",
    json={
        "model": "qwen",
        "stream": True,
        "messages": [{"role": "user", "content": "Jelaskan asyncio."}],
    },
    stream=True,
    timeout=180,
) as resp:
    for line in resp.iter_lines():
        if line:
            line = line.decode("utf-8")
            if line.startswith("data: ") and line != "data: [DONE]":
                chunk = json.loads(line[6:])
                delta = chunk["choices"][0]["delta"].get("content", "")
                print(delta, end="", flush=True)
```

---

### 3. OpenAI Python SDK

Install SDK-nya terlebih dahulu:

```bash
pip install openai
```

**Basic:**

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="tidak-diperlukan",   # server tidak memvalidasi API key
)

completion = client.chat.completions.create(
    model="qwen",
    messages=[
        {"role": "system", "content": "Kamu adalah asisten pemrograman yang helpful."},
        {"role": "user",   "content": "Berikan contoh penggunaan context manager di Python."},
    ],
)

print(completion.choices[0].message.content)
print(f"Tokens: {completion.usage.total_tokens}")
```

---

### 4. Streaming

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="tidak-diperlukan",
)

print("Response: ", end="", flush=True)

with client.chat.completions.create(
    model="qwen",
    stream=True,
    messages=[
        {"role": "user", "content": "Tulis class Python untuk stack data structure."},
    ],
) as stream:
    for chunk in stream:
        delta = chunk.choices[0].delta.content or ""
        print(delta, end="", flush=True)

print()  # newline di akhir
```

---

### 5. Percakapan Multi-turn

Server secara otomatis mendeteksi mode percakapan:
- Jika ada pesan `"assistant"` dalam history → mode `continue` (lanjut chat yang sama)
- Jika tidak ada → mode `new` (buka chat baru)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="tidak-diperlukan",
)

# Simpan history percakapan secara manual
messages = [
    {"role": "system", "content": "Kamu adalah tutor Python yang sabar."},
]

def chat(user_input: str) -> str:
    messages.append({"role": "user", "content": user_input})

    response = client.chat.completions.create(
        model="qwen",
        messages=messages,
    )

    reply = response.choices[0].message.content
    messages.append({"role": "assistant", "content": reply})
    return reply

# Simulasi percakapan
print(chat("Apa itu list comprehension?"))
print(chat("Berikan 3 contoh nyatanya."))
print(chat("Bagaimana bedanya dengan generator expression?"))
```

---

### 6. Async `httpx`

```python
import asyncio
import httpx

async def ask_qwen(prompt: str) -> str:
    async with httpx.AsyncClient(timeout=180) as client:
        response = await client.post(
            "http://127.0.0.1:8000/v1/chat/completions",
            json={
                "model": "qwen",
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]

async def main():
    # Kirim beberapa prompt secara bersamaan
    prompts = [
        "Apa itu Python GIL?",
        "Jelaskan perbedaan list dan tuple.",
        "Apa kegunaan __slots__ di Python?",
    ]
    results = await asyncio.gather(*[ask_qwen(p) for p in prompts])
    for prompt, result in zip(prompts, results):
        print(f"Q: {prompt}")
        print(f"A: {result[:200]}\n")

asyncio.run(main())
```

> **Catatan:** Concurrent request dibatasi oleh `--workers` di server. Jika `--workers 1`, request akan diproses satu per satu secara antrian.

---

## Format Request & Response

### Request (POST `/v1/chat/completions`)

```json
{
  "model": "qwen",
  "messages": [
    {"role": "system", "content": "Instruksi sistem (opsional)"},
    {"role": "user",   "content": "Pertanyaan atau prompt kamu di sini"}
  ],
  "stream": false
}
```

### Response (non-streaming)

```json
{
  "id": "chatcmpl-a1b2c3d4e5f6",
  "object": "chat.completion",
  "created": 1748000000,
  "model": "qwen",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Jawaban dari Qwen AI di sini..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 12,
    "completion_tokens": 148,
    "total_tokens": 160
  }
}
```

> Token dihitung dengan estimasi kasar (~4 karakter per token), bukan tokenizer asli.

### Response (streaming)

Server mengirim Server-Sent Events (SSE):

```
data: {"id":"chatcmpl-...","choices":[{"delta":{"role":"assistant","content":""},...}]}

data: {"id":"chatcmpl-...","choices":[{"delta":{"content":"Jawaban"},...}]}

data: {"id":"chatcmpl-...","choices":[{"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

---

## Penanganan Error

| HTTP Status | Penyebab | Solusi |
|---|---|---|
| `400` | Tidak ada pesan `user` dalam `messages` | Pastikan ada minimal satu `{"role":"user","content":"..."}` |
| `500` | Error internal server | Cek log di `logs/scraper.log` |
| `502` | Scraper gagal (Qwen tidak merespons) | Cek koneksi internet dan status cookie |
| `503` | Pool belum siap | Tunggu beberapa detik, server mungkin baru start |
| `504` | Qwen tidak merespons dalam batas waktu | Coba lagi; Qwen mungkin lambat saat ini |

**Contoh error response:**

```json
{
  "error": {
    "message": "Scraper error: All attempts exhausted",
    "type": "internal_server_error",
    "code": 502
  }
}
```

---

## Tips & Catatan

**Cookie expired** — Jika server terus mengembalikan 502, kemungkinan cookie sudah expired. Export ulang cookie dari browser dan ganti file di folder `cookies/`.

**Timeout panjang** — Qwen AI bisa butuh waktu lama untuk menjawab prompt yang kompleks. Default timeout adalah 5 menit. Sesuaikan `timeout` di sisi client jika perlu.

**Concurrent workers** — Setiap worker membuka satu instance browser Chromium. Jangan set `--workers` terlalu tinggi karena setiap browser mengkonsumsi RAM (~200–400 MB per instance).

**Swagger UI** — Dokumentasi interaktif tersedia otomatis di `http://127.0.0.1:8000/docs` selama server berjalan.

**`api_key` tidak divalidasi** — Server menerima nilai apapun untuk field `api_key`. Isi string sembarang agar SDK tidak error.

**Mode `system`** — Pesan dengan role `"system"` diterima oleh server, namun saat ini tidak diteruskan secara terpisah ke Qwen — hanya pesan `user` terakhir yang dikirim sebagai prompt aktif.
