#!/usr/bin/env python3
"""
local_worker.py – Runs on Local Windows 10
===========================================
Konek ke VPS via WebSocket, menerima task, menjalankan QwenScraper,
dan mengembalikan hasil ke VPS.

Flow:
  [local_worker.py] → konek WebSocket → [vps_server.py]
  [vps_server.py]   → kirim task      → [local_worker.py]
  [local_worker.py] → jalankan scraper → kirim result → [vps_server.py]

Usage:
  pip install websockets
  python local_worker.py --vps ws://YOUR_VPS_IP:8000/ws/worker
  python local_worker.py --vps ws://YOUR_VPS_IP:8000/ws/worker --token rahasia123
  python local_worker.py --vps ws://YOUR_VPS_IP:8000/ws/worker --workers 2
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
import uuid
from pathlib import Path
from dataclasses import dataclass, field
from typing import AsyncIterator

import websockets
from websockets.exceptions import ConnectionClosed

sys.path.insert(0, str(Path(__file__).parent))

from config import COOKIES_DIR, PERSISTENT_CONTEXT_CONFIG
from scrapers.qwen_scraper import QwenScraper
from scrapers.utils import discover_cookie_files, setup_logger

logger = setup_logger("local_worker")

# ─── Session Store (lokal di Windows) ────────────────────────────────────────

@dataclass
class Session:
    session_id: str
    cookie_file: Path
    conversation_url: str | None = None
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    turn_count: int = 0

    def touch(self):
        self.last_used = time.time()
        self.turn_count += 1


class SessionStore:
    def __init__(self, ttl: int = 3600):
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


# ─── Cookie Rotator ───────────────────────────────────────────────────────────

class CookieRotator:
    def __init__(self, cookies_dir: Path):
        self._files: list[Path] = discover_cookie_files(cookies_dir)
        self._index = 0
        self._lock = asyncio.Lock()
        if self._files:
            logger.info("CookieRotator: %d file tersedia: %s",
                        len(self._files), [f.name for f in self._files])
        else:
            logger.warning("Tidak ada cookie file ditemukan di %s", cookies_dir)

    async def next_cookie(self) -> Path | None:
        async with self._lock:
            if not self._files:
                return None
            cookie = self._files[self._index % len(self._files)]
            self._index += 1
            return cookie


# ─── Task Processor ───────────────────────────────────────────────────────────

class TaskProcessor:
    """Menjalankan scraper untuk satu task yang diterima dari VPS."""

    def __init__(self, headless: bool, cookies_dir: Path, session_ttl: int = 3600):
        self.headless = headless
        self.cookies_dir = cookies_dir
        self.rotator = CookieRotator(cookies_dir)
        self.sessions = SessionStore(ttl=session_ttl)

    async def process(self, request_id: str, payload: dict) -> dict:
        """
        Proses satu task. Return dict hasil yang akan dikirim ke VPS.
        """
        messages = payload.get("messages", [])
        think_mode = payload.get("think_mode")
        incoming_sid = payload.get("session_id")

        # Ambil pesan user terakhir
        user_msgs = [m["content"] for m in messages if m.get("role") == "user"]
        prompt = user_msgs[-1] if user_msgs else ""

        if not prompt:
            return {"success": False, "error": "Prompt kosong"}

        # Resolve session
        cookie_file: Path | None = None
        mode = "new"

        if incoming_sid:
            session = await self.sessions.get(incoming_sid)
            if session:
                mode = "continue"
                cookie_file = session.cookie_file
                conv_url = session.conversation_url
                logger.info("CONTINUE session=%s cookie=%s", incoming_sid[:8], session.cookie_file.name)
            else:
                logger.info("Session tidak ditemukan/expired → mode new")

        if mode == "new":
            cookie_file = await self.rotator.next_cookie()
            conv_url = None
            logger.info("NEW mode cookie=%s", cookie_file.name if cookie_file else "none")

        if not cookie_file:
            return {"success": False, "error": "Tidak ada cookie file tersedia"}

        # Jalankan scraper
        scraper = QwenScraper(
            headless=self.headless,
            cookies_path=cookie_file,
            cookies_dir=self.cookies_dir,
            think_mode=think_mode,
        )

        try:
            await scraper.launch_browser(cookie_file=cookie_file)

            if not PERSISTENT_CONTEXT_CONFIG.get("enabled") and cookie_file:
                await scraper.load_cookies(cookie_file)

            if mode == "continue" and conv_url and "chat.qwen.ai" in conv_url:
                logger.info("Navigasi ke conversation: %s", conv_url)
                await scraper._page.goto(conv_url, wait_until="domcontentloaded", timeout=30_000)
                await asyncio.sleep(2)
                scraper._conversation_started = True

            result = await scraper.scrape(prompt, mode=mode)
            current_url: str = scraper._page.url

        except asyncio.TimeoutError:
            return {"success": False, "error": "Timeout: Qwen AI tidak merespons"}
        except Exception as e:
            logger.error("Scraper error [%s]: %s", request_id[:8], e, exc_info=True)
            return {"success": False, "error": str(e)}
        finally:
            try:
                await scraper.close_browser()
            except Exception:
                pass

        if not result.get("success"):
            return {"success": False, "error": result.get("error", "Unknown scraper error")}

        response_text: str = result["response"]
        account_used: str = result.get("account_used", "unknown")

        # Update / buat session
        session = await self.sessions.get_or_create(incoming_sid, cookie_file)
        if current_url and "chat.qwen.ai" in current_url:
            session.conversation_url = current_url
        session.touch()

        logger.info(
            "✅ Selesai [%s] %d chars session=%s",
            request_id[:8], len(response_text), session.session_id[:8]
        )

        return {
            "success": True,
            "response": response_text,
            "session_id": session.session_id,
            "cookie_file": cookie_file.name,
            "conversation_url": session.conversation_url or "",
            "account_used": account_used,
        }


# ─── WebSocket Worker ──────────────────────────────────────────────────────────

class LocalWorker:
    """
    Konek ke VPS via WebSocket dan proses task yang masuk.
    Mendukung multiple concurrent task dengan asyncio.
    """

    def __init__(
        self,
        vps_url: str,
        processor: TaskProcessor,
        max_concurrent: int = 1,
        token: str | None = None,
        reconnect_delay: float = 5.0,
    ):
        self.vps_url = vps_url
        self.processor = processor
        self.max_concurrent = max_concurrent
        self.token = token
        self.reconnect_delay = reconnect_delay
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._running = True

    def _build_url(self) -> str:
        if self.token:
            sep = "&" if "?" in self.vps_url else "?"
            return f"{self.vps_url}{sep}token={self.token}"
        return self.vps_url

    async def _handle_task(self, ws, request_id: str, payload: dict) -> None:
        """Proses satu task dan kirim hasilnya ke VPS."""
        async with self._semaphore:
            logger.info("▶ Memproses task [%s]", request_id[:8])
            try:
                result = await self.processor.process(request_id, payload)
                msg = {
                    "type": "result",
                    "request_id": request_id,
                    "data": result,
                }
            except Exception as e:
                logger.error("Error saat memproses [%s]: %s", request_id[:8], e)
                msg = {
                    "type": "error",
                    "request_id": request_id,
                    "message": str(e),
                }
            try:
                await ws.send(json.dumps(msg))
            except Exception as e:
                logger.error("Gagal mengirim result ke VPS: %s", e)

    async def _keepalive(self, ws) -> None:
        """Kirim ping ke VPS setiap 30 detik supaya koneksi tidak putus."""
        while True:
            await asyncio.sleep(30)
            try:
                await ws.send(json.dumps({"type": "ping"}))
            except Exception:
                break

    async def _connect_and_run(self) -> None:
        url = self._build_url()
        logger.info("🔌 Konek ke VPS: %s", self.vps_url)

        async with websockets.connect(url, ping_interval=20, ping_timeout=30) as ws:
            logger.info("✅ Terhubung ke VPS!")

            # Jalankan keepalive di background
            keepalive_task = asyncio.create_task(self._keepalive(ws))

            try:
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        logger.warning("Pesan tidak valid dari VPS: %s", raw[:100])
                        continue

                    msg_type = msg.get("type")

                    if msg_type == "task":
                        request_id = msg["request_id"]
                        payload = msg["payload"]
                        # Jalankan task di background (non-blocking)
                        asyncio.create_task(self._handle_task(ws, request_id, payload))

                    elif msg_type == "pong":
                        logger.debug("Pong dari VPS")

                    else:
                        logger.debug("Pesan tidak dikenal: %s", msg_type)

            finally:
                keepalive_task.cancel()

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


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="local-worker",
        description="Local Worker – konek ke VPS dan proses scraper secara lokal",
    )
    parser.add_argument(
        "--vps",
        required=True,
        metavar="URL",
        help="WebSocket URL VPS, contoh: ws://1.2.3.4:8000/ws/worker",
    )
    parser.add_argument("--token", default=None, help="Token autentikasi (harus sama dengan --token di vps_server.py)")
    parser.add_argument("--workers", type=int, default=1, help="Jumlah task yang bisa diproses bersamaan (default: 1)")
    parser.add_argument("--no-headless", action="store_true", help="Tampilkan jendela browser")
    parser.add_argument("--cookies-dir", metavar="DIR", type=Path, default=COOKIES_DIR)
    parser.add_argument("--session-ttl", type=int, default=3600, help="Session TTL dalam detik (default: 3600)")
    parser.add_argument("--reconnect-delay", type=float, default=5.0, help="Jeda sebelum reconnect (detik)")
    args = parser.parse_args()

    logger.info("=" * 55)
    logger.info("  Local Worker – AIChatScraper")
    logger.info("  VPS       : %s", args.vps)
    logger.info("  Workers   : %d concurrent tasks", args.workers)
    logger.info("  Headless  : %s", not args.no_headless)
    logger.info("  Token     : %s", "✅ Aktif" if args.token else "❌ Tidak diset")
    logger.info("=" * 55)

    processor = TaskProcessor(
        headless=not args.no_headless,
        cookies_dir=args.cookies_dir,
        session_ttl=args.session_ttl,
    )

    worker = LocalWorker(
        vps_url=args.vps,
        processor=processor,
        max_concurrent=args.workers,
        token=args.token,
        reconnect_delay=args.reconnect_delay,
    )

    try:
        asyncio.run(worker.run())
    except KeyboardInterrupt:
        logger.info("Worker dihentikan.")
        worker.stop()


if __name__ == "__main__":
    main()