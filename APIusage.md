# Panduan Lengkap Menggunakan AIChatScraper API (PAF-ModelQwen)

Dokumen ini berisi panduan lengkap penggunaan API dari server AIChatScraper (PAF-ModelQwen) yang telah Anda siapkan.

API ini didesain agar **100% kompatibel dengan format OpenAI Chat Completions API**, sehingga Anda bisa menggunakan berbagai library yang mendukung OpenAI (seperti OpenAI Python SDK) maupun melakukan request HTTP standar.

---

## Informasi Dasar

- **Base URL:** `http://16.79.2.204:9000`
- **Autentikasi:** API ini **TIDAK memerlukan API Key** dari sisi client. Anda tidak perlu menyertakan header `Authorization` apa pun.
- **Tipe Data:** Data request dan response dikirim dalam format JSON.

---

## Daftar Endpoint

1. **`GET /health`** - Mengecek status server.
2. **`GET /v1/models`** - Melihat daftar akun (cookie) yang aktif di worker.
3. **`GET /v1/sessions`** - Melihat sesi percakapan yang sedang aktif.
4. **`DELETE /v1/sessions/{session_id}`** - Menghapus sesi manual.
5. **`POST /v1/chat/completions`** - Endpoint utama untuk mengirim chat, media, web search, dan tool calling.

---

## 1. Mengecek Akun yang Tersedia (Models)

Setiap worker memiliki beberapa "akun/cookie" Qwen yang terdaftar. Field `model` pada endpoint `/v1/chat/completions` digunakan untuk memilih akun mana yang akan dipakai.

**Request:**
```bash
curl http://16.79.2.204:9000/v1/models
```

**Response:**
```json
{
  "object": "list",
  "data": [
    {"id": "account1", "object": "model", "owned_by": "qwen-ai"},
    {"id": "account2", "object": "model", "owned_by": "qwen-ai"}
  ]
}
```

> **Tips:** Gunakan nama akun (misal `account1`) di field `model`. Jika Anda mengisi `qwen`, worker akan otomatis menyeimbangkan beban (load balance) ke akun yang sedang nganggur.

---

## 2. Percakapan Baru (Chat Completions)

Ini adalah endpoint utama untuk memulai percakapan.

### Contoh Request (cURL)

```bash
curl -X POST http://16.79.2.204:9000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "account1",
    "messages": [
      {"role": "user", "content": "Halo, jelaskan apa itu Python?"}
    ],
    "think_mode": "fast"
  }'
```

### Penjelasan Parameter:
- `model` (wajib): Nama akun (misal `"account1"`) atau `"qwen"`.
- `messages` (wajib): Array berisi riwayat percakapan.
- `stream` (opsional): Boolean `true`/`false`. Default `false`.
- `think_mode` (opsional): Pilih `"fast"` (default), `"auto"`, atau `"thinking"` (untuk masalah rumit/matematika). Mode ini hanya bisa diatur di awal percakapan (sesi baru).

### Contoh Response:
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
        "content": "Python adalah bahasa pemrograman tingkat tinggi..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 12,
    "completion_tokens": 148,
    "total_tokens": 160
  },
  "urls": [],
  "x_meta": {
    "session_id": "a1b2c3d4e5f6...",
    "cookie_file": "account1.json",
    "conversation_url": "https://chat.qwen.ai/c/xyz789",
    "task_type": "chat"
  }
}
```

---

## 3. Melanjutkan Percakapan (Sesi Stateful)

Tidak seperti API OpenAI standar yang mengharuskan Anda mengirimkan **seluruh riwayat pesan** setiap saat, server ini sudah melacak percakapan menggunakan **Session ID**. 

Untuk melanjutkan percakapan yang sama agar Qwen ingat konteks sebelumnya:
1. Ambil `X-Session-ID` dari *Response Header* permintaan pertama (atau ambil dari field `x_meta.session_id` di response body).
2. Sertakan di *Request Header* `X-Session-ID` pada pesan Anda berikutnya.

**Contoh Lanjutan (Turn 2):**
```bash
curl -X POST http://16.79.2.204:9000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-Session-ID: a1b2c3d4e5f6..." \
  -d '{
    "model": "account1",
    "messages": [
      {"role": "user", "content": "Berikan saya contoh kodenya."}
    ]
  }'
```

Sesi akan otomatis kedaluwarsa jika tidak digunakan selama 1 jam.

---

## 4. Mengirim File / Gambar (Attachments)

Anda bisa menyisipkan file ke Qwen dengan meng-encode file tersebut ke **Base64**. Maksimal ukuran yang disarankan adalah 20MB.

**Contoh Python:**
```python
import requests
import base64

with open("foto.jpg", "rb") as f:
    b64_data = base64.b64encode(f.read()).decode()

