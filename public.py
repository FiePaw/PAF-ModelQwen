#!/usr/bin/env python3
"""
public.py – Local Worker dengan BrowserPool
============================================
Konek ke VPS via WebSocket, menerima task, menjalankan QwenScraper
dari pre-warmed BrowserPool, dan mengembalikan hasil ke VPS.

Perbedaan utama dari versi lama:
  • BrowserPool dibuat SEKALI saat startup → N browser sudah warm & login
  • Task tidak lagi spawn browser baru → tidak ada cold-start per task
  • Setiap slot dedicated ke 1 cookie file
  • Slot crash → auto-respawn di background

Flow:
  [public.py] → konek WebSocket → [vps_server.py]
  [vps_server.py]   → kirim task      → [public.py]
  [public.py] → ambil slot dari pool → send_prompt → kirim result → [vps_server.py]

Usage:
  python public.py --vps ws://YOUR_VPS_IP:9000/ws/worker --workers 20 --token YOUR_TOKEN
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

import websockets
from websockets.exceptions import ConnectionClosed

sys.path.insert(0, str(Path(__file__).parent))

from browser_pool import BrowserPool
from config import COOKIES_DIR, PERSISTENT_CONTEXT_CONFIG
from scrapers.utils import setup_logger

logger = setup_logger("local_worker")


# ─── Session Store ─────────────────────────────────────────────────────────────

@dataclass
class Session:
    session_id: str
    cookie_file: Path                      # Path lengkap — dipakai untuk preferred_cookie di pool
    conversation_url: str | None = None
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    turn_count: int = 0

    def touch(self) -> None:
        self.last_used = time.time()
        self.turn_count += 1


class SessionStore:
    def __init__(self, ttl: int = 3600) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = asyncio.Lock()
        self.ttl = ttl

    async def create(self, cookie_file: Path, session_id: str | None = None) -> Session:
        async with self._lock:
            sid = session_id or uuid.uuid4().hex
            session = Session(session_id=sid, cookie_file=cookie_file)
            self._sessions[sid] = session
            return session

    async def get(self, session_id: str) -> Session | None:
        async with self._lock:
            s = self._sessions.get(session_id)
            if s is None:
                return None
            if time.time() - s.last_used > self.ttl:
                del self._sessions[session_id]
                return None
            return s

    async def get_or_create(self, session_id: str | None, cookie_file: Path) -> Session:
        if session_id:
            existing = await self.get(session_id)
            if existing:
                return existing
        return await self.create(cookie_file, session_id=session_id)

    async def cleanup_expired(self) -> int:
        """Hapus semua session expired. Return jumlah yang dihapus."""
        async with self._lock:
            now = time.time()
            expired = [sid for sid, s in self._sessions.items()
                       if now - s.last_used > self.ttl]
            for sid in expired:
                del self._sessions[sid]
            return len(expired)

    async def cleanup_expired(self) -> int:
        """Hapus semua session expired. Return jumlah yang dihapus."""
        async with self._lock:
            now = time.time()
            expired = [sid for sid, s in self._sessions.items()
                       if now - s.last_used > self.ttl]
            for sid in expired:
                del self._sessions[sid]
            return len(expired)



# ─── Page Readiness Helper ─────────────────────────────────────────────────────

# Selector stop/cancel button yang muncul saat Qwen sedang memproses output.
# Selama tombol ini visible, halaman belum siap untuk menerima input baru.
_STOP_BTN_SELECTORS = [
    "button[aria-label='Stop']",
    "button[aria-label='Stop generating']",
    "button[data-testid='stop-button']",
    # fallback: cari button yang mengandung teks stop
    "button.stop-btn",
    ".generation-stop-btn",
]

# Selector input field — sama dengan kandidat di QwenScraper._find_input()
_INPUT_SELECTORS = [
    "textarea[placeholder]",
    "textarea#chat-input",
    "textarea[data-testid='chat-input']",
    "div[contenteditable='true'][data-testid]",
    "div[contenteditable='true']",
    "textarea",
]

_PAGE_READY_TIMEOUT = 60.0   # detik maks menunggu halaman siap
_PAGE_READY_POLL   = 0.5    # interval polling


async def _wait_page_ready(page, worker_label: str = "?") -> None:
    """
    Tunggu sampai halaman conversation Qwen benar-benar siap menerima input:

    Langkah 1 — Tunggu stop/cancel button HILANG dari halaman.
                 Selama Qwen masih memproses output sebelumnya, tombol ini visible.
                 Timeout: _PAGE_READY_TIMEOUT detik.

    Langkah 2 — Tunggu input field muncul dan visible.
                 Pastikan React component sudah mount dan input siap diketik.
                 Timeout: _PAGE_READY_TIMEOUT detik.

    Kedua langkah menggunakan polling ringan dengan asyncio.sleep
    agar tidak memblokir event loop.
    """
    deadline = asyncio.get_event_loop().time() + _PAGE_READY_TIMEOUT

    # ── Langkah 1: tunggu stop button hilang ──────────────────────────────────
    logger.debug("Worker#%s Menunggu Qwen selesai memproses...", worker_label)
    while asyncio.get_event_loop().time() < deadline:
        stop_visible = False
        for sel in _STOP_BTN_SELECTORS:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    stop_visible = True
                    break
            except Exception:
                continue
        if not stop_visible:
            break
        await asyncio.sleep(_PAGE_READY_POLL)
    else:
        logger.warning(
            "Worker#%s Stop button masih visible setelah %.0fs — lanjut anyway",
            worker_label, _PAGE_READY_TIMEOUT,
        )

    # ── Langkah 2: tunggu input field visible ─────────────────────────────────
    logger.debug("Worker#%s Menunggu input field muncul...", worker_label)
    deadline2 = asyncio.get_event_loop().time() + _PAGE_READY_TIMEOUT
    while asyncio.get_event_loop().time() < deadline2:
        for sel in _INPUT_SELECTORS:
            try:
                el = await page.wait_for_selector(sel, timeout=1_000, state="visible")
                if el:
                    logger.debug(
                        "Worker#%s Input field siap (%s)", worker_label, sel
                    )
                    return   # ✅ halaman siap
            except Exception:
                continue
        await asyncio.sleep(_PAGE_READY_POLL)

    # Kalau sampai sini, input field tidak ditemukan — biarkan scraper yang handle error
    logger.warning(
        "Worker#%s Input field tidak ditemukan setelah %.0fs — scraper akan retry sendiri",
        worker_label, _PAGE_READY_TIMEOUT,
    )


# ─── Task Processor ────────────────────────────────────────────────────────────

class TaskProcessor:
    """
    Memproses satu task menggunakan slot dari BrowserPool.
    Tidak ada spawn browser di sini — semuanya diambil dari pool.
    """

    def __init__(self, pool: BrowserPool, session_ttl: int = 3600) -> None:
        self.pool = pool
        self.sessions = SessionStore(ttl=session_ttl)
        # Per-session lock untuk task CONTINUE agar tidak tumpang tindih
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._session_locks_meta: dict[str, float] = {}
        self._locks_mutex = asyncio.Lock()

    async def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        async with self._locks_mutex:
            if session_id not in self._session_locks:
                self._session_locks[session_id] = asyncio.Lock()
            self._session_locks_meta[session_id] = time.time()
            return self._session_locks[session_id]

    async def _cleanup_session_locks(self, ttl: float = 3600.0) -> None:
        async with self._locks_mutex:
            now = time.time()
            stale = [
                sid for sid, ts in self._session_locks_meta.items()
                if now - ts > ttl and not self._session_locks[sid].locked()
            ]
            for sid in stale:
                del self._session_locks[sid]
                del self._session_locks_meta[sid]

    async def process(self, request_id: str, payload: dict, worker_label: str = "?") -> dict:
        """
        Proses satu task dari VPS.
        Task NEW     → acquire slot idle mana saja dari pool.
        Task CONTINUE → acquire slot dengan cookie yang SAMA dengan session awal,
                        lalu navigate ke conv_url sebelum send_prompt.
        """
        messages   = payload.get("messages", [])
        think_mode = payload.get("think_mode")
        incoming_sid = payload.get("session_id")

        user_msgs = [m["content"] for m in messages if m.get("role") == "user"]
        prompt = user_msgs[-1] if user_msgs else ""

        if not prompt:
            return {"success": False, "error": "Prompt kosong"}

        # ── Resolve session ────────────────────────────────────────────────────
        mode = "new"
        conv_url: str | None = None
        preferred_cookie: str | None = None   # nama file, untuk pool.acquire()
        session_cookie_file: Path | None = None  # Path lengkap, untuk SessionStore

        if incoming_sid:
            existing = await self.sessions.get(incoming_sid)
            if existing:
                mode = "continue"
                conv_url = existing.conversation_url
                # ← FIX Bug #1 & #3: ambil cookie Path dari session, teruskan ke pool
                session_cookie_file = existing.cookie_file
                preferred_cookie    = existing.cookie_file.name
                logger.info(
                    "Worker#%s CONTINUE [%s] session=%s cookie=%s conv_url=%s",
                    worker_label, request_id[:8], incoming_sid[:8],
                    preferred_cookie, conv_url,
                )
            else:
                logger.info(
                    "Worker#%s Session tidak ditemukan/expired [%s] → mode new",
                    worker_label, request_id[:8],
                )

        if mode == "new":
            logger.info("Worker#%s NEW [%s]", worker_label, request_id[:8])

        # Per-session lock hanya untuk CONTINUE (agar 2 request ke session yg sama tidak tabrakan)
        lock: asyncio.Lock | None = None
        if mode == "continue" and incoming_sid:
            lock = await self._get_session_lock(incoming_sid)

        async def _run() -> dict:
            logger.debug(
                "Worker#%s Pool status: %s", worker_label, self.pool.status_summary(),
            )

            # ← FIX Bug #3: teruskan preferred_cookie ke acquire()
            async with self.pool.acquire(preferred_cookie=preferred_cookie) as (scraper, cookie_name):
                try:
                    # Untuk CONTINUE: navigasi ke URL conversation yang tersimpan
                    if mode == "continue" and conv_url and "chat.qwen.ai" in conv_url:
                        logger.info(
                            "Worker#%s Navigasi ke conversation: %s",
                            worker_label, conv_url,
                        )
                        await scraper._page.goto(
                            conv_url, wait_until="domcontentloaded", timeout=30_000
                        )
                        # Tunggu halaman benar-benar siap sebelum scrape():
                        # 1. Tunggu Qwen selesai memproses (stop button hilang)
                        # 2. Tunggu input field visible dan enabled
                        await _wait_page_ready(scraper._page, worker_label)
                        scraper._conversation_started = True

                    # Override think_mode per-request jika dikirim dari VPS
                    if think_mode:
                        scraper._think_mode = think_mode
                        scraper._think_mode_applied = False

                    result = await scraper.scrape(prompt, mode=mode)
                    current_url: str = scraper._page.url

                except asyncio.TimeoutError:
                    return {"success": False, "error": "Timeout: Qwen AI tidak merespons"}
                except Exception as e:
                    logger.error(
                        "Worker#%s Scraper error [%s]: %s",
                        worker_label, request_id[:8], e, exc_info=True,
                    )
                    raise  # biarkan pool.acquire() tangkap dan mark dead

            if not result.get("success"):
                return {"success": False, "error": result.get("error", "Unknown scraper error")}

            response_text: str = result["response"]

            # Resolusi cookie_file sebagai Path untuk disimpan di session:
            # - CONTINUE: pakai Path yang sudah diketahui dari session sebelumnya
            # - NEW: cari Path dari nama cookie yang dikembalikan pool
            if session_cookie_file:
                resolved_cookie_path = session_cookie_file
            else:
                # Cari Path dari pool berdasarkan nama file
                resolved_cookie_path = self.pool.get_cookie_path(cookie_name)

            # ← FIX Bug #1: simpan Path ke session, bukan string
            session = await self.sessions.get_or_create(incoming_sid, resolved_cookie_path)
            if current_url and "chat.qwen.ai" in current_url:
                session.conversation_url = current_url
            session.touch()

            logger.info(
                "Worker#%s ✅ [%s] %d chars | session=%s | cookie=%s | url=%s",
                worker_label, request_id[:8], len(response_text),
                session.session_id[:8], cookie_name,
                session.conversation_url or "-",
            )

            return {
                "success": True,
                "response": response_text,
                "session_id": session.session_id,
                "cookie_file": cookie_name,
                "conversation_url": session.conversation_url or "",
                "account_used": cookie_name,
            }

        if lock:
            async with lock:
                return await _run()
        else:
            return await _run()


# ─── WebSocket Worker ──────────────────────────────────────────────────────────

class LocalWorker:
    """
    Konek ke VPS via WebSocket dan delegasikan task ke TaskProcessor.
    Concurrency diatur oleh BrowserPool (jumlah slot idle).
    """

    _instance_counter: int = 0

    def __init__(
        self,
        vps_url: str,
        processor: TaskProcessor,
        max_concurrent: int = 4,
        token: str | None = None,
        reconnect_delay: float = 5.0,
    ) -> None:
        self.vps_url = vps_url
        self.processor = processor
        self.max_concurrent = max_concurrent
        self.token = token
        self.reconnect_delay = reconnect_delay
        self._running = True
        self._label = str(LocalWorker._instance_counter)
        LocalWorker._instance_counter += 1

    def _build_url(self) -> str:
        if self.token:
            sep = "&" if "?" in self.vps_url else "?"
            return f"{self.vps_url}{sep}token={self.token}"
        return self.vps_url

    async def _handle_task(self, ws, request_id: str, payload: dict) -> None:
        logger.info("Worker#%s ▶ Request [%s]", self._label, request_id[:8])
        try:
            result = await self.processor.process(request_id, payload, worker_label=self._label)
            msg = {"type": "result", "request_id": request_id, "data": result}
        except Exception as e:
            logger.error("Worker#%s Error [%s]: %s", self._label, request_id[:8], e)
            msg = {"type": "error", "request_id": request_id, "message": str(e)}

        try:
            await ws.send(json.dumps(msg))
        except Exception as e:
            logger.error("Worker#%s Gagal kirim result ke VPS: %s", self._label, e)

    async def _keepalive(self, ws) -> None:
        """Kirim ping ke VPS setiap 30 detik supaya koneksi tidak putus."""
        while True:
            await asyncio.sleep(30)
            try:
                await ws.send(json.dumps({"type": "ping"}))
            except Exception:
                break

    async def _status_reporter(self) -> None:
        """Log status pool setiap 60 detik."""
        while self._running:
            await asyncio.sleep(60)
            try:
                s = self.processor.pool.status_summary()
                logger.info(
                    "Pool status: total=%d idle=%d busy=%d dead=%d starting=%d",
                    s["total"], s["idle"], s["busy"], s["dead"], s["starting"],
                )
                # Cleanup expired sessions dan locks secara berkala
                cleaned = await self.processor.sessions.cleanup_expired()
                if cleaned:
                    logger.debug("Cleaned %d expired session(s)", cleaned)
                await self.processor._cleanup_session_locks()
            except Exception:
                pass

    async def _connect_and_run(self) -> None:
        url = self._build_url()
        logger.info("Worker#%s 🔌 Konek ke VPS: %s", self._label, self.vps_url)

        async with websockets.connect(url, ping_interval=20, ping_timeout=30) as ws:
            await ws.send(json.dumps({
                "type": "register",
                "max_concurrent": self.max_concurrent,
            }))
            logger.info(
                "Worker#%s ✅ Terhubung ke VPS! (max_concurrent=%d)",
                self._label, self.max_concurrent,
            )

            keepalive_task = asyncio.create_task(self._keepalive(ws))
            status_task    = asyncio.create_task(self._status_reporter())

            try:
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        logger.warning(
                            "Worker#%s Pesan tidak valid dari VPS: %s",
                            self._label, raw[:100],
                        )
                        continue

                    msg_type = msg.get("type")

                    if msg_type == "task":
                        request_id = msg["request_id"]
                        payload    = msg["payload"]
                        logger.info(
                            "Worker#%s → Request [%s] session=%s",
                            self._label, request_id[:8],
                            (payload.get("session_id") or "new")[:8],
                        )
                        # Non-blocking: task jalan paralel di background
                        asyncio.create_task(self._handle_task(ws, request_id, payload))

                    elif msg_type == "pong":
                        logger.debug("Worker#%s Pong dari VPS", self._label)

                    else:
                        logger.debug(
                            "Worker#%s Pesan tidak dikenal: %s",
                            self._label, msg_type,
                        )
            finally:
                keepalive_task.cancel()
                status_task.cancel()

    async def run(self) -> None:
        """Loop utama dengan auto-reconnect."""
        while self._running:
            try:
                await self._connect_and_run()
            except ConnectionClosed as e:
                logger.warning("❌ Koneksi ke VPS terputus: %s", e)
            except OSError as e:
                logger.warning("❌ Tidak bisa konek ke VPS: %s", e)
            except Exception as e:
                logger.error("❌ Error tidak terduga: %s", e, exc_info=True)

            if self._running:
                logger.info("🔄 Reconnect dalam %.0f detik...", self.reconnect_delay)
                await asyncio.sleep(self.reconnect_delay)

    def stop(self) -> None:
        self._running = False


# ─── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="local-worker",
        description="Local Worker – BrowserPool + WebSocket ke VPS",
    )
    parser.add_argument(
        "--vps", required=True, metavar="URL",
        help="WebSocket URL VPS, contoh: ws://1.2.3.4:9000/ws/worker",
    )
    parser.add_argument(
        "--token", default=None,
        help="Token autentikasi (harus sama dengan --token di vps_server.py)",
    )
    parser.add_argument(
        "--workers", type=int, default=4,
        help="Jumlah slot browser di pool (default: 4)",
    )
    parser.add_argument(
        "--no-headless", action="store_true",
        help="Tampilkan jendela browser",
    )
    parser.add_argument(
        "--cookies-dir", metavar="DIR", type=Path, default=COOKIES_DIR,
    )
    parser.add_argument(
        "--session-ttl", type=int, default=3600,
        help="Session TTL dalam detik (default: 3600)",
    )
    parser.add_argument(
        "--reconnect-delay", type=float, default=5.0,
        help="Jeda sebelum reconnect ke VPS (detik)",
    )
    parser.add_argument(
        "--think-mode", default=None,
        choices=["auto", "thinking", "fast"],
        help="Default think mode untuk semua slot (default: dari config)",
    )
    args = parser.parse_args()

    headless = not args.no_headless

    logger.info("=" * 58)
    logger.info("  Local Worker – AIChatScraper (BrowserPool)")
    logger.info("  VPS         : %s", args.vps)
    logger.info("  Pool size   : %d browser", args.workers)
    logger.info("  Headless    : %s", headless)
    logger.info("  Cookies dir : %s", args.cookies_dir)
    logger.info("  Think mode  : %s", args.think_mode or "dari config")
    logger.info("  Token       : %s", "✅ Aktif" if args.token else "❌ Tidak diset")
    logger.info("=" * 58)

    async def _main() -> None:
        # 1. Buat dan warm-up pool
        pool = BrowserPool(
            cookies_dir=args.cookies_dir,
            pool_size=args.workers,
            headless=headless,
            think_mode=args.think_mode,
        )
        try:
            logger.info("Warming up %d browser slot(s)...", args.workers)
            await pool.start()
            logger.info("Pool ready → %s", pool)
        except Exception as e:
            logger.critical("Gagal start BrowserPool: %s", e)
            sys.exit(1)

        # 2. Buat processor dan worker
        processor = TaskProcessor(pool=pool, session_ttl=args.session_ttl)
        worker = LocalWorker(
            vps_url=args.vps,
            processor=processor,
            max_concurrent=args.workers,
            token=args.token,
            reconnect_delay=args.reconnect_delay,
        )

        # 3. Jalankan worker; tutup pool saat selesai
        try:
            await worker.run()
        finally:
            logger.info("Menutup BrowserPool...")
            await pool.stop()

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        logger.info("Worker dihentikan.")


if __name__ == "__main__":
    main()