# Panduan Mengakses AIChatScraper API

Dokumen ini ditujukan untuk **pengguna API** — Anda tidak perlu mengetahui cara kerja server atau instalasi apapun. Cukup baca panduan ini untuk mulai mengirim request dan mendapatkan respons dari Qwen AI.

---

## Daftar Isi

- [Base URL](#base-url)
- [Autentikasi](#autentikasi)
- [Cara Kerja Singkat](#cara-kerja-singkat)
- [Endpoint](#endpoint)
- [Mengirim Chat (Endpoint Utama)](#mengirim-chat-endpoint-utama)
- [Memilih Akun (Model Selector)](#memilih-akun-model-selector)
- [Session & Percakapan Berkelanjutan](#session--percakapan-berkelanjutan)
- [Think Mode](#think-mode)
- [Mengirim File / Attachment](#mengirim-file--attachment)
- [Generate Gambar (Create Image)](#generate-gambar-create-image)
- [Generate Video (Create Video)](#generate-video-create-video)
- [Pencarian Web (Web Search)](#pencarian-web-web-search)
- [Tool Calling (Function Calling)](#tool-calling-function-calling)
- [Contoh Kode](#contoh-kode)
  - [curl](#curl)
  - [Python (requests)](#python-requests)
  - [Python (httpx async)](#python-httpx-async)
  - [JavaScript (fetch)](#javascript-fetch)
  - [OpenAI SDK](#openai-sdk)
- [Referensi Lengkap Request & Response](#referensi-lengkap-request--response)
- [Kode Error](#kode-error)
- [Tips Praktis](#tips-praktis)

---

## Base URL

```
http://16.79.2.204:9000
```

Semua endpoint di bawah ini menggunakan base URL tersebut sebagai awalan.

---

## Autentikasi

API ini **tidak memerlukan API key** dari sisi client. Tidak ada header `Authorization` yang perlu dikirim.

Jika server dikonfigurasi dengan token internal (antara VPS dan worker), itu diurus oleh operator — bukan urusan Anda sebagai pengguna API.

---

## Cara Kerja Singkat

```
Anda                          Server
 │                               │
 │  POST /v1/chat/completions    │
 │  (tanpa X-Session-ID)         │
 │ ──────────────────────────── ▶│
 │                               │  → Qwen AI memproses
 │ ◀──────────────────────────── │
 │  Response + X-Session-ID      │  ← simpan ID ini
 │                               │
 │  POST /v1/chat/completions    │
 │  Header: X-Session-ID: abc    │  ← kirim ID untuk lanjutkan
 │ ──────────────────────────── ▶│
 │                               │  → Qwen melanjutkan percakapan
 │ ◀──────────────────────────── │
 │  Response (konteks tersimpan) │
```

Setiap percakapan baru **tidak perlu header apapun**. Untuk melanjutkan percakapan yang sama, cukup sertakan `X-Session-ID` dari response sebelumnya.

---

## Endpoint

### `GET /health`

Cek apakah server sedang berjalan.

```bash
curl http://16.79.2.204:9000/health
```

**Response:**
```json
{"status": "ok", "timestamp": 1748000000}
```

---

### `GET /v1/models`

Daftar akun (cookie) yang tersedia di worker. Setiap `id` bisa dipakai sebagai nilai field `model` di request untuk memilih akun tertentu. Listing ini **dinamis** — otomatis sinkron dengan cookie yang aktif di worker, tidak perlu konfigurasi manual.

```bash
curl http://16.79.2.204:9000/v1/models
```

**Response (contoh — bergantung pada cookie yang terdaftar di worker):**
```json
{
  "object": "list",
  "data": [
    {"id": "account1", "object": "model", "owned_by": "qwen-ai"},
    {"id": "account2", "object": "model", "owned_by": "qwen-ai"},
    {"id": "account6", "object": "model", "owned_by": "qwen-ai"}
  ]
}
```

---

### `GET /v1/sessions`

Lihat semua sesi aktif yang sedang tersimpan di server.

```bash
curl http://16.79.2.204:9000/v1/sessions
```

**Response:**
```json
{
  "object": "list",
  "count": 1,
  "data": [
    {
      "session_id": "a1b2c3d4e5f6...",
      "cookie_file": "account2.json",
      "conversation_url": "https://chat.qwen.ai/c/xyz789",
      "created_at": "2025-05-04T10:00:00",
      "last_used": "2025-05-04T10:05:00",
      "turn_count": 3
    }
  ]
}
```

---

### `DELETE /v1/sessions/{session_id}`

Hapus sesi secara manual. Request berikutnya dengan ID ini akan dianggap percakapan baru.

```bash
curl -X DELETE http://16.79.2.204:9000/v1/sessions/a1b2c3d4e5f6...
```

---

### `POST /v1/chat/completions`

**Endpoint utama.** Kirim pesan dan dapatkan respons dari Qwen AI.

---

## Mengirim Chat (Endpoint Utama)

### Request Minimal

```bash
curl http://16.79.2.204:9000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "account1",
    "messages": [
      {"role": "user", "content": "Jelaskan apa itu recursion."}
    ]
  }'
```

### Request Headers

| Header | Wajib | Keterangan |
|---|---|---|
| `Content-Type: application/json` | ✅ Ya | Selalu sertakan |
| `X-Session-ID: <id>` | ❌ Opsional | Kirim untuk melanjutkan percakapan sebelumnya |

### Request Body

| Field | Tipe | Wajib | Keterangan |
|---|---|---|---|
| `model` | string | ✅ | Nama akun yang ingin dipakai, misal `"account1"`. Lihat [Memilih Akun](#memilih-akun-model-selector) |
| `messages` | array | ✅ | Minimal satu objek `{"role": "user", "content": "..."}` |
| `stream` | boolean | ❌ | `true` untuk streaming SSE, default `false` |
| `think_mode` | string | ❌ | `"auto"`, `"thinking"`, atau `"fast"` |
| `attachments` | array | ❌ | Daftar file yang akan diupload ke Qwen |
| `task_type` | string | ❌ | Mode task khusus. Kosongkan untuk chat biasa |

**Nilai `role` yang diterima:** `"user"`, `"assistant"`, `"system"`

> Hanya pesan `"user"` terakhir yang dikirim ke Qwen. Pengelolaan riwayat percakapan dikelola oleh session di sisi server.

### Response Headers

| Header | Keterangan |
|---|---|
| `X-Session-ID` | **Simpan ini.** Kirim kembali di request berikutnya untuk melanjutkan percakapan |
| `X-Cookie-File` | Nama akun yang digunakan untuk sesi ini (informasi saja) |
| `X-Conversation-URL` | URL percakapan Qwen yang aktif (informasi saja) |

### Response Body

```json
{
  "id": "chatcmpl-a1b2c3d4e5f6",
  "object": "chat.completion",
  "created": 1748000000,
  "model": "account1",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Recursion adalah teknik pemrograman di mana..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 12,
    "completion_tokens": 148,
    "total_tokens": 160
  },
  "x_meta": {
    "session_id": "a1b2c3d4e5f6...",
    "cookie_file": "account1.json",
    "conversation_url": "https://chat.qwen.ai/c/xyz789",
    "account_used": "account1"
  }
}
```

Respons Qwen ada di: `choices[0].message.content`

`x_meta` berisi `session_id` di dalam body — berguna jika library Anda tidak bisa membaca response headers secara langsung.

---

## Memilih Akun (Model Selector)

Field `model` di request body berfungsi sebagai **selector akun**. Setiap akun di worker memiliki file cookie sendiri (misal `account1.json`, `account6.json`). Dengan menentukan nama akun di `model`, request Anda akan selalu diproses menggunakan akun tersebut.

### Cara Melihat Akun yang Tersedia

```bash
curl http://16.79.2.204:9000/v1/models
```

Listing ini dinamis — otomatis mencerminkan cookie yang sedang aktif di worker.

### Cara Menggunakan Akun Tertentu

```bash
# Gunakan account1
curl http://16.79.2.204:9000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "account1", "messages": [{"role": "user", "content": "Halo!"}]}'

# Gunakan account6
curl http://16.79.2.204:9000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "account6", "messages": [{"role": "user", "content": "Halo!"}]}'
```

### Perilaku Pemilihan Akun

| Nilai `model` | Perilaku |
|---|---|
| Nama akun spesifik (`"account1"`, `"account6"`, dst.) | Worker memilih slot browser dengan cookie file yang sesuai |
| `"qwen"` (generic) | Worker memilih slot idle mana saja (load balance otomatis) |

> **Akun terikat ke session:** Setelah turn pertama, akun yang dipakai sudah dikunci ke session. Mengganti `model` di turn berikutnya tidak akan mengubah akun — gunakan session baru untuk berganti akun.

### Contoh Python — Pilih Akun Spesifik

```python
import requests

BASE_URL = "http://16.79.2.204:9000"

def list_accounts() -> list[str]:
    """Ambil daftar akun yang tersedia dari server."""
    r = requests.get(f"{BASE_URL}/v1/models", timeout=10)
    r.raise_for_status()
    return [m["id"] for m in r.json()["data"]]

def chat(prompt: str, account: str, session_id: str = None) -> tuple[str, str]:
    headers = {"Content-Type": "application/json"}
    if session_id:
        headers["X-Session-ID"] = session_id

    r = requests.post(
        f"{BASE_URL}/v1/chat/completions",
        headers=headers,
        json={"model": account, "messages": [{"role": "user", "content": prompt}]},
        timeout=180,
    )
    r.raise_for_status()
    data = r.json()
    new_sid = r.headers.get("X-Session-ID") or data.get("x_meta", {}).get("session_id") or session_id
    return data["choices"][0]["message"]["content"], new_sid


# Lihat akun yang tersedia
accounts = list_accounts()
print("Akun tersedia:", accounts)
# Output: ['account1', 'account2', 'account6']

# Pakai account6 secara spesifik
reply, sid = chat("Apa itu binary search?", account="account6")
print(f"[account6] {reply}")

# Lanjutkan percakapan — akun sudah terikat ke session
reply2, sid = chat("Beri contoh kodenya.", account="account6", session_id=sid)
print(f"[lanjut] {reply2}")
```

---

## Session & Percakapan Berkelanjutan

### Konsep

Secara default, setiap request adalah **percakapan baru**. Untuk membuat percakapan multi-turn, simpan `X-Session-ID` dari response pertama dan kirimkan kembali di request berikutnya.

Server menyimpan akun mana yang dipakai dan URL percakapan Qwen yang aktif, sehingga Qwen "ingat" konteks percakapan sebelumnya.

### Alur Lengkap

**Turn pertama — tidak ada `X-Session-ID`:**

```
POST /v1/chat/completions
Body: {"model":"account1","messages":[{"role":"user","content":"Apa itu OOP?"}]}

Response:
  Header: X-Session-ID: abc123def456...
  Body: {"choices":[{"message":{"content":"OOP adalah..."}}], "x_meta":{"session_id":"abc123..."}}
```

**Turn kedua — sertakan `X-Session-ID`:**

```
POST /v1/chat/completions
Header: X-Session-ID: abc123def456...
Body: {"model":"account1","messages":[{"role":"user","content":"Jelaskan inheritance-nya."}]}

Response:
  Header: X-Session-ID: abc123def456...   ← sama
  Body: {"choices":[{"message":{"content":"Inheritance adalah..."}}]}
  ← Qwen menjawab dengan konteks "kita sedang membahas OOP"
```

### Session Expired

Sesi otomatis kedaluwarsa setelah **1 jam tidak digunakan**. Jika `X-Session-ID` sudah expired, server akan membuat sesi baru secara otomatis — Anda akan menerima `X-Session-ID` baru di response.

### Menghapus Sesi Manual

```bash
curl -X DELETE http://16.79.2.204:9000/v1/sessions/abc123def456...
```

---

## Think Mode

| Mode | Keterangan | Cocok untuk |
|---|---|---|
| `"fast"` | Cepat, tanpa reasoning panjang (default) | Pertanyaan umum, percakapan ringan |
| `"auto"` | Qwen memilih sendiri sesuai kompleksitas | Penggunaan umum |
| `"thinking"` | Reasoning mendalam, lebih lambat tapi akurat | Matematika, logika, analisis kompleks |

```json
{
  "model": "account1",
  "messages": [{"role": "user", "content": "Buktikan bahwa sqrt(2) adalah bilangan irasional."}],
  "think_mode": "thinking"
}
```

> Think mode hanya bisa diatur pada **turn pertama**. Turn lanjutan mengikuti mode awal.

---

## Mengirim File / Attachment

### Format Attachment

| Field | Tipe | Wajib | Keterangan |
|---|---|---|---|
| `filename` | string | ✅ | Nama file asli, misal `"foto.jpg"` |
| `data` | string | ✅ | Konten file dalam format **base64** (raw atau Data URI) |
| `mime_type` | string | ❌ | MIME type. Jika tidak diisi, di-guess dari `filename` |

### Tipe File yang Didukung

| Kategori | Contoh Format |
|---|---|
| Gambar | `image/jpeg`, `image/png`, `image/webp`, `image/gif` |
| Dokumen | `application/pdf`, `application/msword`, `.docx` |
| Spreadsheet | `application/vnd.ms-excel`, `.xlsx` |
| Teks | `text/plain`, `text/csv`, `text/html`, `application/json` |
| Audio | `audio/mpeg`, `audio/wav` |
| Video | `video/mp4`, `video/webm` |

### Contoh curl — Kirim Gambar

```bash
B64=$(base64 -w 0 foto.jpg)

curl http://16.79.2.204:9000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"account1\",
    \"messages\": [{\"role\": \"user\", \"content\": \"Apa yang ada di gambar ini?\"}],
    \"attachments\": [
      {\"filename\": \"foto.jpg\", \"data\": \"$B64\", \"mime_type\": \"image/jpeg\"}
    ]
  }"
```

### Contoh Python — Kirim File dari Lokal

```python
import base64, mimetypes, requests

BASE_URL = "http://16.79.2.204:9000"

def chat_with_attachment(prompt: str, file_paths: list[str], account: str = "account1", session_id: str = None) -> tuple[str, str]:
    headers = {"Content-Type": "application/json"}
    if session_id:
        headers["X-Session-ID"] = session_id

    attachments = []
    for path in file_paths:
        mime, _ = mimetypes.guess_type(path)
        with open(path, "rb") as f:
            attachments.append({
                "filename": path.split("/")[-1],
                "data": base64.b64encode(f.read()).decode(),
                "mime_type": mime or "application/octet-stream",
            })

    r = requests.post(
        f"{BASE_URL}/v1/chat/completions",
        headers=headers,
        json={
            "model": account,
            "messages": [{"role": "user", "content": prompt}],
            "attachments": attachments,
        },
        timeout=180,
    )
    r.raise_for_status()
    data = r.json()
    new_sid = r.headers.get("X-Session-ID") or data.get("x_meta", {}).get("session_id") or session_id
    return data["choices"][0]["message"]["content"], new_sid


reply, sid = chat_with_attachment(
    prompt="Deskripsikan isi gambar ini.",
    file_paths=["foto.jpg"],
    account="account1",
)
print(reply)
```

### Tips Penggunaan Attachment

**Ukuran file** — Disarankan tidak melebihi **20 MB per file**.

**Attachment di turn lanjutan** — Attachment bisa dikirim di turn mana saja, tidak hanya turn pertama.

**Data URI juga diterima:**
```json
{"filename": "foto.png", "data": "data:image/png;base64,iVBORw0KGgo...", "mime_type": "image/png"}
```

---

## Generate Gambar (Create Image)

Gunakan field `task_type: "create_image"`. URL gambar ada di field **`urls`** (array) pada response.

```json
{
  "model": "account1",
  "task_type": "create_image",
  "messages": [{"role": "user", "content": "Pemandangan kota futuristik di malam hari"}]
}
```

```python
import requests

def create_image(prompt: str, account: str = "account1") -> list[str]:
    r = requests.post(
        "http://16.79.2.204:9000/v1/chat/completions",
        json={"model": account, "task_type": "create_image", "messages": [{"role": "user", "content": prompt}]},
        timeout=180,
    )
    r.raise_for_status()
    return r.json().get("urls", [])

urls = create_image("Kucing astronaut di luar angkasa, gaya anime", account="account2")
print(urls)
```

> Generate gambar membutuhkan waktu ~20–60 detik. Set timeout minimal **120 detik**.

---

## Generate Video (Create Video)

Gunakan `task_type: "create_video"`. URL video ada di field **`urls`**.

```python
import requests

def create_video(prompt: str, account: str = "account1") -> list[str]:
    r = requests.post(
        "http://16.79.2.204:9000/v1/chat/completions",
        json={"model": account, "task_type": "create_video", "messages": [{"role": "user", "content": prompt}]},
        timeout=300,
    )
    r.raise_for_status()
    return r.json().get("urls", [])
```

> Generate video bisa 60–180 detik. Set timeout minimal **300 detik**.

---

## Pencarian Web (Web Search)

Gunakan `task_type: "web_search"`. Output tetap berupa teks, field `urls` selalu `[]`.

```python
import requests

def web_search(prompt: str, account: str = "account1") -> str:
    r = requests.post(
        "http://16.79.2.204:9000/v1/chat/completions",
        json={"model": account, "task_type": "web_search", "messages": [{"role": "user", "content": prompt}]},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

answer = web_search("Siapa juara Formula 1 terbaru?")
print(answer)
```

---

---

## Tool Calling (Function Calling)

> **Fitur baru.** Qwen sekarang bertindak sebagai **LLM murni** yang bisa memutuskan
> kapan harus memanggil tool eksternal. Format request/response **100% kompatibel
> dengan OpenAI function calling API**.

### Konsep

Tanpa tool calling, server selalu kembalikan `finish_reason: "stop"` + teks jawaban.

Dengan tool calling, ada dua kemungkinan response:

| `finish_reason` | Artinya | Yang harus dilakukan |
|---|---|---|
| `"stop"` | Qwen sudah punya jawaban final | Baca `choices[0].message.content` |
| `"tool_calls"` | Qwen minta eksekusi tool | Eksekusi tool, kirim hasilnya kembali |

---

### Alur Lengkap (2 Turn)

```
Turn 1:
  Client ──► POST /v1/chat/completions
              { messages, tools:[{write_file,...}] }

  Server ──► Inject tool schema ke prompt Qwen
  Qwen   ──► Respond: {"status":"tool_calls","tool_calls":[...]}
  Server ──► Return ke client:
              {
                "choices": [{
                  "message": {"role":"assistant","content":null,
                              "tool_calls":[{"id":"call_001","type":"function",
                                "function":{"name":"write_file",
                                            "arguments":{"path":"test.py",
                                                         "content":"..."}}}]},
                  "finish_reason": "tool_calls"
                }]
              }
              + header X-Session-ID: sess_abc123

Client eksekusi tool (write_file) ← misalnya via MCP / lokal

Turn 2:
  Client ──► POST /v1/chat/completions
              Header: X-Session-ID: sess_abc123
              {
                "messages": [
                  {"role":"user",      "content":"Buat file test.py"},
                  {"role":"assistant", "content":null,
                   "tool_calls":[{"id":"call_001",...}]},
                  {"role":"tool",      "tool_call_id":"call_001",
                   "name":"write_file","content":"success: file created"}
                ],
                "tools": [...]
              }

  Server ──► Inject [TOOL RESULT] ke Qwen CONTINUE session
  Qwen   ──► Respond: {"status":"success","choices":[...]}
  Server ──► Return ke client:
              {
                "choices": [{
                  "message": {"role":"assistant",
                              "content":"File test.py berhasil dibuat!"},
                  "finish_reason": "stop"
                }]
              }
```

---

### Format Request (Turn 1)

```json
{
  "model": "account1",
  "messages": [
    {"role": "user", "content": "Buat file test.py dengan kode hello world"}
  ],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "write_file",
        "description": "Write content to a file",
        "parameters": {
          "type": "object",
          "properties": {
            "path":    {"type": "string", "description": "File path"},
            "content": {"type": "string", "description": "File content"}
          },
          "required": ["path", "content"]
        }
      }
    },
    {
      "type": "function",
      "function": {
        "name": "execute_shell",
        "description": "Execute a shell command",
        "parameters": {
          "type": "object",
          "properties": {
            "command": {"type": "string"}
          },
          "required": ["command"]
        }
      }
    }
  ],
  "max_tokens": 2000
}
```

#### Field Tool Calling di Request Body

| Field | Tipe | Default | Keterangan |
|---|---|---|---|
| `tools` | `array` | `null` | List tool definition. Jika `null` → mode chat biasa |
| `tool_choice` | `string` | `"auto"` | `auto` (Qwen putuskan), `none` (jangan pakai tool), `required` (wajib pakai tool) |

---

### Format Response saat `finish_reason: "tool_calls"` (Turn 1)

```json
{
  "id": "chatcmpl-qwen-1750000000-account1",
  "object": "chat.completion",
  "created": 1750000000,
  "model": "account1",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": null,
        "tool_calls": [
          {
            "id": "call_001",
            "type": "function",
            "function": {
              "name": "write_file",
              "arguments": {
                "path": "test.py",
                "content": "print(\'hello world\')"
              }
            }
          }
        ]
      },
      "finish_reason": "tool_calls"
    }
  ],
  "usage": {
    "prompt_tokens": 312,
    "completion_tokens": 48,
    "total_tokens": 360
  }
}
```

> **Catatan:** `content` **wajib `null`** saat `finish_reason` adalah `"tool_calls"`.
> Jangan membaca `content` jika `finish_reason != "stop"`.

---

### Format Request Turn 2 (kirim tool result)

Setelah mengeksekusi tool, kirim hasilnya kembali dengan menambah dua message
ke array `messages`: role `assistant` (dari Turn 1) dan role `tool` (hasil eksekusi).

```json
{
  "model": "account1",
  "messages": [
    {"role": "user",      "content": "Buat file test.py dengan kode hello world"},
    {"role": "assistant", "content": null,
     "tool_calls": [{"id":"call_001","type":"function",
                     "function":{"name":"write_file",
                                 "arguments":{"path":"test.py","content":"..."}}}]},
    {"role": "tool",      "tool_call_id": "call_001",
     "name": "write_file", "content": "success: file test.py created (23 bytes)"}
  ],
  "tools": [
    {"type":"function","function":{"name":"write_file","description":"..."}}
  ]
}
```

**Header yang wajib disertakan di Turn 2:**

```
X-Session-ID: sess_abc123   ← dari response header Turn 1
```

#### Field `role: "tool"` di messages

| Field | Tipe | Keterangan |
|---|---|---|
| `role` | `"tool"` | Wajib `"tool"` |
| `tool_call_id` | string | Harus sama dengan `id` di `tool_calls` Turn 1 |
| `name` | string | Nama tool yang dipanggil |
| `content` | string | Hasil eksekusi tool (string, bisa JSON stringify) |

---

### Contoh Python — Tool Calling Lengkap (2 Turn)

```python
import json
import requests

BASE_URL = "http://16.79.2.204:9000"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file",
            "parameters": {
                "type": "object",
                "properties": {
                    "path":    {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_shell",
            "description": "Execute a shell command and return output",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                },
                "required": ["command"],
            },
        },
    },
]


def execute_tool(name: str, arguments: dict) -> str:
    """Eksekusi tool secara lokal (implementasi di client)."""
    if name == "write_file":
        path    = arguments["path"]
        content = arguments["content"]
        with open(path, "w") as f:
            f.write(content)
        return f"success: file {path!r} created ({len(content)} bytes)"

    elif name == "execute_shell":
        import subprocess
        result = subprocess.run(
            arguments["command"], shell=True,
            capture_output=True, text=True, timeout=30,
        )
        return result.stdout or result.stderr or "(no output)"

    return f"error: unknown tool {name!r}"


def chat_with_tools(user_message: str, account: str = "account1") -> str:
    """
    Agentic loop: kirim pesan, handle tool_calls, return jawaban final.
    """
    messages    = [{"role": "user", "content": user_message}]
    session_id  = None

    while True:
        headers = {"Content-Type": "application/json"}
        if session_id:
            headers["X-Session-ID"] = session_id

        resp = requests.post(
            f"{BASE_URL}/v1/chat/completions",
            headers=headers,
            json={"model": account, "messages": messages, "tools": TOOLS},
            timeout=180,
        )
        resp.raise_for_status()
        data = resp.json()

        # Simpan session_id dari header
        session_id = resp.headers.get("X-Session-ID", session_id)

        choice        = data["choices"][0]
        finish_reason = choice["finish_reason"]
        message       = choice["message"]

        if finish_reason == "stop":
            # Jawaban final — selesai
            return message["content"]

        elif finish_reason == "tool_calls":
            # Qwen minta eksekusi tool
            tool_calls = message["tool_calls"]

            # Tambah pesan assistant (dengan tool_calls) ke history
            messages.append({
                "role":       "assistant",
                "content":    None,
                "tool_calls": tool_calls,
            })

            # Eksekusi setiap tool dan tambah hasilnya ke messages
            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                fn_args = tc["function"]["arguments"]  # dict, bukan string

                print(f"  → Executing tool: {fn_name}({fn_args})")
                result = execute_tool(fn_name, fn_args)
                print(f"  ← Result: {result[:80]}")

                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc["id"],
                    "name":         fn_name,
                    "content":      result,
                })

            # Loop lagi — kirim tool result ke server (Turn 2)
            continue

        else:
            raise ValueError(f"Unexpected finish_reason: {finish_reason!r}")


# ── Contoh pemakaian ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    answer = chat_with_tools(
        "Buat file hello.py berisi print(\'Hello World!\'), "
        "lalu jalankan filenya dan tunjukkan outputnya.",
        account="account1",
    )
    print("\nJawaban akhir:", answer)
```

**Output yang diharapkan:**
```
  → Executing tool: write_file({'path': 'hello.py', 'content': "print('Hello World!')"})
  ← Result: success: file 'hello.py' created (22 bytes)
  → Executing tool: execute_shell({'command': 'python hello.py'})
  ← Result: Hello World!

Jawaban akhir: File hello.py berhasil dibuat dan dijalankan. Output: Hello World!
```

---

### Tips Penggunaan Tool Calling

**Selalu cek `finish_reason` sebelum baca `content`** — Saat `finish_reason` adalah
`"tool_calls"`, field `content` adalah `null`. Membacanya akan return `None`.

**`arguments` sudah berupa dict** — Tidak perlu `json.loads()`. Server sudah
memvalidasi bahwa Qwen mengembalikan `arguments` sebagai object, bukan string.

**Simpan `X-Session-ID` dari Turn 1** — Wajib dikirim di Turn 2 agar server
meneruskan tool result ke Qwen conversation yang sama (CONTINUE mode).

**Qwen bisa minta multiple tool calls sekaligus** — Iterasi seluruh `tool_calls`
array dan eksekusi semuanya sebelum kirim Turn 2.

**Qwen bisa minta tool calls beberapa kali** — Loop `while True` di contoh di atas
sudah handle kasus di mana Qwen meminta 3+ round tool calling sebelum jawaban final.

**Tool calling tidak kompatibel dengan `task_type`** — Jangan kombinasikan `tools`
dengan `task_type: "create_image"` atau `"web_search"`. Gunakan salah satu saja.


## Contoh Kode

### curl

**Percakapan baru:**
```bash
curl http://16.79.2.204:9000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -D - \
  -d '{"model": "account1", "messages": [{"role": "user", "content": "Apa itu list comprehension di Python?"}]}'
```

Flag `-D -` menampilkan response headers — gunakan untuk melihat `X-Session-ID`.

**Melanjutkan percakapan:**
```bash
curl http://16.79.2.204:9000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-Session-ID: abc123def456..." \
  -d '{"model": "account1", "messages": [{"role": "user", "content": "Berikan contoh kodenya."}]}'
```

**Dengan think mode:**
```bash
curl http://16.79.2.204:9000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "account1", "think_mode": "thinking", "messages": [{"role": "user", "content": "Jelaskan algoritma Dijkstra."}]}'
```

---

### Python (requests)

```python
import requests

BASE_URL = "http://16.79.2.204:9000"

def chat(prompt: str, account: str = "account1", session_id: str = None, think_mode: str = None) -> tuple[str, str]:
    headers = {"Content-Type": "application/json"}
    if session_id:
        headers["X-Session-ID"] = session_id

    body = {"model": account, "messages": [{"role": "user", "content": prompt}]}
    if think_mode:
        body["think_mode"] = think_mode

    r = requests.post(f"{BASE_URL}/v1/chat/completions", headers=headers, json=body, timeout=180)
    r.raise_for_status()
    data = r.json()
    new_sid = r.headers.get("X-Session-ID") or data.get("x_meta", {}).get("session_id") or session_id
    return data["choices"][0]["message"]["content"], new_sid


reply1, sid = chat("Apa itu decorator di Python?", account="account2")
print(f"[Turn 1] {reply1}\n")

reply2, sid = chat("Beri contoh penggunaannya.", session_id=sid)
print(f"[Turn 2] {reply2}\n")

reply3, sid2 = chat("Apa itu Docker?", account="account6", think_mode="fast")
print(f"[New session, account6] {reply3}\n")
```

**Kelas wrapper:**

```python
import requests

class QwenClient:
    def __init__(self, base_url: str, account: str = "account1"):
        self.base_url = base_url
        self.account = account
        self.session_id: str | None = None
        self.cookie_file: str | None = None
        self.conversation_url: str | None = None

    def send(self, prompt: str, think_mode: str = None) -> str:
        headers = {"Content-Type": "application/json"}
        if self.session_id:
            headers["X-Session-ID"] = self.session_id

        body = {"model": self.account, "messages": [{"role": "user", "content": prompt}]}
        if think_mode:
            body["think_mode"] = think_mode

        r = requests.post(f"{self.base_url}/v1/chat/completions", headers=headers, json=body, timeout=180)
        r.raise_for_status()
        data = r.json()
        self.session_id = r.headers.get("X-Session-ID") or data.get("x_meta", {}).get("session_id") or self.session_id
        self.cookie_file = r.headers.get("X-Cookie-File", self.cookie_file)
        self.conversation_url = r.headers.get("X-Conversation-URL", self.conversation_url)
        return data["choices"][0]["message"]["content"]

    def new_conversation(self, account: str = None):
        if self.session_id:
            try:
                requests.delete(f"{self.base_url}/v1/sessions/{self.session_id}", timeout=10)
            except Exception:
                pass
        self.session_id = self.cookie_file = self.conversation_url = None
        if account:
            self.account = account

    def info(self):
        print(f"Akun       : {self.account}")
        print(f"Cookie file: {self.cookie_file or '-'}")
        print(f"Session ID : {self.session_id or '(belum ada)'}")
        print(f"Conv URL   : {self.conversation_url or '-'}")


client = QwenClient("http://16.79.2.204:9000", account="account1")
print(client.send("Apa itu context manager di Python?"))
print(client.send("Beri contoh dengan kode."))

client.new_conversation(account="account6")
print(client.send("Jelaskan tentang asyncio.", think_mode="thinking"))
client.info()
```

---

### Python (httpx async)

```python
import asyncio, httpx

BASE_URL = "http://16.79.2.204:9000"

async def chat(client: httpx.AsyncClient, prompt: str, account: str = "account1", session_id: str = None) -> tuple[str, str]:
    headers = {}
    if session_id:
        headers["X-Session-ID"] = session_id

    r = await client.post("/v1/chat/completions", headers=headers,
                          json={"model": account, "messages": [{"role": "user", "content": prompt}]})
    r.raise_for_status()
    data = r.json()
    new_sid = r.headers.get("X-Session-ID") or data.get("x_meta", {}).get("session_id") or session_id
    return data["choices"][0]["message"]["content"], new_sid


async def main():
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=180) as client:
        reply1, sid = await chat(client, "Apa itu Rust?", account="account1")
        print(f"[1] {reply1[:200]}\n")

        reply2, sid = await chat(client, "Mengapa dibilang memory-safe?", session_id=sid)
        print(f"[2] {reply2[:200]}\n")

        # Dua percakapan paralel di akun berbeda
        results = await asyncio.gather(
            chat(client, "Jelaskan Go concurrency model", account="account1"),
            chat(client, "Jelaskan Kotlin coroutines", account="account2"),
        )
        for i, (text, _) in enumerate(results, 1):
            print(f"[Paralel {i}] {text[:150]}\n")

asyncio.run(main())
```

---

### JavaScript (fetch)

```javascript
const BASE_URL = "http://16.79.2.204:9000";

class QwenClient {
  constructor(baseUrl, account = "account1") {
    this.baseUrl = baseUrl;
    this.account = account;
    this.sessionId = null;
  }

  async send(prompt, thinkMode = null) {
    const headers = { "Content-Type": "application/json" };
    if (this.sessionId) headers["X-Session-ID"] = this.sessionId;

    const body = { model: this.account, messages: [{ role: "user", content: prompt }] };
    if (thinkMode) body.think_mode = thinkMode;

    const response = await fetch(`${this.baseUrl}/v1/chat/completions`, {
      method: "POST", headers, body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const newSid = response.headers.get("X-Session-ID");
    if (newSid) this.sessionId = newSid;

    const data = await response.json();
    return data.choices[0].message.content;
  }

  switchAccount(account) {
    this.account = account;
    this.sessionId = null;
  }
}

const client = new QwenClient(BASE_URL, "account1");

(async () => {
  const r1 = await client.send("Apa itu event loop di JavaScript?");
  console.log("[1]", r1.slice(0, 200));

  const r2 = await client.send("Bedanya dengan Python asyncio?");
  console.log("[2]", r2.slice(0, 200));

  client.switchAccount("account6");
  const r3 = await client.send("Jelaskan Docker.", "fast");
  console.log("[New, account6]", r3.slice(0, 200));
})();
```

---

### OpenAI SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://16.79.2.204:9000/v1",
    api_key="tidak-perlu",
)

response = client.chat.completions.create(
    model="account1",   # nama akun sebagai model
    messages=[{"role": "user", "content": "Apa itu list comprehension di Python?"}],
)
print(response.choices[0].message.content)
```

> OpenAI SDK tidak expose custom response headers secara langsung. Gunakan `requests` atau `httpx` jika perlu membaca `X-Session-ID` untuk mode continue.

---

## Referensi Lengkap Request & Response

### Request Body

| Field | Tipe | Default | Keterangan |
|---|---|---|---|
| `model` | string | — | Wajib. Nama akun (`"account1"`, `"account6"`, dll.) atau `"qwen"` untuk auto |
| `messages` | array | — | Wajib. Array objek `{role, content}` |
| `messages[].role` | string | — | `"user"`, `"assistant"`, atau `"system"` |
| `messages[].content` | string | — | Isi pesan |
| `stream` | boolean | `false` | Aktifkan streaming SSE |
| `think_mode` | string | `"fast"` | `"auto"`, `"thinking"`, atau `"fast"` |
| `attachments` | array | `[]` | File attachment. Setiap item: `{filename, data (base64), mime_type?}` |
| `task_type` | string | `"chat"` | `"chat"`, `"create_image"`, `"create_video"`, `"web_search"` |

### Response Body saat `finish_reason: "tool_calls"`

```json
{
  "id": "chatcmpl-qwen-...",
  "object": "chat.completion",
  "created": 1750000000,
  "model": "account1",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant",
      "content": null,
      "tool_calls": [{
        "id": "call_001",
        "type": "function",
        "function": {
          "name": "<nama_tool>",
          "arguments": { "<key>": "<value>" }
        }
      }]
    },
    "finish_reason": "tool_calls"
  }],
  "usage": {
    "prompt_tokens": 312,
    "completion_tokens": 48,
    "total_tokens": 360
  },
  "x_meta": {
    "session_id": "sess_abc123",
    "cookie_file": "account1.json",
    "conversation_url": "https://chat.qwen.ai/c/..."
  }
}
```

> `content` adalah `null` saat `finish_reason: "tool_calls"`. Wajib cek `finish_reason` sebelum membaca `content`.

---

### Response Body (non-streaming)

```json
{
  "id": "chatcmpl-a1b2c3d4e5f6",
  "object": "chat.completion",
  "created": 1748000000,
  "model": "account1",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "..."},
      "finish_reason": "stop"
    }
  ],
  "usage": {"prompt_tokens": 20, "completion_tokens": 312, "total_tokens": 332},
  "urls": [],
  "x_meta": {
    "session_id": "a1b2c3d4e5f6...",
    "cookie_file": "account1.json",
    "conversation_url": "https://chat.qwen.ai/c/xyz789",
    "task_type": "chat",
    "url_count": 0
  }
}
```

> `urls` berisi URL media untuk `create_image` / `create_video`. Selalu `[]` untuk `chat` dan `web_search`.

### Response Body (streaming)

Saat `"stream": true`, server mengirim **Server-Sent Events (SSE)**:

```
data: {"id":"chatcmpl-...","choices":[{"delta":{"role":"assistant","content":"Neural"},"index":0}]}

data: {"id":"chatcmpl-...","choices":[{"delta":{"content":" network"},"index":0}]}

data: [DONE]
```

**Contoh membaca streaming:**

```python
import json, requests

def chat_stream(prompt: str, account: str = "account1", session_id: str = None) -> tuple[str, str]:
    headers = {"Content-Type": "application/json"}
    if session_id:
        headers["X-Session-ID"] = session_id

    full_text = ""
    new_sid = session_id

    with requests.post(
        "http://16.79.2.204:9000/v1/chat/completions",
        headers=headers,
        json={"model": account, "stream": True, "messages": [{"role": "user", "content": prompt}]},
        stream=True,
        timeout=180,
    ) as resp:
        resp.raise_for_status()
        new_sid = resp.headers.get("X-Session-ID", session_id)
        for line in resp.iter_lines():
            if not line:
                continue
            line = line.decode() if isinstance(line, bytes) else line
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            chunk = json.loads(line[6:])
            delta = chunk["choices"][0]["delta"].get("content", "")
            print(delta, end="", flush=True)
            full_text += delta

    print()
    return full_text, new_sid


full_reply, sid = chat_stream("Jelaskan cara kerja HTTP request.", account="account1")
print(f"\nSession: {sid[:8]}...")
```

---

## Kode Error

| HTTP Status | Artinya | Yang Harus Dilakukan |
|---|---|---|
| `200` | Sukses | Baca `choices[0].message.content` |
| `400` | Request tidak valid | Pastikan ada `{"role":"user","content":"..."}` di `messages` |
| `404` | Session tidak ditemukan | Session expired — mulai percakapan baru tanpa `X-Session-ID` |
| `500` | Error internal server | Coba lagi beberapa saat |
| `502` | Scraper gagal memproses | Coba lagi — browser worker mungkin sedang restart |
| `503` | Tidak ada worker tersedia | Tunggu beberapa detik lalu coba lagi |
| `504` | Timeout dari Qwen AI | Coba ganti `think_mode` ke `"fast"` dan coba lagi |

**Contoh menangani error:**

```python
import requests
from requests.exceptions import HTTPError, Timeout

def safe_chat(prompt: str, account: str = "account1", session_id: str = None) -> tuple[str | None, str | None]:
    try:
        headers = {"Content-Type": "application/json"}
        if session_id:
            headers["X-Session-ID"] = session_id

        r = requests.post(
            "http://16.79.2.204:9000/v1/chat/completions",
            headers=headers,
            json={"model": account, "messages": [{"role": "user", "content": prompt}]},
            timeout=180,
        )
        r.raise_for_status()
        data = r.json()
        new_sid = r.headers.get("X-Session-ID", session_id)
        return data["choices"][0]["message"]["content"], new_sid

    except HTTPError as e:
        if e.response.status_code == 404:
            print("Session expired — memulai percakapan baru")
            return safe_chat(prompt, account=account, session_id=None)
        elif e.response.status_code in (502, 503, 504):
            print(f"Server error {e.response.status_code} — coba lagi nanti")
        else:
            print(f"Error {e.response.status_code}: {e.response.text}")
        return None, session_id

    except Timeout:
        print("Request timeout — coba lagi")
        return None, session_id
```

---

## Tips Praktis

**Cek dulu akun yang tersedia** — Jalankan `GET /v1/models` sebelum mulai untuk tahu nama akun yang bisa dipakai.

**Selalu simpan `X-Session-ID`** — Simpan dari response pertama dan kirim di setiap request berikutnya. Jika hilang, percakapan dimulai ulang dari awal.

**Gunakan `x_meta` sebagai fallback** — `session_id` juga ada di `response.x_meta.session_id` jika library Anda tidak bisa membaca response headers.

**Akun terikat ke session** — Setelah turn pertama, akun dikunci ke session. Mengganti `model` di turn berikutnya tidak mengubah akun. Gunakan session baru untuk berganti akun.

**Timeout yang disarankan:**
- `chat` / `web_search` — 120 detik
- `create_image` — minimal 180 detik
- `create_video` — minimal 300 detik

**Think mode `thinking` lebih lambat** — Gunakan hanya untuk pertanyaan yang benar-benar membutuhkan reasoning mendalam. Untuk percakapan biasa, `fast` atau `auto` sudah cukup.

**Session TTL 1 jam** — Sesi kedaluwarsa setelah 1 jam tidak digunakan.

**Percakapan paralel** — Setiap sesi menggunakan slot browser tersendiri. Anda bisa membuat beberapa sesi paralel dengan `session_id` berbeda tanpa saling mengganggu.

**Strategi pengiriman `messages[]` bergantung pada mode:**

- **Mode chat biasa (tanpa `tools`)** — Cukup kirim pesan `"user"` terbaru. Riwayat dikelola server via session.
- **Mode tool calling (dengan `tools`)** — Kirim *full* `messages[]` termasuk role `assistant` (dengan `tool_calls`) dan role `tool` (hasil eksekusi). Server butuh history ini untuk routing Turn 2 ke CONTINUE session yang tepat.

**`task_type` tidak bisa dikombinasikan dengan session** — `create_image`, `create_video`, dan `web_search` selalu memulai sesi baru. Tidak perlu menyimpan `X-Session-ID` dari response-nya.

**URL media ada di field `urls`** — Untuk `create_image` dan `create_video`, URL hasil generate ada di `response.urls` (array), bukan di `choices[0].message.content`.

**Attachment bisa dikirim di turn mana saja** — Tidak hanya turn pertama. File selalu di-encode sebagai base64 di dalam field `attachments`.
