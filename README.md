# AIChatScraper – Qwen AI

Async Python scraper untuk Qwen AI (`chat.qwen.ai`) menggunakan **Playwright** dengan arsitektur class-based, persistent browser profile, cookie persistence, dan rotasi akun otomatis.

---

## Fitur

| Fitur | Keterangan |
|---|---|
| ⚡ Async/await | Performa tinggi dengan `asyncio` + Playwright async |
| 🧠 Think mode | Pilih mode berpikir Qwen: `auto`, `thinking`, atau `fast` |
| 🔄 Multi-account rotation | Rotasi cookie otomatis saat rate-limit / session expired |
| 🍪 Cookie persistence | Export dari Cookie-Editor, simpan di `cookies/*.json` |
| 💾 Persistent browser profile | State browser (cookies, localStorage) bertahan antar sesi |
| 🔁 Concurrent scraping | Jalankan banyak prompt sekaligus (`--concurrent N`) |
| 📦 Output terstruktur | JSON + ekstrak code block ke file terpisah |
| 📋 Logging | Console + rotating file log di `logs/scraper.log` |
| 🔧 Error recovery | Retry otomatis + fallback antar akun |
| 🔍 Debug selector | Scan DOM otomatis saat think mode gagal diterapkan |

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
├── profiles/               # Persistent browser profiles (otomatis dibuat)
│   ├── account1/
│   └── account2/
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

> Setiap akun mendapat folder profile browser tersendiri di `profiles/`. Pada run pertama cookie disuntikkan ke profile; run berikutnya browser langsung memakai state yang sudah tersimpan — tidak perlu inject ulang.

---

## Think Mode

Qwen AI memiliki tiga mode berpikir yang dapat dipilih:

| Mode | Keterangan |
|---|---|
| `auto` | Qwen memilih sendiri apakah perlu berpikir dalam atau tidak |
| `thinking` | Mode berpikir mendalam — respons lebih lambat tapi lebih akurat |
| `fast` | Mode cepat tanpa proses reasoning panjang |

Default mode dikonfigurasi di `config.py` (`QWEN_CONFIG.default_think_mode`, default: `"fast"`).

### Cara Penggunaan

**Via CLI:**

```bash
# Gunakan mode thinking (mendalam)
python main.py --prompt "Jelaskan konsep monad" --think-mode thinking

# Gunakan mode fast (cepat)
python main.py --prompt "Apa itu list?" --think-mode fast

# Gunakan mode auto (Qwen yang memilih)
python main.py --prompt "Buat REST API dengan FastAPI" --think-mode auto
```

**Via Library:**

```python
import asyncio
from scrapers.qwen_scraper import QwenScraper

async def main():
    # Think mode di-set saat inisialisasi (berlaku untuk semua prompt)
    async with QwenScraper(headless=True, think_mode="thinking") as q:
        result = await q.scrape("Jelaskan algoritma Dijkstra")
    print(result["response"])

    # Think mode per-prompt (override global)
    async with QwenScraper(headless=True, think_mode="auto") as q:
        result = await q.scrape("Halo", think_mode="fast")   # override ke fast
    print(result["response"])

asyncio.run(main())
```

**Via `send_prompt` langsung:**

```python
async with QwenScraper(headless=True) as q:
    await q._goto_new_chat()
    response = await q.send_prompt(
        "Jelaskan recursion",
        mode="new",
        think_mode="thinking",   # override per-call
    )
    print(response)
```

### Mekanisme Internal

Pemilihan mode bekerja dengan cascade 5 strategi secara berurutan:

1. **Skip** — jika mode yang diminta sudah aktif di UI, tidak ada aksi
2. **Multi-selector trigger** — mencoba 6+ kandidat selector CSS untuk membuka dropdown
3. **JS label scan** — jika semua selector gagal, scan semua elemen DOM yang teks-nya cocok dengan label mode
4. **Multi-selector option click** — klik opsi via 9 pola selector berbeda (rc-select, Ant Design, `role=option`, dll.)
5. **JS brute-force scan** — scan seluruh text node visible sebagai last resort

Jika semua strategi gagal, scraping **tetap dilanjutkan** (tidak diblokir) dan log peringatan ditulis.

### Debug Think Mode

Saat think mode gagal diterapkan, method `debug_think_mode_selectors` otomatis dipanggil. Method ini men-scan seluruh DOM dan mencatat semua elemen visible yang teks-nya adalah `auto`, `thinking`, atau `fast` — beserta `tag`, `className`, dan `parentClass`-nya — ke log file.

