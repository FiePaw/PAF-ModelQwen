# Changelog – AIChatScraper VPS WebSocket Proxy

Semua perubahan penting pada project ini didokumentasikan di sini.  
Format mengikuti [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased] – 2026-05-16 — Browser Restart & Rate Limit Recovery

### `config.py` — Tambah: Kategori rate limit & konfigurasi browser restart

#### Perubahan

Rate limit phrases dipecah menjadi dua kategori dengan alur penanganan berbeda:

| Key | Isi | Alur |
|---|---|---|
| `rate_limit_restart_first_phrases` | `allocated quota exceeded`, `token limit`, `quota exceeded`, `usage limit`, `daily limit`, `increase your quota limit` | Restart browser → retry → jika masih gagal → rotate akun |
| `rate_limit_rotate_phrases` | `rate limit`, `too many requests`, `please try again later`, `request limit`, `you've reached` | Langsung rotate akun |
| `rate_limit_phrases` | Gabungan keduanya | Dipakai `is_rate_limited()` untuk deteksi umum |

Tambah key konfigurasi baru:

```python
"max_browser_restarts": 3,      # maks restart per akun sebelum fallback rotate
"browser_restart_delay": 5,     # jeda sebelum restart browser (detik)
```

Tambah `page_crash_phrases` untuk deteksi halaman crash fatal:

```python
"page_crash_phrases": [
    "oops! something unexpected happened",
    "something unexpected happened",
    "failure code:",
    "try refreshing",
    "oops! there was an issue connecting",
]
```

---

### `scrapers/base_scraper.py` — Tambah: `_is_page_crashed()`, `restart_browser()`, alur rate limit bertahap

#### Masalah

Dua jenis error dari gambar debug yang diterima:

1. **`Allocated quota exceeded`** (rate limit Alibaba Cloud / token limit) — sebelumnya langsung rotate akun, padahal sering kali cukup diselesaikan dengan restart browser + sesi baru pada akun yang sama.
2. **`Oops! Something unexpected happened`** (halaman Qwen crash) — tidak ada penanganan khusus, browser dibiarkan dalam kondisi crash dan request berikutnya pasti gagal.

#### Perubahan

**Method baru `_is_page_crashed()`**

Deteksi crash halaman dengan scan teks body terhadap `page_crash_phrases`. Jika `inner_text()` sendiri gagal (halaman tidak responsif), dianggap crash.

```python
async def _is_page_crashed(self) -> bool: ...
```

**Method baru `restart_browser()`**

Tutup semua resource Playwright (context, browser, playwright instance) lalu relaunch dengan cookie/profile akun yang sama. Counter `browser_restart_count` dilacak per-akun dan direset ke 0 setiap kali rotate ke akun baru.

```python
async def restart_browser(self, cookie_file: Path | None = None) -> bool: ...
```

**Alur baru `scrape()` untuk rate limit quota/token**

```
Response mengandung "allocated quota exceeded" / "token limit" / dll
  │
  ├─ browser_restart_count < max_browser_restarts (default 3)?
  │     → restart_browser() → retry attempt yang sama (attempt -= 1)
  │       browser_restart_count += 1
  │
  └─ browser_restart_count habis / restart gagal
        → fallback _rotate_account() ke akun berikutnya
          browser_restart_count = 0  ← reset untuk akun baru
```

**Alur baru `scrape()` untuk page crash**

```
_is_page_crashed() == True  (sebelum kirim prompt ATAU setelah dapat response)
  │
  ├─ browser_restart_count < max_browser_restarts?
  │     → restart_browser() → retry
  │
  └─ restart habis → break (all attempts exhausted)
```

Selain itu, `browser_restart_count` juga direset ke `0` setiap kali:
- Rotate ke akun baru (karena counter berlaku per-akun)
- Session expired → rotate

---

### `scrapers/qwen_scraper.py` — Fix: Raise exception jika page crash setelah navigate

#### Perubahan

`_goto_new_chat()` sekarang memanggil `_is_page_crashed()` setelah navigate berhasil. Jika crash terdeteksi, raise `RuntimeError` sehingga `scrape()` menangkapnya di blok `except Exception` dan memicu `restart_browser()`.

```python
# Sebelum: navigate selesai → langsung lanjut, tidak cek apakah halaman sehat
await self._page.goto(self.BASE_URL, ...)

# Sesudah: cek crash dulu
if await self._is_page_crashed():
    raise RuntimeError("Page crashed after navigation to Qwen")
```

---

### `browser_pool.py` — Fix: Deteksi crash pada slot sebelum dan sesudah task

#### Masalah

Slot pool di mode worker VPS bisa dalam kondisi crash (halaman error) tanpa terdeteksi, sehingga task berikutnya yang mengambil slot tersebut pasti gagal.

#### Perubahan

**`acquire()` — cek crash sebelum yield slot ke task**

```python
# Sebelum: slot idle langsung diserahkan ke task
yield slot.scraper, slot.cookie_file.name, slot.slot_id

# Sesudah: cek crash dulu, kalau crash → mark DEAD + respawn + cari slot lain
if slot.scraper and await slot.scraper._is_page_crashed():
    slot.mark_dead()
    self._schedule_respawn(slot)
    continue   # kembali cari slot idle yang sehat
```

**`_reset_slot_page()` — cek crash setelah task selesai**

```python
# Sebelum: langsung reset flag → slot kembali IDLE
slot.scraper._conversation_started = False

# Sesudah: cek crash dulu
if await slot.scraper._is_page_crashed():
    slot.mark_dead()
    self._schedule_respawn(slot)
    return   # tidak kembalikan ke IDLE
```

Jika `_reset_slot_page()` menandai slot DEAD, `acquire()` tidak akan memaksa status kembali ke IDLE — respawn sudah dijadwalkan otomatis.

---

## [Unreleased] – 2026-05-15 — Optimasi & Model Selector

### `public.py` — Fix: Skip `goto()` pada CONTINUE jika browser sudah di halaman yang benar

#### Masalah

