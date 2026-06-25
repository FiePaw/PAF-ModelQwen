# AIChatScraper – Qwen AI Scraper & API Bridge

Async Python scraper untuk Qwen AI (`chat.qwen.ai`) menggunakan **Playwright** dengan arsitektur class-based, persistent browser profile, pre-warmed browser pool, cookie persistence, rotasi akun otomatis, dan model distributed worker.

---

## ⚡ Fitur Utama

| Fitur | Keterangan |
| :--- | :--- |
| **Async & Playwright** | Performa tinggi dengan `asyncio` + Playwright Async Chromium. |
| **Think Mode Selection** | Pilih mode berpikir Qwen: `auto` (otomatis), `thinking` (reasoning mendalam), atau `fast` (respons cepat). |
| **Multi-Account Rotation** | Rotasi cookie otomatis saat mendeteksi rate-limit, session expired, atau kuota harian habis. |
| **Persistent Browser Profile** | Browser profiles tersimpan di `profiles/` untuk menyimpan cookies dan session state agar tidak perlu login ulang. |
| **BrowserPool (Pre-warmed)** | Menghilangkan *cold-start* browser. Browser dibuka dan di-login sejak startup dan siap menerima task. |
| **Attachment Paste via CDP** | Mengunggah gambar, PDF, dokumen, audio/video menggunakan injeksi clipboard lewat Chrome DevTools Protocol (CDP) + `Ctrl+V`. |
| **Penanganan Error Tangguh** | Auto-restart browser saat terjadi page crash, perbaikan otomatis untuk tanda kutip JSON yang tidak di-escape (`unescaped quotes`), serta mekanisme *corrective feedback*. |
| **Distributed Worker** | Dapat dihubungkan ke VPS server (`vps_server.py`) via WebSocket untuk mengekspos endpoint API OpenAI-compatible secara terdistribusi. |
| **Fitur Media & Pencarian Bawaan** | Mendukung generasi gambar (`create_image`), video (`create_video`), dan pencarian web (`web_search`) melalui interaksi tombol UI Qwen. |

---

## 📁 Struktur Folder

```
PAF-ModelQwen-main/
├── scrapers/
│   ├── __init__.py
│   ├── base_scraper.py      # BaseAIChatScraper – ABC untuk siklus browser & rotasi cookie
│   ├── qwen_scraper.py      # QwenScraper – implementasi interaksi UI Qwen, upload, & media
│   └── utils.py             # Helper functions (pretty logging, JSON helper, token counter)
├── config.py                # File konfigurasi utama (timeout, selector, phrase rate limit)
├── main.py                  # CLI entry point untuk eksekusi standalone (single/batch)
├── browser_pool.py          # BrowserPool – manajemen slot browser pre-warmed (idle/busy/dead)
├── public.py                # Local worker – terhubung ke VPS server via WebSocket
├── newpublic_BETA.py        # Local worker (Beta) – dengan optimalisasi dan penanganan session
├── PublicForward/
│   └── ForVPS/
│       ├── start.sh
│       └── vps_server.py    # Server WebSocket di VPS untuk menerima request & mendelegasikan task
├── cookies/                 # Penyimpanan file cookie JSON dari browser
├── profiles/                # Penyimpanan persistent browser profiles per akun (dibuat otomatis)
├── dataSession/             # Penyimpanan cache data session lokal (dibuat otomatis)
├── output/                  # Hasil output scraping JSON
│   └── code/                # Potongan kode yang berhasil diekstrak
└── logs/                    # Log aktivitas scraping
```

---

## 🚀 Instalasi & Setup

### 1. Kloning dan Konfigurasi Environment
```bash
# Buat virtual environment
python -m venv .venv
source .venv/bin/activate        # Linux/macOS
.venv\Scripts\activate           # Windows

# Install dependencies
pip install -r requirements.txt
pip install -r requirements_api.txt   # Jika menggunakan mode worker/vps

# Install browser Chromium untuk Playwright
playwright install chromium
```

### 2. Ekspor Cookie Akun
Qwen AI menggunakan cookie untuk autentikasi sesi.
1. Pasang ekstensi **Cookie-Editor** di Google Chrome atau Firefox.
2. Buka `https://chat.qwen.ai` dan lakukan **login**.
3. Buka ekstensi Cookie-Editor → klik **Export** → **Export as JSON**.
4. Simpan file di dalam folder `cookies/` dengan nama bebas (contoh: `cookies/akun_utama.json`).
5. Untuk mendukung rotasi otomatis, Anda dapat meletakkan beberapa file cookie (misal: `account1.json`, `account2.json`, dll.) di folder tersebut.

---

## 💻 Cara Penggunaan (Mode Standalone)