Untuk melihat hasilnya secara langsung:

```bash
# Jalankan dengan browser visible + log level DEBUG
python main.py --prompt "test" --no-headless --think-mode thinking
tail -f logs/scraper.log
```

Output log akan berisi entri seperti:

```
[INFO] QwenScraper: Think-mode debug scan found 3 element(s):
[{'tag': 'SPAN', 'className': 'qwen-select-thinking-label', 'text': 'fast', ...}, ...]
```

Gunakan `className` dari hasil scan untuk memperbarui selector di `config.py` jika UI Qwen berubah.

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

# Pilih think mode
python main.py --prompt "Jelaskan monad" --think-mode thinking
python main.py --prompt "Apa itu list?" --think-mode fast
python main.py --prompt "Buat fungsi sort" --think-mode auto
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

# Dengan think mode thinking untuk semua prompt
python main.py --prompts-file prompts.txt --concurrent 2 --think-mode thinking
```

---

## Penggunaan sebagai Library

```python
import asyncio
from scrapers.qwen_scraper import QwenScraper

async def main():
    # Single prompt dengan think mode
    async with QwenScraper(headless=True, think_mode="thinking") as q:
        result = await q.scrape("Jelaskan recursion")

    print(result["response"])
    print(f"Code blocks: {result['code_block_count']}")

    # Concurrent / batch dengan think mode
    prompts = ["Apa itu OOP?", "Jelaskan decorator Python"]
    results = await QwenScraper.scrape_many(
        prompts=prompts,
        max_concurrent=2,
        think_mode="fast",
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
| `BROWSER_CONFIG.headless` | `False` | Jalankan browser tanpa UI |
| `BROWSER_CONFIG.slow_mo` | `25` | Delay antar aksi (ms) |
| `PERSISTENT_CONTEXT_CONFIG.enabled` | `True` | Pakai persistent browser profile |
| `QWEN_CONFIG.default_think_mode` | `"fast"` | Think mode default (`auto`/`thinking`/`fast`) |
| `QWEN_CONFIG.timeouts.response_wait` | `300000` | Timeout respons AI (ms) |
| `QWEN_CONFIG.selectors.think_mode_trigger` | `.qwen-select-thinking-label` | Selector tombol dropdown think mode |
| `QWEN_CONFIG.selectors.think_mode_selected` | `.qwen-select-option-selected-label-container` | Selector label mode aktif |
| `ROTATION_CONFIG.max_retries_per_account` | `2` | Max retry per akun |
| `ROTATION_CONFIG.retry_delay` | `5` | Jeda antar retry (detik) |

### Mengubah selector think mode

Jika UI Qwen berubah dan think mode tidak terdeteksi, update dua selector berikut di `config.py`:

```python
QWEN_CONFIG = {
    ...
    "selectors": {
        ...
        # Selector tombol yang diklik untuk membuka dropdown
        "think_mode_trigger": ".qwen-select-thinking-label",

        # Selector label yang menampilkan mode aktif saat ini
        "think_mode_selected": ".qwen-select-option-selected-label-container",

        # Selector container daftar opsi dropdown
        "think_mode_options": ".rc-virtual-list-holder-inner",
    },
    ...
}
```

Jalankan dengan `--no-headless` dan cek log untuk menemukan selector yang benar.

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

**Think mode tidak berubah**
- Jalankan dengan `--no-headless` untuk observasi visual
- Cek `logs/scraper.log` — cari entri `Think-mode debug scan` untuk melihat elemen yang ditemukan
- Perbarui `QWEN_CONFIG.selectors.think_mode_trigger` di `config.py` sesuai hasil scan
- Naikkan `BROWSER_CONFIG.slow_mo` ke 100–200 ms agar dropdown sempat terbuka

**Rate limit terus-menerus**
- Tambah lebih banyak akun di folder `cookies/`
- Naikkan `ROTATION_CONFIG.retry_delay`
- Kurangi `--concurrent` jika menggunakan batch mode

**Response tidak terdeteksi**
- Jalankan dengan `--no-headless` untuk observasi visual
- Naikkan `BROWSER_CONFIG.slow_mo` ke 100–200 ms
- Cek `logs/scraper.log` untuk detail error

**Profile browser korup**
- Hapus folder `profiles/<nama_akun>/` dan jalankan ulang — profile akan dibuat ulang dari cookie file