Setiap request CONTINUE selalu memanggil `scraper._page.goto(conv_url)` meskipun browser
sudah berada di URL conversation yang tepat. Pada arsitektur `BrowserPool`, slot browser
berpotensi tetap berada di halaman yang sama setelah turn sebelumnya selesai — terutama
jika tidak ada request lain yang "merebut" slot tersebut di antaranya.

`goto()` pada halaman Qwen (SPA React berat) membutuhkan **2–6 detik** hanya untuk load,
ditambah `_wait_page_ready()` yang ikut polling setelahnya. Ini overhead murni yang sia-sia
jika browser sudah di halaman yang benar.

#### Fix

Sebelum memanggil `goto()`, bandingkan `scraper._page.url` dengan `conv_url` yang tersimpan
di session. Jika sudah cocok, `goto()` dan `_wait_page_ready()` di-skip sepenuhnya.

```python
# Sebelum (selalu goto):
await scraper._page.goto(conv_url, wait_until="domcontentloaded", timeout=30_000)
await _wait_page_ready(scraper._page, worker_label)

# Sesudah (cek dulu):
current_url_now = scraper._page.url
already_there = conv_url in current_url_now or current_url_now in conv_url
if already_there:
    logger.info("Skip goto() — browser sudah di halaman: %s", conv_url)
else:
    await scraper._page.goto(conv_url, ...)
    await _wait_page_ready(...)
```

#### Dampak

| Skenario | Sebelum | Sesudah |
|---|---|---|
| Slot langsung dipakai lagi (URL sama) | goto() + wait ~2–6s | Skip → 0s |
| Slot dipakai session lain dulu (URL beda) | goto() + wait ~2–6s | goto() + wait ~2–6s (tidak berubah) |

Turn pertama CONTINUE tetap melakukan `goto()` karena sesaat setelah mode NEW selesai,
slot dikembalikan ke pool dan bisa diambil session lain. Keuntungan terbesar terasa pada
percakapan dengan ritme cepat (turn pendek, jeda singkat antar request).

---

### `vps_server.py` + `public.py` — Fitur: Model Selector (Cookie via Field `model`)

Field `model` di request body kini berfungsi sebagai **selector akun**. Sebelumnya field ini
hanya di-echo kembali di response tanpa efek apapun pada pemilihan slot browser.

#### `vps_server.py`

- **`_available_cookie_names`** (global set baru) — menampung nama cookie (tanpa ekstensi)
  yang dilaporkan semua worker aktif. Diisi saat worker konek dan mengirim pesan `register`.

- **`GET /v1/models`** — listing sekarang dinamis dari `_available_cookie_names`, bukan
  hardcoded `["qwen", "qwen-turbo"]`. Jika belum ada worker yang konek, fallback ke `"qwen"`.
  ```json
  { "data": [
      {"id": "account1", ...},
      {"id": "account2", ...}
  ]}
  ```

- **`worker_endpoint`** — pesan registrasi worker kini membawa field `cookie_names`:
  `{"type": "register", "max_concurrent": 4, "cookie_names": ["account1", "account2"]}`.
  VPS meng-update `_available_cookie_names` dari nilai ini.

- **`POST /v1/chat/completions`** — jika `model` bukan `"qwen"` / `"qwen-turbo"` (generic),
  nilainya dipakai sebagai `preferred_cookie` dan disertakan di payload task ke worker.

#### `public.py`

- **Pesan registrasi** — saat konek ke VPS, worker kini melaporkan `cookie_names` yang
  dikumpulkan dari nama unik semua slot di pool:
  `[s.cookie_file.stem for s in pool._slots]` (deduplicated).

- **`TaskProcessor.process()`** — membaca `preferred_cookie` dari payload. Untuk task NEW,
  jika ada `preferred_cookie` dari field `model`, nilai tersebut diteruskan ke
  `pool.acquire(preferred_cookie=...)` sehingga slot dengan cookie yang tepat dipilih.

- **Fix unpack** — `pool.acquire()` yield 3 nilai `(scraper, cookie_name, slot_id)`.
  Unpack sebelumnya hanya 2 nilai → `ValueError: too many values to unpack`. Diperbaiki
  menjadi `(scraper, cookie_name, _slot_id)`.

#### Behavior

| Nilai `model` | Perilaku |
|---|---|
| `"account1"`, `"account2"`, dst. | Worker pilih slot dengan cookie file yang sesuai |
| `"qwen"` / `"qwen-turbo"` (generic) | Worker pilih slot idle mana saja (round-robin) |

> Untuk request CONTINUE, `preferred_cookie` ditentukan dari session yang tersimpan (akun
> awal), bukan dari field `model` di request berikutnya — akun tidak bisa berganti di tengah
> sesi.

---

### `HowToUseAPI(Updated).md` — Update: Dokumentasi Model Selector

- Section **`GET /v1/models`** diperbarui: jelaskan bahwa listing dinamis dari cookie aktif di worker.
- Section baru **"Memilih Akun (Model Selector)"** ditambahkan: cara kerja, tabel perilaku,
  contoh Python `list_accounts()` + `chat(account=...)`.
- Semua contoh kode: `"model": "qwen"` → `"model": "account1"` (atau nama akun spesifik).
- Tabel request body: keterangan field `model` diperbarui.

---

### `example/account_selector.py` — Baru: Contoh Pemilihan Akun

Script demonstrasi dua skenario:

1. **Percakapan 3 turn** dengan akun yang dipilih secara spesifik.
2. **Dua percakapan paralel** ke dua akun berbeda menggunakan `asyncio` + `httpx`.

Mendukung `--account`, `--host`, `--port` via CLI.

---

### `example/chat.py` — Update: Tambah Pemilihan Akun Interaktif

Versi baru `chat.py` (CLI chatbot) kini mendukung pemilihan akun:

- Prompt input menampilkan akun aktif: `You [22:30:01][account1] ›`
- **`/switch`** — ganti akun via picker interaktif, session otomatis reset.
- **`/account`** — lihat akun yang sedang dipakai.
- **`/accounts`** — lihat semua akun tersedia (refresh dari server).
- Argumen `--account` untuk langsung menentukan akun tanpa picker.

---

