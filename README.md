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
| 🌐 Distributed worker | `public.py` + `browser_pool.py` untuk mode worker VPS |

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
├── main.py                 # CLI entry point (standalone)
├── public.py               # Local worker – konek ke VPS via WebSocket
├── browser_pool.py         # BrowserPool – pre-warmed browser slot management
├── vps_server.py           # VPS server – menerima request dari luar
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
    async with QwenScraper(headless=True, think_mode="thinking") as q:
        result = await q.scrape("Jelaskan algoritma Dijkstra")
    print(result["response"])

asyncio.run(main())
```

---

## Mode Worker VPS (public.py + browser_pool.py)

Selain mode standalone (`main.py`), AIChatScraper mendukung arsitektur **distributed worker** di mana:

- **`vps_server.py`** berjalan di VPS — menerima request dari client luar
- **`public.py`** berjalan di mesin lokal (Windows/Linux) — konek ke VPS via WebSocket dan memproses task menggunakan **BrowserPool**

### Arsitektur BrowserPool

```
Startup (sekali):
  BrowserPool.start()
    ├── Slot #0  → browser warm, cookie: account1.json  [IDLE]
    ├── Slot #1  → browser warm, cookie: account2.json  [IDLE]
    ├── ...
    └── Slot #N  → browser warm, cookie: accountN.json  [IDLE]

Task masuk:
  mode NEW      → acquire slot idle mana saja
  mode CONTINUE → acquire slot dengan cookie yang SAMA dengan session awal
                  (tunggu slot itu idle, tidak fallback ke cookie lain)

Setelah task selesai:
  → slot kembali IDLE, siap task berikutnya (tanpa cold-start)

Slot crash:
  → auto-respawn di background dengan cookie yang sama (maks 3x)
```

Keuntungan utama dibanding versi lama (spawn browser per task):

| | Versi lama | BrowserPool |
|---|---|---|
| Cold-start per task | ~5–15 detik | ~0 detik |
| Browser launch | Setiap task | Sekali saat startup |
| Overhead per request | Tinggi | Minimal |
| Konsistensi akun CONTINUE | ❌ Bisa salah slot | ✅ Cookie-pinned |

### Menjalankan Worker

```bash
# Jalankan worker lokal, konek ke VPS
python public.py --vps ws://YOUR_VPS_IP:9000/ws/worker --workers 20 --token YOUR_TOKEN

# Tampilkan jendela browser (debug)
python public.py --vps ws://... --workers 4 --no-headless

# Set session TTL 2 jam
python public.py --vps ws://... --workers 10 --session-ttl 7200

# Override think mode default untuk semua slot
python public.py --vps ws://... --workers 10 --think-mode fast
```

### Referensi CLI public.py

| Argumen | Default | Keterangan |
|---|---|---|
| `--vps` | *(wajib)* | WebSocket URL VPS, contoh: `ws://1.2.3.4:9000/ws/worker` |
| `--token` | `None` | Token autentikasi (harus sama dengan VPS) |
| `--workers` | `4` | Jumlah slot browser di pool |
| `--no-headless` | `False` | Tampilkan jendela browser |
| `--cookies-dir` | `./cookies` | Folder file cookie JSON |
| `--session-ttl` | `3600` | Session TTL dalam detik |
| `--reconnect-delay` | `5.0` | Jeda sebelum reconnect ke VPS (detik) |
| `--think-mode` | dari config | Default think mode: `auto`, `thinking`, atau `fast` |

### Session & Continue Mode (Worker)

Session dikelola oleh `SessionStore` di `public.py`. Setiap session menyimpan:

- `session_id` — pengenal unik
- `cookie_file` — `Path` lengkap ke cookie file yang dipakai (dikunci sejak request NEW pertama)
- `conversation_url` — URL conversation Qwen yang aktif

Saat request CONTINUE datang, pool **hanya** akan memilihkan slot dengan `cookie_file` yang sama — bukan slot sembarang — sehingga akun Qwen konsisten dengan history percakapan yang tersimpan di `conversation_url`.

### Cookie per Slot

Jumlah cookie file yang tersedia vs `--workers`:

```
# Cookie = 3, workers = 6 → wrap round-robin
Slot #0 → account1.json
Slot #1 → account2.json
Slot #2 → account3.json
Slot #3 → account1.json   ← wrap
Slot #4 → account2.json
Slot #5 → account3.json

# Artinya 2 slot per akun; request CONTINUE ke akun tertentu
# menunggu salah satu dari 2 slot tersebut idle.
```

---

## Penggunaan CLI (Standalone)

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

# Pilih think mode
python main.py --prompt "Jelaskan monad" --think-mode thinking
python main.py --prompt "Apa itu list?" --think-mode fast
```

### Multi-Prompt Concurrent

```bash
# Jalankan semua prompt secara bersamaan (maks 3 browser)
python main.py --prompts-file prompts.txt --concurrent 3

# Dengan think mode thinking untuk semua prompt
python main.py --prompts-file prompts.txt --concurrent 2 --think-mode thinking
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

**Think mode tidak berubah**
- Jalankan dengan `--no-headless` untuk observasi visual
- Cek `logs/scraper.log` — cari entri `Think-mode debug scan`
- Perbarui `QWEN_CONFIG.selectors.think_mode_trigger` di `config.py` sesuai hasil scan

**Rate limit terus-menerus**
- Tambah lebih banyak akun di folder `cookies/`
- Naikkan `ROTATION_CONFIG.retry_delay`
- Kurangi `--concurrent` / `--workers`

**Worker tidak bisa konek ke VPS**
- Cek token — harus sama antara `public.py --token` dan `vps_server.py --token`
- Pastikan port VPS terbuka dan URL WebSocket benar (`ws://` bukan `http://`)
- Worker akan auto-reconnect setiap `--reconnect-delay` detik

**Slot browser di pool crash / dead**
- Worker akan otomatis respawn slot tersebut di background (maks 3 percobaan)
- Status pool ter-log setiap 60 detik — cek `logs/scraper.log` untuk entri `Pool status`
- Jika semua slot dead, restart worker

**Mode CONTINUE tidak nyambung ke percakapan sebelumnya**
- Pastikan `X-Session-ID` (atau `session_id`) dari response pertama disimpan dan dikirim kembali
- Cek apakah session belum expired (default TTL: 1 jam, ubah via `--session-ttl`)
- Worker menjamin slot yang sama (per cookie) dipakai untuk CONTINUE — tapi jika semua slot dengan cookie tersebut dead, request akan fallback ke slot lain