# AIChatScraper – Qwen AI

Async Python scraper untuk Qwen AI (`chat.qwen.ai`) menggunakan **Playwright** dengan arsitektur class-based, cookie persistence, dan rotasi akun otomatis.

---

## Fitur

| Fitur | Keterangan |
|---|---|
| ⚡ Async/await | Performa tinggi dengan `asyncio` + Playwright async |
| 🔄 Multi-account rotation | Rotasi cookie otomatis saat rate-limit / session expired |
| 🍪 Cookie persistence | Export dari Cookie-Editor, simpan di `cookies/*.json` |
| 🔁 Concurrent scraping | Jalankan banyak prompt sekaligus (`--concurrent N`) |
| 💾 Output terstruktur | JSON + ekstrak code block ke file terpisah |
| 📋 Logging | Console + rotating file log di `logs/scraper.log` |
| 🔧 Error recovery | Retry otomatis + fallback antar akun |

---

## Struktur Folder

```
aichat-scraper/
├── scrapers/
│   ├── __init__.py
│   ├── base_scraper.py     # BaseAIChatScraper – abstract base class
│   ├── qwen_scraper.py     # QwenScraper – implementasi Qwen AI
│   └── utils.py            # Helper functions
├── config.py               # Konfigurasi & path
├── main.py                 # CLI entry point
├── requirements.txt
├── cookies/                # Simpan file cookie di sini  ← BUAT FOLDER INI
│   ├── account1.json
│   ├── account2.json
│   └── ...
├── output/                 # Hasil scraping (JSON)
│   └── code/               # Code block yang diekstrak
└── logs/                   # Log file
```

---

## Instalasi

```bash
# 1. Clone / download project
cd aichat-scraper

# 2. Buat virtual environment
python -m venv .venv
source .venv/bin/activate        # Linux/macOS
.venv\Scripts\activate           # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Install browser Chromium untuk Playwright
playwright install chromium
```

---

## Setup Cookie

Qwen AI menggunakan autentikasi berbasis cookie. Ikuti langkah berikut:

### Ekspor Cookie dari Browser

1. Install ekstensi **Cookie-Editor** di Chrome/Firefox
2. Buka `https://chat.qwen.ai` dan **login**
3. Klik ekstensi Cookie-Editor → **Export** → **Export as JSON**
4. Simpan file di folder `cookies/`, misal: `cookies/account1.json`

### Multi-Account

Untuk mendukung rotasi akun, simpan beberapa file cookie:

```
cookies/
├── account1.json   # akun utama
├── account2.json   # akun cadangan 1
└── account3.json   # akun cadangan 2
```

Sistem akan **otomatis merotasi** ke akun berikutnya jika mendeteksi:
- Rate limit / too many requests
- Session expired / login required
- Quota / usage limit exceeded

---

## Penggunaan CLI

### Prompt Tunggal

```bash
# Mode baru (percakapan baru)
python main.py --prompt "Jelaskan async/await di Python" --mode new

# Lanjutkan percakapan sebelumnya
python main.py --prompt "Beri contoh kodenya" --mode continue

# Tampilkan browser (non-headless, untuk debug)
python main.py --prompt "Hello" --no-headless

# Simpan code block yang ditemukan di response
python main.py --prompt "Buat REST API dengan FastAPI" --save-code

# Gunakan satu cookie file spesifik
python main.py --prompt "Hi" --cookie cookies/account1.json

# Tentukan nama output file
python main.py --prompt "Hello" --output hasil.json
```

### Multi-Prompt Concurrent

Buat file `prompts.txt` (satu prompt per baris):

```
Apa itu machine learning?
Jelaskan neural network
Buat kode Python untuk sorting
```

```bash
# Jalankan semua prompt secara bersamaan (maks 3 browser)
python main.py --prompts-file prompts.txt --concurrent 3

# Dengan 2 browser sekaligus
python main.py --prompts-file prompts.txt --concurrent 2
```

---

## Penggunaan sebagai Library

```python
import asyncio
from scrapers.qwen_scraper import QwenScraper

async def main():
    # Single prompt
    async with QwenScraper(headless=True) as q:
        result = await q.scrape("Jelaskan recursion")
    
    print(result["response"])
    print(f"Code blocks: {result['code_block_count']}")

    # Concurrent / batch
    prompts = ["Apa itu OOP?", "Jelaskan decorator Python"]
    results = await QwenScraper.scrape_many(
        prompts=prompts,
        max_concurrent=2,
    )
    for r in results:
        print(r["prompt"], "→", r["success"])

asyncio.run(main())
```

---

## Format Output JSON

```json
{
  "prompt": "Jelaskan async/await",
  "response": "Async/await adalah...",
  "file_type": "python",
  "code_blocks": [
    {
      "index": 1,
      "lang": "python",
      "extension": "py",
      "code": "import asyncio\n..."
    }
  ],
  "code_block_count": 1,
  "account_used": "account1",
  "timestamp": "2024-05-24T15:30:12",
  "success": true,
  "error": null
}
```

---

## Konfigurasi

Edit `config.py` untuk menyesuaikan:

| Parameter | Default | Keterangan |
|---|---|---|
| `BROWSER_CONFIG.headless` | `True` | Jalankan browser tanpa UI |
| `BROWSER_CONFIG.slow_mo` | `50` | Delay antar aksi (ms) |
| `QWEN_CONFIG.timeouts.response_wait` | `300000` | Timeout respons AI (ms) |
| `ROTATION_CONFIG.max_retries_per_account` | `2` | Max retry per akun |
| `ROTATION_CONFIG.retry_delay` | `5` | Jeda antar retry (detik) |

---

## Troubleshooting

**Browser tidak muncul / crash**
```bash
playwright install chromium --with-deps
```

**Cookie tidak terbaca**
- Pastikan format export adalah JSON (bukan Netscape)
- Gunakan Cookie-Editor versi terbaru
- Cek apakah `domain` pada cookie adalah `.qwen.ai`

**Rate limit terus-menerus**
- Tambah lebih banyak akun di folder `cookies/`
- Naikkan `ROTATION_CONFIG.retry_delay`
- Kurangi `--concurrent` jika menggunakan batch mode

**Response tidak terdeteksi**
- Jalankan dengan `--no-headless` untuk observasi visual
- Naikkan `BROWSER_CONFIG.slow_mo` ke 100–200 ms
- Cek `logs/scraper.log` untuk detail error