Sesi perbaikan bug menyeluruh untuk fitur `create_image`, `create_video`, dan `web_search`
yang ditambahkan di versi sebelumnya. Semua fitur kini berfungsi end-to-end.

---

### `PublicForward/ForVPS/vps_server.py`

#### Fix: `task_type` tidak pernah diteruskan ke worker (NameError + AttributeError)

Tiga bug terpisah yang ditemukan dan diperbaiki secara bertahap:

1. **`NameError: media_urls`** — variable `media_urls` dan `result_task_type` di-assign di blok
   yang salah (duplikat) sehingga tidak terdefinisi saat response dibangun. Fix: assign langsung
   setelah `result` diterima dari worker, berlaku untuk semua `task_type`.

2. **`AttributeError: task_type`** — field `task_type` tidak pernah ditambahkan ke
   `ChatCompletionRequest` Pydantic model, sehingga Qwen selalu menjalankan mode `chat`.
   Fix: tambah `task_type: Optional[str] = None` ke model.

3. **`task_type` tidak di-forward ke worker** — meski sudah ada di model, nilai `task_type`
   tidak dimasukkan ke `task_payload` yang dikirim ke worker via WebSocket.
   Fix: tambah `"task_type": req.task_type or "chat"` ke payload.

Setelah ketiga fix ini, `create_image` / `create_video` / `web_search` baru bisa
benar-benar dieksekusi oleh worker.

#### Tambah: field `urls` dan `task_type` di JSON response

Response non-streaming kini menyertakan:
- `"urls": [...]` di level atas — berisi URL media untuk `create_image`/`create_video`
- `"x_meta.task_type"` — task type yang dieksekusi
- `"x_meta.url_count"` — jumlah URL yang dikembalikan

---

### `scrapers/qwen_scraper.py`

#### Fix: tombol Create Image/Video/Web search tidak ditemukan

Beberapa lapisan fix untuk menemukan dan mengklik tombol yang benar di DOM Qwen:

1. **Tombol ada di dalam submenu** — tombol tidak langsung visible di halaman, harus buka
   dropdown dulu via tombol trigger. Tambah `_open_toolbar_menu()` dan `_find_and_click_menu_item()`.

2. **Selector `mode-select-open` yang benar** — tombol trigger dropdown Qwen punya class
   `mode-select-open`. Selector sebelumnya (`mode-select-btn`, `toolbar-btn`, `aria-haspopup`)
   tidak cocok.

3. **Selector Ant Design yang tepat** — item menu generate ada di dalam:
   ```
   .ant-dropdown-menu-item .mode-select-dropdown-item
   ```
   Sebelumnya selector tidak cocok dengan struktur DOM Qwen.

4. **Casing keyword** — Qwen pakai `"Create image"` (lowercase `i`), bukan `"Create Image"`.

#### Fix: navigate ke halaman baru setelah klik tombol mode

`send_prompt(mode="new")` selalu memanggil `_goto_new_chat()` → halaman di-navigate ulang
→ mode yang sudah diklik hilang. Fix: pisahkan method submit:

- **`_submit_prompt(prompt)`** — kirim prompt tanpa navigasi, untuk `web_search`
- **`_submit_prompt_media(prompt, mode)`** — kirim prompt + tunggu media, untuk
  `create_image` / `create_video`

#### Fix: create_image/video stuck setelah generate selesai

`_wait_for_generation` menunggu `.qwen-markdown` / `.chat-response-message` yang tidak
pernah muncul di mode Create Image/Video (Qwen hanya render elemen gambar). Tambah:

- **`_count_media_elements()`** — hitung elemen `.qwen-chat-response-control-card` /
  `.qwen-image` di DOM
- **`_wait_for_generation_media(mode)`** — poll sederhana: tunggu `is_generating()=False`
  AND `count_media_elements()>0`, tanpa log progress, tanpa timeout besar

#### Fix: selector ekstraksi URL media salah

Selector `.chat-message-container img` dan `.chat-response-message video` tidak ada di DOM
Qwen untuk mode generate. Semua diganti dengan:
```
.qwen-chat-response-control-card img[src]
.qwen-image img[src]
.qwen-chat-response-control-card video[src]
```

#### Fix: method `web_search` dan `_click_web_search_button` hilang

Kedua method terhapus tidak sengaja saat replace baris di commit sebelumnya. Dipulihkan kembali.

---

### `HowToUseAPI(Updated).md`

Tambah tiga section baru:

- **Generate Gambar (Create Image)** — format request/response, contoh Python + curl,
  catatan timeout 120–180 detik
- **Generate Video (Create Video)** — format request/response, contoh Python, catatan
  timeout 300 detik
- **Pencarian Web (Web Search)** — format request/response, contoh Python + curl

Update bagian lain:
- Daftar Isi: tambah link ke tiga section baru
- Tabel request body: tambah field `task_type`
- Referensi response body: tambah field `urls` dan `x_meta.task_type` / `url_count`
- Tips Praktis: tambah tip tentang `task_type`, field `urls`, dan timeout per task type

---

## [Unreleased] – 2026-05-12 (2)

### Fitur Baru: Web Search via Tombol "Web search" Qwen

Menambahkan dukungan `task_type: "web_search"` yang mengaktifkan mode pencarian web
di Qwen AI sebelum mengirim prompt. Output tetap berupa teks response chat biasa,
namun Qwen menelusuri internet terlebih dahulu sehingga jawaban diperkaya data terkini.

---

#### `qwen_scraper.py` — Tambah: `_click_web_search_button()` + `web_search()`

##### Selector `_SEL_WEB_SEARCH_BTN` (baru)

Daftar selector CSS untuk menemukan tombol "Web search" di toolbar Qwen (ada di submenu More):
`aria-label`, `data-testid`, class name, `title`, dan fallback scan innerText.

##### Method `_click_web_search_button()` (baru)

Mengklik tombol "Web search" dengan strategi dua tahap:
1. Coba selector spesifik.
2. Fallback: scan semua `<button>` / `[role="button"]` via `innerText` / `title` / `aria-label`.

