# Panduan Mengakses AIChatScraper API

Dokumen ini ditujukan untuk **pengguna API** — Anda tidak perlu mengetahui cara kerja server atau instalasi apapun. Cukup baca panduan ini untuk mulai mengirim request dan mendapatkan respons dari Qwen AI.

---

## Daftar Isi

- [Base URL](#base-url)
- [Autentikasi](#autentikasi)
- [Cara Kerja Singkat](#cara-kerja-singkat)
- [Endpoint](#endpoint)
- [Mengirim Chat (Endpoint Utama)](#mengirim-chat-endpoint-utama)
- [Session & Percakapan Berkelanjutan](#session--percakapan-berkelanjutan)
- [Think Mode](#think-mode)
- [Mengirim File / Attachment](#mengirim-file--attachment)
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
http://108.137.15.61:9000
```

Minta base URL kepada operator server. Contoh:

```
http://108.137.15.61:9000
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
curl http://108.137.15.61:9000/health
```

**Response:**
```json
{"status": "ok", "timestamp": 1748000000}
```

---

### `GET /v1/models`

Daftar model yang tersedia.

```bash
curl http://108.137.15.61:9000/v1/models
```

**Response:**
```json
{
  "object": "list",
  "data": [
    {"id": "qwen", "object": "model", "owned_by": "qwen"}
  ]
}
```

---

### `GET /v1/sessions`

Lihat semua sesi aktif yang sedang tersimpan di server.

```bash
curl http://108.137.15.61:9000/v1/sessions
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
curl -X DELETE http://108.137.15.61:9000/v1/sessions/a1b2c3d4e5f6...
```

---

### `POST /v1/chat/completions`

**Endpoint utama.** Kirim pesan dan dapatkan respons dari Qwen AI.

---

## Mengirim Chat (Endpoint Utama)

### Request Minimal

```bash
curl http://108.137.15.61:9000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen",
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
| `model` | string | ✅ | Isi dengan `"qwen"` |
| `messages` | array | ✅ | Minimal satu objek `{"role": "user", "content": "..."}` |
| `stream` | boolean | ❌ | `true` untuk streaming SSE, default `false` |
| `think_mode` | string | ❌ | `"auto"`, `"thinking"`, atau `"fast"` |
| `attachments` | array | ❌ | Daftar file yang akan diupload ke Qwen (lihat [Mengirim File / Attachment](#mengirim-file--attachment)) |

**Nilai `role` yang diterima:** `"user"`, `"assistant"`, `"system"`

> Hanya pesan `"user"` terakhir yang dikirim ke Qwen. Pesan `"system"` dan riwayat `"assistant"` disertakan sebagai konteks untuk kompatibilitas dengan OpenAI SDK, namun pengelolaan riwayat percakapan sebenarnya dikelola oleh session di sisi server.

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
  "model": "qwen",
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
    "cookie_file": "account2.json",
    "conversation_url": "https://chat.qwen.ai/c/xyz789",
    "account_used": "account2"
  }
}
```

Respons Qwen ada di: `choices[0].message.content`

`x_meta` adalah ekstensi tambahan — berisi `session_id` di dalam body, berguna jika library Anda tidak bisa membaca response headers secara langsung.

---

## Session & Percakapan Berkelanjutan

### Konsep

Secara default, setiap request adalah **percakapan baru**. Untuk membuat percakapan multi-turn (tanya-jawab yang nyambung), Anda perlu menyimpan `X-Session-ID` dari response pertama dan mengirimkannya kembali di request berikutnya.

Server menyimpan:
- Akun mana yang dipakai untuk sesi Anda
- URL percakapan Qwen yang aktif

Sehingga ketika Anda mengirim prompt berikutnya, Qwen "ingat" konteks percakapan sebelumnya.

### Alur Lengkap

**Turn pertama — tidak ada `X-Session-ID`:**

```
POST /v1/chat/completions
Body: {"model":"qwen","messages":[{"role":"user","content":"Apa itu OOP?"}]}

Response:
  Header: X-Session-ID: abc123def456...
  Body: {"choices":[{"message":{"content":"OOP adalah..."}}], "x_meta":{"session_id":"abc123..."}}
```

**Turn kedua — sertakan `X-Session-ID`:**

```
POST /v1/chat/completions
Header: X-Session-ID: abc123def456...
Body: {"model":"qwen","messages":[{"role":"user","content":"Jelaskan inheritance-nya."}]}

Response:
  Header: X-Session-ID: abc123def456...   ← sama
  Body: {"choices":[{"message":{"content":"Inheritance adalah..."}}]}
  ← Qwen menjawab dengan konteks "kita sedang membahas OOP"
```

### Session Expired

Sesi otomatis kedaluwarsa setelah **1 jam tidak digunakan**. Jika `X-Session-ID` yang Anda kirim sudah expired, server akan membuat sesi baru secara otomatis — Anda akan menerima `X-Session-ID` baru di response. Perbarui ID yang Anda simpan.

### Menghapus Sesi Manual

Jika ingin memulai percakapan baru tanpa menunggu TTL:

```bash
curl -X DELETE http://108.137.15.61:9000/v1/sessions/abc123def456...
```

---

## Think Mode

Qwen AI memiliki tiga mode berpikir yang bisa Anda pilih per-request:

| Mode | Keterangan | Cocok untuk |
|---|---|---|
| `"fast"` | Cepat, tanpa reasoning panjang (default) | Pertanyaan umum, percakapan ringan |
| `"auto"` | Qwen memilih sendiri sesuai kompleksitas | Penggunaan umum |
| `"thinking"` | Reasoning mendalam, lebih lambat tapi akurat | Matematika, logika, analisis kompleks |

**Cara menggunakannya — tambahkan field `think_mode` di request body:**

```json
{
  "model": "qwen",
  "messages": [{"role": "user", "content": "Buktikan bahwa sqrt(2) adalah bilangan irasional."}],
  "think_mode": "thinking"
}
```

> Think mode hanya bisa diatur pada **turn pertama** (mode `new`). Pada turn lanjutan (mode `continue`), UI Qwen tidak menyediakan kontrol think mode di dalam halaman percakapan, sehingga mode yang dipakai mengikuti setelan awal.

---

## Mengirim File / Attachment

Anda bisa melampirkan satu atau lebih file ke setiap request — baik pada percakapan baru maupun pada turn lanjutan (mode `continue`). File dikirim sebagai **base64** di dalam field `attachments` pada request body.

### Format Attachment

Setiap item dalam array `attachments` berisi tiga field:

| Field | Tipe | Wajib | Keterangan |
|---|---|---|---|
| `filename` | string | ✅ | Nama file asli, misal `"foto.jpg"` atau `"laporan.pdf"` |
| `data` | string | ✅ | Konten file dalam format **base64**. Bisa raw base64 (`"iVBOR..."`) atau Data URI (`"data:image/png;base64,iVBOR..."`) |
| `mime_type` | string | ❌ | MIME type file. Jika tidak diisi, server akan meng-guess dari `filename`. Contoh: `"image/jpeg"`, `"application/pdf"`, `"text/plain"` |

### Tipe File yang Didukung

Semua tipe file yang didukung Qwen AI dapat dikirim, di antaranya:

| Kategori | Contoh Format |
|---|---|
| Gambar | `image/jpeg`, `image/png`, `image/webp`, `image/gif` |
| Dokumen | `application/pdf`, `application/msword`, `application/vnd.openxmlformats-officedocument.wordprocessingml.document` |
| Spreadsheet | `application/vnd.ms-excel`, `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` |
| Teks | `text/plain`, `text/csv`, `text/html`, `application/json` |
| Audio | `audio/mpeg`, `audio/wav` |
| Video | `video/mp4`, `video/webm` |

### Contoh Request dengan Attachment

**curl — kirim satu gambar:**

```bash
# Encode file ke base64 dulu
B64=$(base64 -w 0 foto.jpg)

curl http://108.137.15.61:9000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"qwen\",
    \"messages\": [{\"role\": \"user\", \"content\": \"Apa yang ada di gambar ini?\"}],
    \"attachments\": [
      {
        \"filename\": \"foto.jpg\",
        \"data\": \"$B64\",
        \"mime_type\": \"image/jpeg\"
      }
    ]
  }"
```

**curl — kirim beberapa file sekaligus:**

```bash
B64_IMG=$(base64 -w 0 diagram.png)
B64_PDF=$(base64 -w 0 laporan.pdf)

curl http://108.137.15.61:9000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"qwen\",
    \"messages\": [{\"role\": \"user\", \"content\": \"Jelaskan isi dokumen dan gambar ini.\"}],
    \"attachments\": [
      {\"filename\": \"diagram.png\", \"data\": \"$B64_IMG\", \"mime_type\": \"image/png\"},
      {\"filename\": \"laporan.pdf\", \"data\": \"$B64_PDF\", \"mime_type\": \"application/pdf\"}
    ]
  }"
```

**Python — kirim gambar dari file lokal:**

```python
import base64
import requests

BASE_URL = "http://108.137.15.61:9000"

def encode_file(path: str) -> str:
    """Encode file lokal ke base64 string."""
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()

def chat_with_attachment(
    prompt: str,
    file_paths: list[str],
    session_id: str = None,
    think_mode: str = None,
) -> tuple[str, str]:
    headers = {"Content-Type": "application/json"}
    if session_id:
        headers["X-Session-ID"] = session_id

    # Bangun daftar attachment dari file lokal
    attachments = []
    for path in file_paths:
        import mimetypes
        mime, _ = mimetypes.guess_type(path)
        attachments.append({
            "filename": path.split("/")[-1],
            "data": encode_file(path),
            "mime_type": mime or "application/octet-stream",
        })

    body = {
        "model": "qwen",
        "messages": [{"role": "user", "content": prompt}],
        "attachments": attachments,
    }
    if think_mode:
        body["think_mode"] = think_mode

    r = requests.post(
        f"{BASE_URL}/v1/chat/completions",
        headers=headers,
        json=body,
        timeout=180,
    )
    r.raise_for_status()
    data = r.json()

    new_sid = (
        r.headers.get("X-Session-ID")
        or data.get("x_meta", {}).get("session_id")
        or session_id
    )
    return data["choices"][0]["message"]["content"], new_sid


# ── Contoh penggunaan ──────────────────────────────────────────────────────────

# Kirim satu gambar
reply, sid = chat_with_attachment(
    prompt="Deskripsikan isi gambar ini secara detail.",
    file_paths=["foto.jpg"],
)
print(f"[Turn 1] {reply}\n")

# Lanjutkan percakapan dengan gambar baru (turn 2, session sama)
reply2, sid = chat_with_attachment(
    prompt="Bandingkan dengan gambar berikut ini.",
    file_paths=["foto2.jpg"],
    session_id=sid,
)
print(f"[Turn 2] {reply2}\n")

# Kirim beberapa file sekaligus
reply3, sid2 = chat_with_attachment(
    prompt="Analisis dokumen dan diagram ini.",
    file_paths=["laporan.pdf", "diagram.png"],
    think_mode="thinking",
)
print(f"[Multi-file] {reply3}\n")
```

**Python — kirim dari bytes / memory (tanpa file fisik):**

```python
import base64, requests

def chat_with_bytes(
    prompt: str,
    file_bytes: bytes,
    filename: str,
    mime_type: str,
    session_id: str = None,
) -> tuple[str, str]:
    headers = {"Content-Type": "application/json"}
    if session_id:
        headers["X-Session-ID"] = session_id

    body = {
        "model": "qwen",
        "messages": [{"role": "user", "content": prompt}],
        "attachments": [
            {
                "filename": filename,
                "data": base64.b64encode(file_bytes).decode(),
                "mime_type": mime_type,
            }
        ],
    }

    r = requests.post(
        "http://108.137.15.61:9000/v1/chat/completions",
        headers=headers,
        json=body,
        timeout=180,
    )
    r.raise_for_status()
    data = r.json()
    new_sid = r.headers.get("X-Session-ID", session_id)
    return data["choices"][0]["message"]["content"], new_sid


# Contoh: screenshot dari PIL
from PIL import ImageGrab
import io

screenshot = ImageGrab.grab()
buf = io.BytesIO()
screenshot.save(buf, format="PNG")
img_bytes = buf.getvalue()

reply, sid = chat_with_bytes(
    prompt="Ada apa di layar saya?",
    file_bytes=img_bytes,
    filename="screenshot.png",
    mime_type="image/png",
)
print(reply)
```

### Tips Penggunaan Attachment

**Ukuran file** — Tidak ada batasan eksplisit dari API ini, namun Qwen AI sendiri memiliki batasan ukuran upload. Disarankan tidak melebihi **20 MB per file**.

**Attachment di turn lanjutan** — Attachment bisa dikirim di turn mana saja, tidak hanya turn pertama. Ini berguna untuk percakapan analisis bertahap (misal: kirim grafik di turn 2 untuk dibahas lebih lanjut dari konteks turn 1).

**Data URI juga diterima** — Selain raw base64, format Data URI juga valid:
```json
{
  "filename": "foto.png",
  "data": "data:image/png;base64,iVBORw0KGgo...",
  "mime_type": "image/png"
}
```

**mime_type opsional tapi disarankan** — Jika tidak diisi, server meng-guess dari ekstensi filename. Untuk keandalan maksimal, selalu sertakan `mime_type` secara eksplisit.

---


### curl

**Percakapan baru:**

```bash
curl http://108.137.15.61:9000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -D - \
  -d '{
    "model": "qwen",
    "messages": [{"role": "user", "content": "Apa itu list comprehension di Python?"}]
  }'
```

Flag `-D -` menampilkan response headers di terminal — gunakan ini untuk melihat `X-Session-ID`.

**Melanjutkan percakapan:**

```bash
curl http://108.137.15.61:9000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-Session-ID: abc123def456..." \
  -d '{
    "model": "qwen",
    "messages": [{"role": "user", "content": "Berikan contoh kodenya."}]
  }'
```

**Dengan think mode:**

```bash
curl http://108.137.15.61:9000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen",
    "think_mode": "thinking",
    "messages": [{"role": "user", "content": "Jelaskan algoritma Dijkstra secara detail."}]
  }'
```

---

### Python (requests)

**Instalasi:**
```bash
pip install requests
```

**Penggunaan dasar:**

```python
import requests

BASE_URL = "http://108.137.15.61:9000"

def chat(prompt: str, session_id: str = None, think_mode: str = None) -> tuple[str, str]:
    """
    Kirim pesan ke Qwen AI.
    Mengembalikan (teks_respons, session_id).
    """
    headers = {"Content-Type": "application/json"}
    if session_id:
        headers["X-Session-ID"] = session_id

    body = {
        "model": "qwen",
        "messages": [{"role": "user", "content": prompt}],
    }
    if think_mode:
        body["think_mode"] = think_mode

    response = requests.post(
        f"{BASE_URL}/v1/chat/completions",
        headers=headers,
        json=body,
        timeout=180,
    )
    response.raise_for_status()

    data = response.json()
    text = data["choices"][0]["message"]["content"]

    # Ambil session_id dari header atau dari x_meta di body
    new_session_id = (
        response.headers.get("X-Session-ID")
        or data.get("x_meta", {}).get("session_id")
        or session_id
    )
    return text, new_session_id


# ── Contoh penggunaan ──────────────────────────────────────────────────────────

# Turn pertama — percakapan baru
reply1, sid = chat("Apa itu decorator di Python?")
print(f"[Turn 1] {reply1}\n")

# Turn kedua — melanjutkan percakapan (konteks tersimpan)
reply2, sid = chat("Beri contoh penggunaannya.", session_id=sid)
print(f"[Turn 2] {reply2}\n")

# Turn ketiga — masih sesi yang sama
reply3, sid = chat("Bagaimana cara membuat decorator dengan parameter?", session_id=sid)
print(f"[Turn 3] {reply3}\n")

# Mulai percakapan baru (tidak kirim session_id)
reply4, sid2 = chat("Apa itu Docker?", think_mode="fast")
print(f"[New session] {reply4}\n")
```

**Kelas wrapper lengkap:**

```python
import requests

class QwenClient:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.session_id: str | None = None
        self.cookie_file: str | None = None
        self.conversation_url: str | None = None

    def send(self, prompt: str, think_mode: str = None) -> str:
        """Kirim pesan. Session dikelola otomatis."""
        headers = {"Content-Type": "application/json"}
        if self.session_id:
            headers["X-Session-ID"] = self.session_id

        body = {
            "model": "qwen",
            "messages": [{"role": "user", "content": prompt}],
        }
        if think_mode:
            body["think_mode"] = think_mode

        r = requests.post(
            f"{self.base_url}/v1/chat/completions",
            headers=headers,
            json=body,
            timeout=180,
        )
        r.raise_for_status()
        data = r.json()

        # Update session info
        self.session_id = (
            r.headers.get("X-Session-ID")
            or data.get("x_meta", {}).get("session_id")
            or self.session_id
        )
        self.cookie_file = r.headers.get("X-Cookie-File", self.cookie_file)
        self.conversation_url = r.headers.get("X-Conversation-URL", self.conversation_url)

        return data["choices"][0]["message"]["content"]

    def new_conversation(self):
        """Mulai percakapan baru — reset session."""
        if self.session_id:
            try:
                requests.delete(f"{self.base_url}/v1/sessions/{self.session_id}", timeout=10)
            except Exception:
                pass
        self.session_id = None
        self.cookie_file = None
        self.conversation_url = None

    def info(self):
        print(f"Session ID : {self.session_id or '(belum ada)'}")
        print(f"Akun       : {self.cookie_file or '-'}")
        print(f"Conv URL   : {self.conversation_url or '-'}")


# ── Contoh penggunaan ──────────────────────────────────────────────────────────

client = QwenClient("http://108.137.15.61:9000")

print(client.send("Apa itu context manager di Python?"))
client.info()

print(client.send("Beri contoh dengan kode."))       # lanjut
print(client.send("Bagaimana cara custom context manager?"))  # lanjut

client.new_conversation()
print(client.send("Sekarang jelaskan tentang asyncio.", think_mode="thinking"))
client.info()   # session_id baru, akun mungkin berbeda
```

---

### Python (httpx async)

**Instalasi:**
```bash
pip install httpx
```

```python
import asyncio
import httpx

BASE_URL = "http://108.137.15.61:9000"

async def chat(
    client: httpx.AsyncClient,
    prompt: str,
    session_id: str = None,
    think_mode: str = None,
) -> tuple[str, str]:
    headers = {}
    if session_id:
        headers["X-Session-ID"] = session_id

    body = {
        "model": "qwen",
        "messages": [{"role": "user", "content": prompt}],
    }
    if think_mode:
        body["think_mode"] = think_mode

    r = await client.post("/v1/chat/completions", headers=headers, json=body)
    r.raise_for_status()
    data = r.json()

    new_sid = (
        r.headers.get("X-Session-ID")
        or data.get("x_meta", {}).get("session_id")
        or session_id
    )
    return data["choices"][0]["message"]["content"], new_sid


async def main():
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=180) as client:
        # Percakapan pertama
        reply1, sid = await chat(client, "Apa itu Rust?")
        print(f"[1] {reply1[:200]}\n")

        reply2, sid = await chat(client, "Mengapa dibilang memory-safe?", session_id=sid)
        print(f"[2] {reply2[:200]}\n")

        # Percakapan paralel (dua sesi berbeda bersamaan)
        results = await asyncio.gather(
            chat(client, "Jelaskan Go concurrency model"),
            chat(client, "Jelaskan Kotlin coroutines"),
        )
        for i, (text, s) in enumerate(results, 1):
            print(f"[Paralel {i}] session={s[:8]} | {text[:150]}\n")

asyncio.run(main())
```

---

### JavaScript (fetch)

```javascript
const BASE_URL = "http://108.137.15.61:9000";

class QwenClient {
  constructor(baseUrl) {
    this.baseUrl = baseUrl;
    this.sessionId = null;
  }

  async send(prompt, thinkMode = null) {
    const headers = { "Content-Type": "application/json" };
    if (this.sessionId) {
      headers["X-Session-ID"] = this.sessionId;
    }

    const body = {
      model: "qwen",
      messages: [{ role: "user", content: prompt }],
    };
    if (thinkMode) body.think_mode = thinkMode;

    const response = await fetch(`${this.baseUrl}/v1/chat/completions`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      const err = await response.text();
      throw new Error(`HTTP ${response.status}: ${err}`);
    }

    // Simpan session ID dari header
    const newSid = response.headers.get("X-Session-ID");
    if (newSid) this.sessionId = newSid;

    const data = await response.json();
    return data.choices[0].message.content;
  }

  resetSession() {
    this.sessionId = null;
  }
}

// ── Contoh penggunaan ──────────────────────────────────────────────────────────

const client = new QwenClient(BASE_URL);

(async () => {
  const r1 = await client.send("Apa itu event loop di JavaScript?");
  console.log("[1]", r1.slice(0, 200));

  const r2 = await client.send("Bedanya dengan Python asyncio?");
  console.log("[2]", r2.slice(0, 200));   // konteks tersambung

  client.resetSession();
  const r3 = await client.send("Jelaskan Docker.", "fast");
  console.log("[New]", r3.slice(0, 200));
})();
```

---

### OpenAI SDK

API ini kompatibel dengan OpenAI Python SDK. Arahkan `base_url` ke server ini.

**Instalasi:**
```bash
pip install openai
```

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://108.137.15.61:9000/v1",
    api_key="tidak-perlu",   # wajib diisi SDK tapi tidak diverifikasi server
)

# ── Percakapan sederhana ───────────────────────────────────────────────────────

response = client.chat.completions.create(
    model="qwen",
    messages=[{"role": "user", "content": "Apa itu list comprehension di Python?"}],
)
print(response.choices[0].message.content)

# ── Percakapan multi-turn dengan session ──────────────────────────────────────
# Catatan: OpenAI SDK tidak expose custom response headers secara langsung.
# Gunakan x_meta dari response body, atau gunakan requests/httpx untuk
# membaca header X-Session-ID secara langsung (lihat contoh di atas).

# Alternatif: gunakan x_meta yang ada di response (via model_extra)
import requests

session_id = None
BASE = "http://108.137.15.61:9000"

def chat_with_session(prompt: str) -> str:
    global session_id
    headers = {"Content-Type": "application/json"}
    if session_id:
        headers["X-Session-ID"] = session_id

    r = requests.post(
        f"{BASE}/v1/chat/completions",
        headers=headers,
        json={"model": "qwen", "messages": [{"role": "user", "content": prompt}]},
        timeout=180,
    )
    r.raise_for_status()
    data = r.json()
    session_id = r.headers.get("X-Session-ID", session_id)
    return data["choices"][0]["message"]["content"]

print(chat_with_session("Apa itu type hints di Python?"))
print(chat_with_session("Beri contoh kodenya."))   # melanjutkan
```

---

## Referensi Lengkap Request & Response

### Request Body

```json
{
  "model": "qwen",
  "messages": [
    {
      "role": "system",
      "content": "Kamu adalah asisten yang menjawab dalam Bahasa Indonesia."
    },
    {
      "role": "user",
      "content": "Jelaskan apa yang ada di gambar ini."
    }
  ],
  "stream": false,
  "think_mode": "auto",
  "attachments": [
    {
      "filename": "foto.jpg",
      "data": "iVBORw0KGgo...",
      "mime_type": "image/jpeg"
    }
  ]
}
```

| Field | Tipe | Default | Keterangan |
|---|---|---|---|
| `model` | string | — | Wajib. Isi `"qwen"` |
| `messages` | array | — | Wajib. Array objek `{role, content}` |
| `messages[].role` | string | — | `"user"`, `"assistant"`, atau `"system"` |
| `messages[].content` | string | — | Isi pesan |
| `stream` | boolean | `false` | Aktifkan streaming SSE |
| `think_mode` | string | `"fast"` | `"auto"`, `"thinking"`, atau `"fast"` |
| `attachments` | array | `[]` | Daftar file attachment. Setiap item: `{filename, data (base64), mime_type?}` |
| `attachments[].filename` | string | — | Nama file, misal `"foto.jpg"` |
| `attachments[].data` | string | — | Konten file dalam base64 (raw atau Data URI) |
| `attachments[].mime_type` | string | auto-detect | MIME type opsional, misal `"image/jpeg"` |

### Response Body (non-streaming)

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
        "content": "Neural network adalah..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 20,
    "completion_tokens": 312,
    "total_tokens": 332
  },
  "x_meta": {
    "session_id": "a1b2c3d4e5f6...",
    "cookie_file": "account2.json",
    "conversation_url": "https://chat.qwen.ai/c/xyz789",
    "account_used": "account2"
  }
}
```

### Response Body (streaming)

Saat `"stream": true`, server mengirim **Server-Sent Events (SSE)**:

```
data: {"id":"chatcmpl-...","choices":[{"delta":{"role":"assistant","content":"Neural"},"index":0}]}

