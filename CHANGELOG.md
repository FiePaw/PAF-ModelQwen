# Changelog – AIChatScraper VPS WebSocket Proxy

Semua perubahan penting pada project ini didokumentasikan di sini.  
Format mengikuti [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased] – 2026-05-08

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