Pola identik dengan `_click_create_button()` — konsisten di seluruh codebase.

##### Method `web_search(prompt, timeout)` (baru)

High-level method untuk web search:

1. Navigasi ke halaman chat baru.
2. Klik tombol "Web search" via `_click_web_search_button()`.
3. Kirim prompt dan tunggu response teks Qwen.

Return dict:
```python
{
    "success" : bool,
    "prompt"  : str,
    "response": str,   # jawaban Qwen berbasis pencarian web
    "error"   : str | None,
}
```

Berbeda dengan `create_image`/`create_video`, tidak ada ekstraksi URL media —
output web search selalu berupa teks.

---

#### `public.py` — Update: Tambah `"web_search"` di `task_type` handler

- `task_type` kini mendukung 4 nilai: `"chat"` | `"create_image"` | `"create_video"` | `"web_search"`.
- `_run_media()` diperbarui dengan `elif`/`else` chain:
  - `create_image` → `scraper.create_image(prompt)`
  - `create_video` → `scraper.create_video(prompt)`
  - `web_search`   → `scraper.web_search(prompt)` ← baru
- Komentar field `urls` diperbarui: `"hanya terisi untuk create_image/create_video"`.

---

#### `example/web_search.py` — Baru: Contoh Penggunaan Web Search

```python
requests.post("http://108.137.15.61:9000/v1/chat/completions", json={
    "model"    : "qwen",
    "task_type": "web_search",
    "messages" : [{"role": "user", "content": "Berita AI terbaru hari ini"}],
})
```

Response:
```json
{
    "success"  : true,
    "task_type": "web_search",
    "response" : "Berdasarkan pencarian web, ...",
    "urls"     : []
}
```

Dilengkapi argparse (`--host`, `--port`, `--prompt`, `--timeout`) dan error handling.

---

## [Unreleased] – 2026-05-12

### Fitur Baru: Generate Gambar & Video via "Create Image" / "Create Video"

Menambahkan dukungan untuk meminta Qwen AI membuat gambar dan video langsung dari prompt teks,
menggunakan tombol **Create Image** dan **Create Video** yang tersedia di toolbar Qwen.
Implementasi mengikuti pola yang sama dengan fitur upload attachment (clipboard CDP).

---

#### `qwen_scraper.py` — Tambah: Method Generate Gambar & Video

##### Method `_click_create_button(mode)` (baru)

Mengklik tombol "Create Image" atau "Create Video" di toolbar Qwen secara otomatis.

- **Strategi multi-selector** — mencoba selector CSS spesifik satu per satu (aria-label,
  data-testid, class name). Jika semua gagal, scan seluruh `<button>` dan `[role="button"]`
  di halaman via `innerText` / `title` / `aria-label` — pola yang sama dengan attachment upload.
- **mode `"image"`** → mencari tombol "Create Image".
- **mode `"video"`** → mencari tombol "Create Video".
- Return `True` jika berhasil diklik, `False` jika tombol tidak ditemukan.

##### Method `_wait_media_output(mode, timeout)` (baru)

Menunggu dan mengekstrak URL gambar/video hasil generate dari DOM halaman Qwen.

- Poll setiap 1 detik hingga elemen `<img>` (image) atau `<video>` (video) berisi URL `http`
  muncul di area response.
- Selector mencakup class name Qwen yang umum (`generated-image`, `image-result`, `gen-image`,
  dst.) serta fallback generik berdasarkan domain CDN (`aliyuncs`, `qwen`).
- Log progress setiap 15 detik agar proses tidak tampak freeze.
- Timeout default: **120 detik** untuk gambar, **180 detik** untuk video.
- Jika URL tidak terdeteksi dari DOM, coba ekstrak dari teks response via regex
  (fallback untuk format response teks yang menyertakan link langsung).

##### Method `create_image(prompt, timeout)` (baru)

High-level method untuk generate gambar:

1. Navigasi ke halaman chat baru (`_goto_new_chat()`).
2. Klik tombol "Create Image" via `_click_create_button("image")`.
3. Kirim prompt dan tunggu response.
4. Ekstrak URL gambar via `_wait_media_output("image")`.
5. Fallback regex dari teks response jika URL tidak terdeteksi di DOM.

Return dict:
```python
{
    "success" : bool,
    "prompt"  : str,
    "urls"    : list[str],   # URL gambar hasil generate
    "response": str,         # teks response Qwen (jika ada)
    "error"   : str | None,
}
```

##### Method `create_video(prompt, timeout)` (baru)

Identik dengan `create_image` namun untuk video. Timeout default lebih panjang (180 detik)
karena render video membutuhkan waktu lebih lama dari render gambar.

---

#### `public.py` — Tambah: `task_type` di `TaskProcessor.process()`

- **Field baru `task_type`** dibaca dari payload: `"chat"` (default) | `"create_image"` | `"create_video"`.
- **Shortcut path** — jika `task_type` adalah `create_image` atau `create_video`, task langsung
  dijalankan via `scraper.create_image()` / `scraper.create_video()` **tanpa** melalui alur
  session (tidak ada session_id, mode continue, dll).
- **Log worker** diperbarui — baris log penerima task kini menyertakan `type=` agar mudah
  dibedakan antara task chat biasa vs generate media:
  ```
  Worker#0 → Request [abc12345] type=create_image session=new
  ```
- **Response `create_image` / `create_video`** menyertakan field tambahan:
  ```python
  {
      "success"     : True,
      "task_type"   : "create_image",
      "prompt"      : str,
      "urls"        : list[str],
      "response"    : str,
      "cookie_file" : str,
      "account_used": str,
  }
  ```

---

#### `example/create_image.py` — Baru: Contoh Penggunaan Create Image

Script contoh lengkap untuk memanggil fitur create image dari Python:

```python
requests.post("http://108.137.15.61:9000/v1/chat/completions", json={
    "model"    : "qwen",
    "task_type": "create_image",
    "messages" : [{"role": "user", "content": "Kucing astronaut di luar angkasa"}],
})
```

Response yang dikembalikan server:
```json
{
    "success"  : true,
    "task_type": "create_image",
    "urls"     : ["https://cdn.qwen.ai/...gambar.jpg"],
    "response" : "..."
}
```