data: {"id":"chatcmpl-...","choices":[{"delta":{"content":" network"},"index":0}]}

data: {"id":"chatcmpl-...","choices":[{"delta":{"content":" adalah"},"index":0}]}

data: [DONE]
```

Contoh membaca streaming:

```python
import json, requests

def chat_stream(prompt: str, session_id: str = None) -> tuple[str, str]:
    headers = {"Content-Type": "application/json"}
    if session_id:
        headers["X-Session-ID"] = session_id

    full_text = ""
    new_sid = session_id

    with requests.post(
        f"http://108.137.15.61:9000/v1/chat/completions",
        headers=headers,
        json={"model": "qwen", "stream": True,
              "messages": [{"role": "user", "content": prompt}]},
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

    print()  # newline setelah stream selesai
    return full_text, new_sid


full_reply, sid = chat_stream("Jelaskan cara kerja HTTP request.")
print(f"\nSession: {sid[:8]}...")
```

---

## Kode Error

| HTTP Status | Artinya | Yang Harus Dilakukan |
|---|---|---|
| `200` | Sukses | Baca `choices[0].message.content` |
| `400` | Request tidak valid | Pastikan ada `{"role":"user","content":"..."}` di `messages` |
| `404` | Session tidak ditemukan | Session expired atau ID salah — mulai percakapan baru (tidak kirim `X-Session-ID`) |
| `500` | Error internal server | Coba lagi beberapa saat |
| `502` | Scraper gagal memproses | Coba lagi — mungkin browser worker sedang restart |
| `503` | Server belum siap | Tunggu beberapa detik lalu coba lagi |
| `504` | Timeout dari Qwen AI | Coba lagi — Qwen mungkin lambat merespons, coba ganti `think_mode` ke `"fast"` |

**Contoh menangani error:**

```python
import requests
from requests.exceptions import HTTPError, Timeout

def safe_chat(prompt: str, session_id: str = None) -> tuple[str | None, str | None]:
    try:
        headers = {"Content-Type": "application/json"}
        if session_id:
            headers["X-Session-ID"] = session_id

        r = requests.post(
            "http://108.137.15.61:9000/v1/chat/completions",
            headers=headers,
            json={"model": "qwen", "messages": [{"role": "user", "content": prompt}]},
            timeout=180,
        )
        r.raise_for_status()
        data = r.json()
        new_sid = r.headers.get("X-Session-ID", session_id)
        return data["choices"][0]["message"]["content"], new_sid

    except HTTPError as e:
        status = e.response.status_code
        if status == 404:
            print("Session expired — memulai percakapan baru")
            return safe_chat(prompt, session_id=None)   # retry tanpa session
        elif status in (502, 503, 504):
            print(f"Server error {status} — coba lagi nanti")
        else:
            print(f"Error {status}: {e.response.text}")
        return None, session_id

    except Timeout:
        print("Request timeout — coba lagi")
        return None, session_id
```

---

## Tips Praktis

**Selalu simpan `X-Session-ID`** — Simpan dari response pertama dan kirim di setiap request berikutnya agar percakapan tersambung. Jika lupa atau hilang, Anda hanya akan memulai percakapan baru.

**Gunakan `x_meta` sebagai fallback** — Jika library Anda tidak mudah membaca response headers, `session_id` juga ada di `response.x_meta.session_id` dalam body JSON.

**Timeout yang disarankan** — Set timeout minimal **120 detik** di client Anda. Qwen AI bisa membutuhkan waktu lama terutama untuk mode `thinking`.

**Think mode `thinking` lebih lambat** — Gunakan hanya untuk pertanyaan yang benar-benar membutuhkan reasoning mendalam (matematika, logika, analisis). Untuk percakapan biasa, `fast` atau `auto` sudah cukup.

**Session TTL 1 jam** — Sesi kedaluwarsa setelah 1 jam tidak digunakan. Jika aplikasi Anda perlu sesi lebih lama, minta operator untuk menaikkan `--session-ttl`.

**Percakapan paralel** — Setiap sesi menggunakan slot browser tersendiri. Anda bisa membuat beberapa sesi paralel (masing-masing dengan `session_id` berbeda) tanpa saling mengganggu.

**Jangan kirim seluruh riwayat chat di `messages`** — Berbeda dengan OpenAI API asli, riwayat percakapan dikelola oleh server via session. Cukup kirim pesan `"user"` terbaru saja di setiap request.

**Attachment bisa dikirim di turn mana saja** — Tidak hanya turn pertama. Anda bisa mengirim gambar atau dokumen baru di turn ke-2, ke-3, dan seterusnya dalam sesi yang sama. File selalu di-encode sebagai base64 di dalam field `attachments`.