Mode standalone menggunakan berkas `main.py` untuk menjalankan prompt secara lokal tanpa melalui server perantara.

### 1. Menjalankan Single Prompt
```bash
# Membuka sesi percakapan baru dengan Qwen berpikir mendalam
python main.py --prompt "Jelaskan konsep monad di fungsional programming" --think-mode thinking

# Menjalankan browser secara visual (non-headless) untuk memantau proses
python main.py --prompt "Halo Qwen" --no-headless

# Menyimpan potongan kode yang dihasilkan di response secara terpisah
python main.py --prompt "Buat FastAPI REST API sederhana" --save-code
```

### 2. Melanjutkan Percakapan (Mode Continue)
Sistem dapat melanjutkan sesi percakapan sebelumnya dengan menggunakan bendera `--mode continue`.
```bash
python main.py --prompt "Tambahkan unit test untuk kode sebelumnya" --mode continue
```

### 3. Menjalankan Batch Prompt Secara Concurrent
Jika Anda memiliki daftar pertanyaan dalam sebuah berkas teks (satu baris per prompt), Anda dapat menjalankannya secara paralel.
```bash
# Menjalankan prompt dari file prompts.txt dengan 3 browser sekaligus
python main.py --prompts-file prompts.txt --concurrent 3
```

---

## 🌐 Mode Distributed Worker (API Bridge)

Arsitektur distributed worker dirancang agar Anda dapat mengakses kapasitas scraping Qwen Anda lewat endpoint API yang di-host di VPS publik.

```
[External Client] ─── HTTP REST ───> [vps_server.py] 
                                            │ (WebSocket)
                                            v
                                    [public.py (Local Worker)]
                                            │ (BrowserPool)
                                            v
                                    [Playwright Chromium]
                                            │
                                            v
                                     [chat.qwen.ai]
```

### 1. Jalankan WebSocket Server di VPS
Jalankan server penerima di mesin VPS Anda. Server ini akan mendengarkan koneksi WebSocket dari worker lokal dan menyediakan API REST HTTP yang kompatibel dengan format OpenAI Chat Completions.
```bash
python PublicForward/ForVPS/vps_server.py --port 9000 --token TOKEN_KEAMANAN_ANDA
```

### 2. Jalankan Worker Lokal (Mesin Desktop)
Jalankan worker di PC lokal Anda agar terhubung ke VPS. Worker ini akan membuka slot browser di latar belakang dan menunggu instruksi tugas.
```bash
# Menjalankan worker lokal terhubung ke VPS dengan 4 browser pre-warmed
python public.py --vps ws://IP_VPS_ANDA:9000/ws/worker --workers 4 --token TOKEN_KEAMANAN_ANDA
```
*Catatan:* Anda juga dapat menggunakan berkas `newpublic_BETA.py` yang memiliki penanganan manajemen sesi (`SessionStore`) yang lebih optimal untuk menyimpan status percakapan di disk (`dataSession/`).

---

## 🛠️ Konfigurasi Lanjutan

Anda dapat menyesuaikan parameter operasi scraper di dalam berkas [config.py](file:///C:/Users/SPIN/Desktop/PAF-ModelQwen-main/config.py):
* **`BROWSER_CONFIG`**: Mengatur resolusi viewport, user agent, lambatnya simulasi aksi (`slow_mo`), dan mode visual.
* **`QWEN_CONFIG`**: Berisi selector DOM elemen input, tombol kirim, indikator typing, serta pilihan default think mode (`fast`/`thinking`/`auto`).
* **`ROTATION_CONFIG`**: Mengatur frasa-frasa pendeteksi rate limit dan session expired, jumlah maksimum coba ulang sebelum berpindah akun (`max_retries_per_account`), dan waktu jeda antar-rotasi.

---

## 🔍 Troubleshooting & Penanganan Masalah

### 1. JSON Parse Error (Tanda Kutip Tidak Di-escape)
Qwen terkadang membalas dengan JSON yang cacat karena adanya kutip ganda literal di tengah teks. Sistem ini otomatis mendeteksinya dan menggunakan modul reparasi regex di `base_scraper.py` untuk meng-escape tanda kutip tersebut sebelum diparsing ulang oleh Python.

### 2. Rate Limit & Pengurangan Kuota
Jika log menampilkan *warning* rate limit, disarankan untuk:
* Menambahkan lebih banyak file cookie akun di folder `cookies/`.
* Mengurangi jumlah konkurensi (`--concurrent` atau `--workers`).
* Meningkatkan nilai `retry_delay` di `config.py` agar memberi jeda nafas pada browser.

### 3. Chromium Crash atau Hilang
Pastikan browser biner Playwright telah terinstal sempurna dengan menjalankan:
```bash
playwright install chromium --with-deps
```