Dilengkapi argparse (`--host`, `--port`, `--prompt`, `--timeout`) dan error handling lengkap.

---

#### `example/create_video.py` — Baru: Contoh Penggunaan Create Video

Identik dengan `create_image.py` namun untuk video. Default timeout 240 detik.

```python
requests.post("http://108.137.15.61:9000/v1/chat/completions", json={
    "model"    : "qwen",
    "task_type": "create_video",
    "messages" : [{"role": "user", "content": "Sunrise di pegunungan, sinematik"}],
})
```

---

## [Unreleased] – 2026-05-08

### `utils.py` — Baru: Pretty Console Logger dengan Dukungan Windows ANSI

#### Latar Belakang
Output log sebelumnya menggunakan `logging.Formatter` standar yang menghasilkan baris panjang
tanpa warna, sulit dibaca saat banyak worker berjalan bersamaan. Di Windows, percobaan awal
menambahkan ANSI color codes justru menghasilkan karakter escape mentah (`←[92m`) di cmd.exe
karena Virtual Terminal Processing belum aktif.

#### Perubahan di `setup_logger()`
Fungsi tetap memiliki signature yang sama — tidak ada perubahan di sisi pemanggil.

- **`_PrettyConsoleFormatter`** (baru) — formatter khusus console dengan layout kolom:
  ```
  HH:MM:SS  LEVEL    logger_name     │  pesan
  ```
  Setiap kolom memiliki lebar tetap sehingga pesan sejajar vertikal meskipun nama logger
  berbeda-beda (`local_worker`, `browser_pool`, `QwenScraper`, dst.).

- **Highlight otomatis dalam pesan** — kata kunci penting diberi warna berbeda tanpa mengubah
  teks aslinya:

  | Elemen | Warna |
  |---|---|
  | `Worker#N` | Biru bold |
  | `[request_id]` | Kuning |
  | `NEW` / `CONTINUE` | Cyan bold / Magenta bold |
  | `✅` / `❌` / `🔌` / `🔄` | Hijau / Merah / Cyan / Kuning |
  | `idle=` / `busy=` / `dead=` | Hijau / Kuning / Merah |
  | `Pool ready`, `Terhubung` | Hijau bold |
  | `Error`, `Gagal`, `Timeout` | Merah |

- **Level styling**:
  - `DEBUG` — abu-abu redup (tidak mengganggu saat verbose)
  - `INFO` — hijau
  - `WARN` — kuning
  - `ERROR` — merah
  - `CRIT` — background merah

- **File handler tidak berubah** — rotating file handler tetap menulis plain text tanpa kode
  ANSI, sehingga log file tetap bisa dibaca dengan text editor biasa.

#### Fix — ANSI codes muncul sebagai teks mentah di Windows

**Masalah:** cmd.exe dan PowerShell lama tidak mengaktifkan ANSI processing secara default,
sehingga kode seperti `\033[92m` tampil sebagai `←[92m` di layar.

**Fix:** Tambah `_enable_windows_ansi()` yang memanggil Windows API
(`SetConsoleMode` + flag `ENABLE_VIRTUAL_TERMINAL_PROCESSING`) sebelum output pertama ditulis.
Deteksi dilakukan satu kali saat import via `_supports_color()`:

- Jika `stderr.isatty()` False (output di-pipe/redirect) → semua warna dinonaktifkan, plain text.
- Jika Windows dan VT mode berhasil diaktifkan → warna tampil normal di cmd.exe / PowerShell / Windows Terminal.
- Jika Windows dan VT mode gagal (versi lama, tidak ada console) → fallback plain text.
- Linux / macOS dengan terminal → warna aktif langsung.

```
Sebelum (Windows cmd):
  ←[2m02:57:15←[0m  ←[92mINFO   ←[0m  ←[2mlocal_worker  ←[0m  ←[2m│←[0m  ...

Sesudah (semua platform):
  02:57:15  INFO     local_worker    │  Worker#0 🔌 Konek ke VPS: ws://...
```

---

## [Unreleased] – 2026-05-08 (2)

### Fitur Baru: Upload File / Attachment via Clipboard CDP

Menambahkan dukungan upload file (gambar, PDF, dokumen, dll) ke Qwen AI melalui mekanisme
clipboard berbasis CDP (Chrome DevTools Protocol). Fitur ini bekerja pada percakapan baru
maupun turn lanjutan (mode `continue`).

---

#### `qwen_scraper.py` — Tambah: Class `Attachment` + Upload via Clipboard CDP

##### Class `Attachment` (baru)

Class helper untuk merepresentasikan satu file attachment sebelum dikirim ke Qwen.

- **`Attachment.from_path(path)`** — buat dari file lokal di mesin yang menjalankan worker.
- **`Attachment.from_base64(b64_data, filename, mime_type?)`** — buat dari string base64.
  Mendukung raw base64 (`"iVBOR..."`) maupun Data URI (`"data:image/png;base64,iVBOR..."`).
- **`att.to_temp_file()`** — tulis data ke file sementara (dipakai secara internal).
- **`att.is_supported()`** — validasi ringan apakah MIME type dikenal Qwen.

##### Method upload baru di `QwenScraper`

- **`_upload_attachments(attachments)`** — orkestrator utama. Membuat CDP session sekali
  untuk semua file, iterasi per attachment, jeda 1.2 detik antar file.

- **`_paste_attachment_via_cdp(cdp, att)`** — inti upload. Melakukan 6 langkah di dalam
  satu `page.evaluate()`:
  1. Decode base64 → `Uint8Array` di browser.
  2. Buat `Blob` + `File` object dengan nama dan MIME type yang benar.
  3. Coba tulis ke `navigator.clipboard` (opsional, tidak blocking jika gagal).
  4. Masukkan `File` ke `DataTransfer`.
  5. Fokus ke textarea input Qwen.
  6. Dispatch `ClipboardEvent('paste')` ke textarea.

  Setelah `paste`, tunggu preview 5 detik. Jika preview tidak muncul, baru coba
  `DragEvent('drop')` sebagai fallback — **tidak** keduanya sekaligus, untuk mencegah
  file terdaftar dua kali di Qwen.

