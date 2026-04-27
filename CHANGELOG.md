# Changelog – AIChatScraper VPS WebSocket Proxy

Semua perubahan penting pada project ini didokumentasikan di sini.  
Format mengikuti [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

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