response = requests.post("http://16.79.2.204:9000/v1/chat/completions", json={
    "model": "account1",
    "messages": [{"role": "user", "content": "Apa isi gambar ini?"}],
    "attachments": [
        {
            "filename": "foto.jpg",
            "data": b64_data,
            "mime_type": "image/jpeg"
        }
    ]
})
print(response.json()["choices"][0]["message"]["content"])
```

---

## 5. Fitur Khusus: Generate Gambar, Video & Web Search

Anda bisa memanfaatkan kemampuan AI multimoda dari Qwen menggunakan parameter `task_type`.
*Perhatian: Fitur `task_type` tidak bisa digabung dengan sesi lanjutan (selalu menghasilkan sesi baru).*

### A. Web Search
Menugaskan Qwen mencari info real-time di internet.
```json
{
  "model": "account1",
  "task_type": "web_search",
  "messages": [{"role": "user", "content": "Siapa juara dunia Formula 1 tahun ini?"}]
}
```

### B. Generate Gambar
Membuat gambar dari deskripsi teks.
- Pastikan mengatur HTTP request timeout minimal 120-180 detik.
- URL gambar tidak ada di teks response, melainkan di field `urls`.
```json
{
  "model": "account1",
  "task_type": "create_image",
  "messages": [{"role": "user", "content": "Gambar robot kucing di mars, 8k resolution"}]
}
```

### C. Generate Video
Sama dengan gambar, tetapi `task_type: "create_video"`. Timeout minimal 300 detik.

---

## 6. Integrasi dengan OpenAI Python SDK

Karena API ini kompatibel penuh dengan format OpenAI, Anda bisa menggunakan `openai` module langsung. Hal ini sangat berguna jika proyek Anda awalnya dibuat untuk OpenAI/ChatGPT.

1. Install module: `pip install openai`
2. Eksekusi kode:

```python
from openai import OpenAI

# Arahkan base_url ke server Anda
client = OpenAI(
    base_url="http://16.79.2.204:9000/v1",
    api_key="tidak-perlu", # API key dikosongkan
)

response = client.chat.completions.create(
    model="account1", # Gunakan nama akun worker Anda
    messages=[
        {"role": "user", "content": "Tuliskan puisi pendek tentang AI."}
    ],
)

print(response.choices[0].message.content)
```

> **Catatan Mode Sesi di OpenAI SDK:**
> Secara bawaan, OpenAI Python SDK tidak membiarkan Anda mengakses Header Response dengan mudah (untuk mengambil `X-Session-ID`). Jika Anda butuh mode percakapan berlanjut yang stateful, disarankan menggunakan module `requests` di Python, atau manfaatkan atribut ekstensi jika menggunakan SDK.

---

## 7. Tool Calling (OpenAI-compatible Function Calling)

Server mendukung fitur Tool Calling murni!
Ketika Anda menyertakan array `tools`, dan Qwen merasa dia perlu alat tersebut:
1. Server merespon dengan `finish_reason: "tool_calls"` dan nilai `content: null`.
2. Anda bertugas **mengeksekusi tool** tersebut di lokal.
3. Kirim kembali hasil eksekusinya menggunakan Header `X-Session-ID` dari Turn 1, menambahkan role `tool` pada pesannya.

> **⚠️ Prasyarat: Custom Instruction Qwen harus diperbarui di semua akun.**
>
> Qwen di `chat.qwen.ai` memiliki built-in tools sendiri (web search, code
> interpreter, dll.) yang dapat berkonflik dengan tool definitions dari client.
> Untuk menghindari konflik ini, **Custom Instruction** pada setiap akun Qwen
> harus berisi klausa berikut (lihat README untuk teks lengkapnya):
> - Format `tool_calls` sebagai format response ketiga yang dikenali.
> - Instruksi bahwa `[SYSTEM CONTEXT]` meng-override semua built-in tools.
>
> Tanpa update ini, Qwen bisa mencampur format tool calling internalnya dengan
> format kustom, menghasilkan JSON malformed dan corrective feedback yang salah.

**Contoh Payload Awal (Turn 1):**
```json
{
  "model": "account1",
  "messages": [{"role": "user", "content": "Buat file test.py"}],
  "tools": [{
    "type": "function",
    "function": {
      "name": "write_file",
      "description": "Menulis file",
      "parameters": {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"]
      }
    }
  }]
}
```

**Contoh Turn 2 — kirim hasil eksekusi tool:**
```json
{
  "model": "account1",
  "messages": [
    {"role": "user", "content": "Buat file test.py"},
    {
      "role": "assistant",
      "content": null,
      "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "write_file", "arguments": {"path": "test.py", "content": "print('hello')"}}}]
    },
    {
      "role": "tool",
      "tool_call_id": "call_1",
      "name": "write_file",
      "content": "{\"success\": true}"
    }
  ]
}
```
Header: `X-Session-ID: <session_id dari Turn 1>`

---

## Rekap & Tips Error
- **Timeout Disarankan:** Atur timeout request klien Anda minimal 180 detik. Terutama untuk `"think_mode": "thinking"` karena butuh waktu lama untuk reasoning, dan untuk `create_image`/`create_video`.
- **404 Session Not Found:** Sesi sudah kadaluarsa. Mulai pesan baru tanpa header `X-Session-ID`.
- **503 Tidak ada worker tersedia:** Terjadi saat `public.py` tidak dijalankan, koneksi terputus, atau worker penuh. Tunggu sebentar atau cek status di `/health`.