- **`_wait_attachment_preview(timeout)`** — poll setiap 0.3 detik menunggu elemen
  thumbnail/preview attachment muncul di UI Qwen. Return `True` jika terdeteksi,
  `False` jika timeout. Tidak blocking — scrape tetap dilanjutkan walau preview tidak
  terdeteksi.

##### Fix: File muncul 2x di Qwen

Versi awal men-dispatch `paste` dan `drop` sekaligus dalam satu JS call. Qwen merespons
keduanya sehingga file terdaftar dua kali. Diperbaiki dengan urutan sekuensial:

```
dispatch paste → tunggu preview 5s
  ├── preview muncul? → ✅ selesai, drop TIDAK dijalankan
  └── tidak muncul?  → dispatch drop → tunggu preview 5s lagi
```

##### Kenapa clipboard, bukan file chooser?

Qwen menggunakan custom upload handler — bukan `<input type="file">` standar. Metode
`expect_file_chooser` dan `set_input_files()` dari Playwright tidak dapat menginterceptnya.
Clipboard paste via CDP bekerja langsung di level DOM event sehingga tidak bergantung pada
implementasi UI Qwen.

---

#### `vps_server.py` — Tambah: Model `AttachmentPayload` + Field `attachments`

- **`AttachmentPayload`** (Pydantic model baru):
  ```python
  class AttachmentPayload(BaseModel):
      filename: str
      data: str          # raw base64 atau Data URI
      mime_type: Optional[str] = None
  ```
- **`ChatCompletionRequest`** — tambah field `attachments: Optional[list[AttachmentPayload]]`.
- **`chat_completions` endpoint** — attachment di-serialize ke `task_payload` dan diteruskan
  ke worker sebagai list of dict `{filename, data, mime_type}`.
- Log request kini menyertakan jumlah attachment: `attachments=N`.

---

#### `public.py` — Tambah: Parse Attachment di `TaskProcessor.process()`

- `process()` membaca field `attachments` dari payload, mengkonversi setiap item ke objek
  `Attachment` via `Attachment.from_base64()` (untuk data dari API) atau `Attachment.from_path()`
  (untuk path lokal).
- Attachment yang gagal di-parse di-skip dengan log warning — tidak menggagalkan seluruh task.
- List `Attachment` diteruskan ke `scraper.scrape(..., attachments=attachments)`.

---

#### `base_scraper.py` — Update: `scrape()` terima parameter `attachments`

`scrape(prompt, mode, attachments)` menerima `attachments: list | None = None` dan
meneruskannya ke `send_prompt()` via `extra kwargs`.

---

#### `HowToUseAPI.md` — Update: Dokumentasi Fitur Attachment

- Tambah section baru **"Mengirim File / Attachment"** dengan:
  - Tabel format field `attachments` (`filename`, `data`, `mime_type`)
  - Tabel tipe file yang didukung (gambar, dokumen, spreadsheet, teks, audio, video)
  - Contoh curl (satu file dan multi-file)
  - Contoh Python dari file lokal
  - Contoh Python dari bytes/memory (misal screenshot PIL)
- Update tabel **Request Body** di section referensi — tambah baris `attachments` dan sub-field.
- Update contoh JSON request body — sertakan contoh `attachments`.
- Update **Tips Praktis** — tambah tip bahwa attachment bisa dikirim di turn mana saja.

---


### `browser_pool.py` — Baru: Pre-warmed Browser Pool

#### Latar Belakang
Versi lama `public.py` men-spawn browser baru untuk setiap task yang masuk (cold-start ~5–15
detik per request). `BrowserPool` menggantikan pendekatan ini dengan mempertahankan N browser
yang sudah warm dan login sejak startup, sehingga overhead per request menjadi hampir nol.

#### Desain
- **`BrowserSlot`** — satu slot mewakili satu browser yang dedicated ke satu cookie file.
  Status slot: `STARTING → IDLE → BUSY → DEAD`.
- **`BrowserPool.start()`** — spawn semua N slot secara paralel saat startup. Jumlah slot
  dikonfigurasi via `--workers` (sama seperti sebelumnya). Jika cookie file lebih sedikit dari
  `--workers`, cookie di-wrap round-robin.
- **`BrowserPool.acquire(preferred_cookie)`** — context manager yang meminjam satu slot idle.
  - Mode `NEW` (`preferred_cookie=None`): pilih slot idle mana saja, prioritas slot paling
    lama idle untuk meratakan beban.
  - Mode `CONTINUE` (`preferred_cookie="accountX.json"`): **hanya** pertimbangkan slot dengan
    cookie file yang cocok. Tunggu slot tersebut idle — tidak fallback ke cookie lain — agar
    akun Qwen konsisten dengan conversation yang tersimpan.
- **Auto-respawn** — slot yang crash otomatis di-respawn di background dengan cookie yang sama
  (maks 3 percobaan). Selama respawn berlangsung slot tidak tersedia untuk task baru.
- **`BrowserPool.get_cookie_path(cookie_name)`** — resolve nama file ke `Path` lengkap,
  dipakai `TaskProcessor` saat menyimpan session setelah task NEW.
- **Status diagnostik** — `status_summary()` mengembalikan jumlah slot per status; di-log
  setiap 60 detik oleh worker.

#### Perbandingan dengan versi lama

| | Versi lama | BrowserPool |
|---|---|---|
| Browser launch | Setiap task | Sekali saat startup |
| Cold-start per request | ~5–15 detik | ~0 detik |
| Konsistensi akun CONTINUE | ❌ Bisa salah slot | ✅ Cookie-pinned |
| Respawn otomatis | ❌ | ✅ (maks 3x per slot) |

---

### `public.py` — Refactor: Integrasi BrowserPool + Fix Session & Cookie

#### Perubahan arsitektur

- **`CookieRotator` dihapus** — tugasnya diambil alih sepenuhnya oleh `BrowserPool`.
- **`TaskProcessor`** tidak lagi spawn browser. Setiap task cukup memanggil
  `async with pool.acquire(preferred_cookie=...) as (scraper, cookie_name)`.
- **`_main()`** melakukan warm-up pool terlebih dahulu sebelum konek ke VPS, sehingga
  saat task pertama masuk semua browser sudah siap.

#### Fix Bug #1 — `Session.cookie_file` dikembalikan ke `Path`

`Session` sempat menyimpan `cookie_file` sebagai `str` (nama file saja). Ini menyebabkan
`TaskProcessor` tidak bisa meneruskan `Path` yang benar ke `SessionStore.get_or_create()` dan
ke pool saat mode CONTINUE. Dikembalikan ke `cookie_file: Path` seperti versi aslinya.

#### Fix Bug #3 — Pool tidak menjamin slot dengan cookie yang sama untuk CONTINUE

`pool.acquire()` sebelumnya selalu mengambil slot idle mana saja tanpa mempertimbangkan cookie.
Request CONTINUE bisa mendapat slot dengan akun berbeda, sehingga conversation di Qwen tidak
nyambung. Sekarang `preferred_cookie` diteruskan ke `acquire()` dan pool menunggu slot yang
tepat.

#### Fix — Log nama akun selalu `account1`

`_cookie_index` di `BaseAIChatScraper.__init__()` selalu dimulai dari `0`. Di arsitektur pool,
`_discover_accounts()` memuat semua cookie ke `_cookie_files`, tapi `_cookie_index` tidak
pernah diset sesuai posisi `slot.cookie_file` — akibatnya `scrape()` selalu log `account1`
meski browser-nya pakai cookie berbeda.

**Fix** (di `browser_pool.py`, method `_init_slot`): setelah `_discover_accounts()`, cari
posisi `slot.cookie_file` di dalam `_cookie_files` dan set `_cookie_index` ke posisi tersebut.

```
Sebelum:  Slot#3 (account2.json) → _cookie_index=0 → log "account1" ❌
Sesudah:  Slot#3 (account2.json) → _cookie_index=1 → log "account2" ✅
```

#### Fix — `Could not locate chat input field` pada mode CONTINUE

**Masalah:** Setelah `goto(conv_url)`, kode lama hanya menunggu `await asyncio.sleep(1.5)`
sebelum memanggil `scrape()`. Jika Qwen masih memproses output dari request sebelumnya
(stop button masih visible, input field belum mount), `scrape()` langsung gagal dengan
`RuntimeError: Could not locate chat input field`.

**Fix:** `asyncio.sleep(1.5)` diganti dengan fungsi `_wait_page_ready(page, worker_label)`
yang bekerja dalam dua langkah berurutan:

1. **Tunggu stop button hilang** — poll setiap 0.5 detik sampai semua kandidat selector
   stop/cancel button tidak visible (artinya Qwen sudah idle). Timeout 60 detik.
2. **Tunggu input field visible** — poll sampai textarea/input field benar-benar mount dan
   visible di DOM. Timeout 60 detik.

Jika salah satu langkah melewati timeout, log warning ditulis tapi tidak raise exception —
`scraper.scrape()` tetap dipanggil dan retry mechanism-nya yang menangani lebih lanjut.

```
Sebelum:  goto() → sleep(1.5s) → scrape()               ← tebak-tebakan timing
Sesudah:  goto() → tunggu idle → tunggu input → scrape() ← deterministik
```

---

### `vps_server.py` — Fix Bug #2: Typo `bind_session()`

#### Masalah
Sticky routing mode CONTINUE di VPS selalu gagal — session tidak pernah dirouting ke worker
yang benar.

#### Penyebab
Typo satu baris di `WorkerManager.bind_session()`:

```python
# Sebelum (salah):
self._session_worker[session_id] = session_id   # value seharusnya worker_id

# Sesudah (benar):
self._session_worker[session_id] = worker_id
```

`_session_worker` adalah dict `session_id → worker_id`. Karena value-nya diisi dengan
`session_id` bukan `worker_id`, lookup `_session_worker[sid]` mengembalikan `session_id`
lalu dicari di `_workers` — tidak ketemu — sehingga VPS selalu routing CONTINUE ke worker
sembarang.

---

## [Unreleased] – 2026-04-27

### `qwen_scraper.py` — Fix: Scraping Stuck pada Mode Continue

#### Masalah
Proses scraping berhenti (stuck) setelah log `Submitting prompt` tanpa pernah masuk ke fase
menunggu response. Terjadi khususnya pada request mode `continue` setelah beberapa giliran
percakapan.

#### Penyebab
1. **`pre_count` diambil setelah `input.fill()`** — snapshot jumlah elemen response diambil
   *setelah* prompt diketik, sehingga ada jendela race condition di mana DOM bisa berubah
   sebelum `send` diklik. Akibatnya `pre_count` sudah sama dengan `cur_count` sejak awal dan
   fase deteksi `appeared` tidak pernah terpenuhi.

2. **Fase deteksi `appeared` bergantung tunggal pada `cur_count > pre_count`** — Qwen kadang
   me-render ulang seluruh container percakapan alih-alih menambah elemen baru, sehingga count
   tidak naik meski response sudah mulai digenerate.

3. **Think-mode trigger dicari di halaman conversation** — Pada halaman conversation (mode
   `continue`), Qwen menyembunyikan dropdown think-mode. Kode lama terus mencarinya,
   menghasilkan warning spam dan membuang ~2 detik tiap request.

4. **Send button tidak dicek apakah `disabled`** — Tombol send kadang dalam state `disabled`
   sesaat sebelum Qwen siap menerima input berikutnya.

#### Perubahan
- **`send_prompt()`**: Pindahkan pengambilan `pre_count` ke *sebelum* `input.fill()` dan
  `input.type()` untuk menghilangkan race condition.
- **`_wait_for_generation()`**: Dipisah menjadi dua fase:
  - *Fase 1* (maks 10 detik): tunggu sinyal generasi dimulai — `is_generating() == True`
    **atau** `cur_count > pre_count`. Jika tidak ada sinyal dalam 10 detik, lanjut dengan
    warning (tidak stuck selamanya).
  - *Fase 2*: tunggu konten stabil seperti sebelumnya.
- **`_ensure_page_ready()`**: Pada mode `continue`, langsung set `_think_mode_applied = True`
  tanpa mencari trigger (karena memang tidak ada di halaman conversation).
- **`_find_send_button_enabled()`** (method baru): Menunggu hingga 5 detik sampai send button
  benar-benar enabled (`disabled` attribute tidak ada), menggantikan `_find_send_button()` yang
  langsung klik tanpa pengecekan.
- **`_count_response_elements()`** (method baru): Helper terpusat untuk menghitung elemen
  response di DOM, dipakai bersama oleh `send_prompt()` dan `_wait_for_generation()`.

---

### `vps_server.py` — Fix: Request NEW Gagal saat Worker Sedang Menangani CONTINUE

#### Masalah
- Request mode `NEW` mendapat error `503 – Tidak ada worker tersedia` saat satu-satunya worker
  sedang sibuk mengerjakan task `CONTINUE`.
- Request mode `NEW` yang berhasil masuk ke antrian mendapat error `All attempts exhausted`
  karena state internal scraper terkontaminasi.

#### Penyebab
`WorkerManager` menggunakan `busy: bool` — satu worker hanya bisa memegang **1 slot** sekaligus.
Tidak ada perbedaan perlakuan antara task NEW (independen) dan CONTINUE (terikat session).

#### Perubahan
- **`WorkerManager` didesain ulang**:
  - `busy: bool` diganti dengan `active_tasks: int` + `max_concurrent: int` per worker.
  - Tambah `_session_worker: dict[str, str]` untuk sticky routing session → worker.
  - `get_idle_worker()` diganti dengan `get_worker_for_task(session_id)`:
    - **NEW** (`session_id=None`): pilih worker dengan `active_tasks` terkecil (load balancing).
      Tidak perlu menunggu worker manapun selesai.
    - **CONTINUE** (`session_id` ada): routing ke worker yang sudah memegang session tersebut.
      Jika belum ada binding, pilih worker dengan slot kosong dan ikat session ke sana.
  - `set_idle()` diganti dengan `release_task(worker_id, session_id)` yang mengurangi counter.
  - `register()` menerima parameter `max_concurrent` dari worker saat koneksi pertama.
- **`worker_endpoint` (WebSocket handler)**:
  - Menunggu pesan `{"type": "register", "max_concurrent": N}` dari worker saat pertama konek.
  - Setelah result diterima, binding `session_id → worker_id` disimpan untuk routing CONTINUE
    berikutnya.
  - `set_idle()` diganti dengan `release_task()`.
- **`chat_completions` endpoint**:
  - `get_idle_worker()` diganti dengan `get_worker_for_task(session_id=incoming_sid)`.
  - Log diperjelas dengan informasi mode (`NEW` / `CONTINUE`).
  - `release_task()` dipanggil pada semua jalur error (timeout, gagal kirim).

---

### `public.py` (local_worker.py) — Fix: Concurrency dan Session Isolation

#### Masalah
- `_semaphore` global di `LocalWorker` memblok semua task, termasuk NEW yang tidak
  seharusnya menunggu task CONTINUE selesai.
- Tidak ada proteksi jika dua task CONTINUE untuk session yang sama entah bagaimana masuk
  bersamaan ke worker yang sama.

#### Perubahan
- **Hapus `_semaphore` global** dari `LocalWorker`. Concurrency kini dikelola di dua level:
  1. VPS (`active_tasks < max_concurrent`) — mencegah overflow task ke satu worker.
  2. `TaskProcessor` session lock — mencegah dua CONTINUE untuk session yang sama berjalan
     paralel.
- **`TaskProcessor`**: Tambah `_session_locks: dict[str, asyncio.Lock]` dengan cleanup otomatis
  lock yang sudah tidak dipakai (`_cleanup_session_locks()`). Task NEW tidak kena lock sama
  sekali.
- **`LocalWorker`**: Kirim pesan `{"type": "register", "max_concurrent": N}` ke VPS segera
  setelah WebSocket terkoneksi, agar VPS tahu kapasitas worker ini.
- **Worker label** (`Worker#0`, `Worker#1`, dst.) ditambahkan ke semua log entry untuk
  memudahkan debug ketika ada beberapa worker berjalan bersamaan.
- **`--workers` default** dinaikkan dari `1` → `4` karena concurrency kini benar-benar paralel.
- **`process()`** menerima parameter `worker_label` untuk konsistensi log.

---

## Behavior Setelah Perbaikan

```
Skenario: 1 worker, ada task CONTINUE berjalan, lalu masuk task NEW

Sebelum:
  Worker busy=True → NEW dapat 503 atau masuk antrian → gagal

Sesudah:
  Worker active_tasks=1, max_concurrent=4 → NEW langsung dapat slot baru
  CONTINUE dan NEW berjalan paralel di worker yang sama
  Dua CONTINUE untuk session yang sama mengantri via session lock (aman)
```

```
Skenario: think-mode pada request continue ke-2 dst.

Sebelum:
  QwenScraper: Setting think mode → 'fast'
  QwenScraper: WARNING Could not locate think-mode trigger – skipping (tiap request)

Sesudah:
  QwenScraper: Continue mode: think-mode UI not available – skipping (sekali, tanpa retry)
```

```
Skenario: mode CONTINUE saat halaman masih memproses output sebelumnya

Sebelum:
  goto(conv_url) → sleep(1.5s) → scrape()
  ❌ RuntimeError: Could not locate chat input field (jika Qwen belum idle)

Sesudah:
  goto(conv_url) → tunggu stop button hilang → tunggu input visible → scrape()
  ✅ Input field pasti sudah mount sebelum prompt dikirim
```

```
Skenario: log nama akun pada worker dengan banyak cookie

Sebelum:
  Slot#3 pakai account2.json → log "Attempt 1/12 using account 'account1'" ❌

Sesudah:
  Slot#3 pakai account2.json → log "Attempt 1/12 using account 'account2'" ✅
```