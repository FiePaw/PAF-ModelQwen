#!/usr/bin/env python3
"""
vps_server.py – Runs on VPS (Ubuntu)
=====================================
Menerima HTTP request dari Client (OpenAI-compatible),
lalu meneruskan ke Local Worker (Windows) via WebSocket.

Flow:
  [Client] → POST /v1/chat/completions
           → VPS menunggu worker tersedia
           → Forward payload ke worker via WebSocket
           → Worker memproses dengan scraper
           → Result dikembalikan ke client

Usage:
  pip install fastapi uvicorn websockets
  python vps_server.py
  python vps_server.py --host 0.0.0.0 --port 8000 --token rahasia123
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional, Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field as PydanticField

# ─── Logger ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("vps_server")

# ─── Config (diset dari CLI) ──────────────────────────────────────────────────

_config: dict = {
    "auth_token": None,          # Token opsional untuk autentikasi worker
    "request_timeout": 300,      # Timeout tunggu hasil dari worker (detik)
    "worker_timeout": 60,        # Timeout tunggu worker tersedia (detik)
}

# ─── Worker Manager ───────────────────────────────────────────────────────────

class WorkerManager:
    """
    Mengelola pool WebSocket connections dari Local Worker.

    Perubahan model dari versi sebelumnya:
    - Sebelumnya: worker ditandai busy=True (boolean), hanya bisa handle 1 task sekaligus.
    - Sekarang:   worker menyimpan active_tasks (int) dan max_concurrent (int).
                  Task NEW bisa dikirim ke worker mana pun yang masih punya slot kosong.
                  Task CONTINUE dikirim ke worker yang sedang menangani session_id yang sama,
                  atau ke worker dengan slot kosong jika belum ada yang memegang session itu.

    session_id → worker_id  (untuk routing CONTINUE ke worker yang benar)
    """

    def __init__(self):
        # worker_id → {ws, active_tasks, max_concurrent, connected_at, sessions: set[str]}
        self._workers: dict[str, dict] = {}
        self._lock = asyncio.Lock()
        # session_id → worker_id  (sticky routing untuk mode continue)
        self._session_worker: dict[str, str] = {}
        # request_id → Future
        self._pending: dict[str, asyncio.Future] = {}

    async def register(self, worker_id: str, ws: WebSocket, max_concurrent: int = 4) -> None:
        async with self._lock:
            self._workers[worker_id] = {
                "ws": ws,
                "active_tasks": 0,
                "max_concurrent": max_concurrent,
                "connected_at": time.time(),
                "sessions": set(),
            }
        logger.info(
            "✅ Worker terdaftar: %s (max_concurrent=%d, total workers: %d)",
            worker_id[:8], max_concurrent, len(self._workers),
        )

    async def unregister(self, worker_id: str) -> None:
        async with self._lock:
            info = self._workers.pop(worker_id, None)
            if info:
                # Hapus semua session binding milik worker ini
                for sid in list(self._session_worker.keys()):
                    if self._session_worker[sid] == worker_id:
                        del self._session_worker[sid]
        logger.info("❌ Worker disconnect: %s (sisa: %d)", worker_id[:8], len(self._workers))

    async def get_worker_for_task(
        self,
        session_id: str | None,
        timeout: float = 30,
    ) -> tuple[str, dict] | None:
        """
        Pilih worker yang tepat berdasarkan tipe task:

        - CONTINUE (session_id ada):
            Cari worker yang sedang memegang session_id ini (sticky routing).
            Jika tidak ditemukan (session baru / pertama kali), pilih worker
            dengan slot kosong dan ikat session tersebut ke worker tersebut.

        - NEW (session_id None):
            Pilih worker mana saja yang masih punya slot kosong.
            Tidak perlu menunggu worker tertentu selesai.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            async with self._lock:
                # ── Mode CONTINUE: cari worker pemegang session ini ───────────
                if session_id and session_id in self._session_worker:
                    wid = self._session_worker[session_id]
                    info = self._workers.get(wid)
                    if info and info["active_tasks"] < info["max_concurrent"]:
                        info["active_tasks"] += 1
                        return wid, info
                    # Worker pemegang session penuh → tunggu

                else:
                    # ── Mode NEW atau session belum terikat: cari slot kosong ─
                    # Urutkan by active_tasks ascending agar beban merata
                    candidates = sorted(
                        self._workers.items(),
                        key=lambda x: x[1]["active_tasks"],
                    )
                    for wid, info in candidates:
                        if info["active_tasks"] < info["max_concurrent"]:
                            info["active_tasks"] += 1
                            # Ikat session ke worker ini (jika CONTINUE baru)
                            if session_id:
                                info["sessions"].add(session_id)
                                self._session_worker[session_id] = wid
                            return wid, info

            await asyncio.sleep(0.2)
        return None

    async def release_task(self, worker_id: str, session_id: str | None = None) -> None:
        """Kurangi active_tasks worker setelah task selesai."""
        async with self._lock:
            if worker_id in self._workers:
                info = self._workers[worker_id]
                info["active_tasks"] = max(0, info["active_tasks"] - 1)

    def bind_session(self, session_id: str, worker_id: str) -> None:
        """Ikat session_id ke worker tertentu (dipanggil setelah result diterima)."""
        self._session_worker[session_id] = session_id

    def unbind_session(self, session_id: str) -> None:
        """Lepas binding session → worker (saat session expired / error)."""
        self._session_worker.pop(session_id, None)

    def get_session_worker(self, session_id: str) -> str | None:
        return self._session_worker.get(session_id)

    def create_future(self, request_id: str) -> asyncio.Future:
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[request_id] = fut
        return fut

    def resolve_future(self, request_id: str, result: dict) -> None:
        fut = self._pending.pop(request_id, None)
        if fut and not fut.done():
            fut.set_result(result)

    def reject_future(self, request_id: str, error: str) -> None:
        fut = self._pending.pop(request_id, None)
        if fut and not fut.done():
            fut.set_exception(RuntimeError(error))

    @property
    def stats(self) -> dict:
        total = len(self._workers)
        total_slots = sum(w["max_concurrent"] for w in self._workers.values())
        active = sum(w["active_tasks"] for w in self._workers.values())
        return {
            "total_workers": total,
            "total_slots": total_slots,
            "active_tasks": active,
            "idle_slots": max(0, total_slots - active),
            "pending_requests": len(self._pending),
            "tracked_sessions": len(self._session_worker),
        }


# ─── Global state ─────────────────────────────────────────────────────────────

workers = WorkerManager()


# ─── Pydantic Models (OpenAI-compatible) ──────────────────────────────────────

class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"] = "user"
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "qwen"
    messages: list[ChatMessage]
    stream: bool = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    think_mode: Optional[Literal["auto", "thinking", "fast"]] = None

    @property
    def last_user_message(self) -> str:
        msgs = [m.content for m in self.messages if m.role == "user"]
        return msgs[-1] if msgs else ""


def _token_estimate(text: str) -> int:
    return max(1, len(text) // 4)


def _make_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"


# ─── SSE Streaming helper ─────────────────────────────────────────────────────

async def _sse_stream(
    request: ChatCompletionRequest,
    response_text: str,
    completion_id: str,
) -> AsyncIterator[str]:
    created = int(time.time())

    def chunk(delta: dict, finish: str | None = None) -> str:
        return "data: " + json.dumps({
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": request.model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }) + "\n\n"

    yield chunk({"role": "assistant", "content": ""})
    buffer = ""
    for word in response_text.split(" "):
        buffer += word + " "
        if len(buffer) >= 8:
            yield chunk({"content": buffer})
            buffer = ""
            await asyncio.sleep(0.01)
    if buffer:
        yield chunk({"content": buffer})
    yield chunk({}, finish="stop")
    yield "data: [DONE]\n\n"


# ─── FastAPI App ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 55)
    logger.info("  VPS Proxy Server ready")
    logger.info("  Menunggu Local Worker konek di /ws/worker")
    logger.info("=" * 55)
    yield
    logger.info("Server shutting down")


app = FastAPI(
    title="AIChatScraper – VPS Proxy",
    description="Forward HTTP requests ke Local Worker via WebSocket",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Session-ID", "X-Cookie-File", "X-Conversation-URL"],
)


# ─── WebSocket endpoint untuk Local Worker ────────────────────────────────────

@app.websocket("/ws/worker")
async def worker_endpoint(ws: WebSocket):
    """
    Local Worker konek ke sini saat startup.
    Protokol pesan (JSON):

    Worker → VPS (saat connect): {"type": "register", "max_concurrent": 4}
    VPS → Worker:  {"type": "task", "request_id": "...", "payload": {...}}
    Worker → VPS:  {"type": "result", "request_id": "...", "data": {...}}
                   {"type": "error",  "request_id": "...", "message": "..."}
                   {"type": "ping"}  (keepalive)
    """
    token = ws.query_params.get("token", "")
    if _config["auth_token"] and token != _config["auth_token"]:
        await ws.close(code=4001, reason="Unauthorized")
        logger.warning("Worker ditolak: token salah")
        return

    await ws.accept()
    worker_id = uuid.uuid4().hex

    # Tunggu pesan registrasi pertama dari worker (max 5 detik)
    # Worker mengirim {"type": "register", "max_concurrent": N}
    # Jika tidak ada, default ke 4
    max_concurrent = 4
    try:
        raw_reg = await asyncio.wait_for(ws.receive_text(), timeout=5.0)
        reg_msg = json.loads(raw_reg)
        if reg_msg.get("type") == "register":
            max_concurrent = int(reg_msg.get("max_concurrent", 4))
    except (asyncio.TimeoutError, Exception):
        pass  # Tidak ada registrasi → pakai default

    await workers.register(worker_id, ws, max_concurrent=max_concurrent)

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == "result":
                request_id = msg["request_id"]
                result_data = msg["data"]
                # Bind session_id ke worker ini untuk sticky routing CONTINUE
                sid = result_data.get("session_id")
                if sid:
                    workers._session_worker[sid] = worker_id
                workers.resolve_future(request_id, result_data)
                await workers.release_task(worker_id, sid)
                logger.info(
                    "✔ Result diterima [req=%s] session=%s",
                    request_id[:8], (sid or "")[:8],
                )

            elif msg_type == "error":
                request_id = msg["request_id"]
                workers.reject_future(request_id, msg.get("message", "Unknown error"))
                await workers.release_task(worker_id)
                logger.warning(
                    "✖ Error dari worker [req=%s]: %s",
                    request_id[:8], msg.get("message"),
                )

            elif msg_type == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))

            else:
                logger.debug("Pesan tidak dikenal dari worker: %s", msg_type)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error("Worker error: %s", e)
    finally:
        await workers.unregister(worker_id)


# ─── HTTP Routes (OpenAI-compatible) ─────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "status": "ok",
        "service": "AIChatScraper – VPS Proxy",
        "version": "1.0.0",
        "workers": workers.stats,
    }


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": int(time.time()), "workers": workers.stats}


@app.get("/v1/models")
async def list_models():
    now = int(time.time())
    return {
        "object": "list",
        "data": [
            {"id": "qwen", "object": "model", "created": now, "owned_by": "qwen-ai"},
            {"id": "qwen-turbo", "object": "model", "created": now, "owned_by": "qwen-ai"},
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest, raw_req: Request):
    prompt = req.last_user_message
    if not prompt:
        raise HTTPException(status_code=400, detail="No user message found")

    # Ambil session headers dari client
    incoming_sid = raw_req.headers.get("X-Session-ID", "").strip() or None
    request_id = uuid.uuid4().hex
    completion_id = _make_id()
    task_mode = "CONTINUE" if incoming_sid else "NEW"

    logger.info(
        "📨 Request [%s] mode=%s prompt=%d chars session=%s",
        request_id[:8], task_mode, len(prompt),
        incoming_sid[:8] if incoming_sid else "-",
    )

    # Pilih worker berdasarkan mode (NEW → slot kosong mana saja,
    # CONTINUE → worker yang memegang session ini)
    worker_timeout = _config["worker_timeout"]
    worker = await workers.get_worker_for_task(
        session_id=incoming_sid,
        timeout=worker_timeout,
    )
    if worker is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Tidak ada worker tersedia. Pastikan local_worker.py sudah berjalan di Windows."
                if not incoming_sid else
                f"Worker untuk session {incoming_sid[:8]} tidak tersedia atau penuh."
            ),
        )

    worker_id, worker_info = worker
    ws: WebSocket = worker_info["ws"]
    logger.info("📤 Task [%s] → Worker#%s [mode=%s]", request_id[:8], worker_id[:8], task_mode)

    # Buat future untuk menunggu hasil
    future = workers.create_future(request_id)

    # Kirim task ke worker
    task_payload = {
        "type": "task",
        "request_id": request_id,
        "payload": {
            "messages": [{"role": m.role, "content": m.content} for m in req.messages],
            "model": req.model,
            "stream": req.stream,
            "think_mode": req.think_mode,
            "session_id": incoming_sid,
        },
    }

    try:
        await ws.send_text(json.dumps(task_payload))
    except Exception as e:
        await workers.release_task(worker_id, incoming_sid)
        workers.reject_future(request_id, str(e))
        raise HTTPException(status_code=502, detail=f"Gagal mengirim ke worker: {e}")

    # Tunggu hasil dari worker
    try:
        result = await asyncio.wait_for(future, timeout=_config["request_timeout"])
    except asyncio.TimeoutError:
        await workers.release_task(worker_id, incoming_sid)
        raise HTTPException(status_code=504, detail="Worker tidak merespons dalam waktu yang ditentukan")
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    if not result.get("success"):
        raise HTTPException(status_code=502, detail=f"Scraper error: {result.get('error', 'Unknown')}")

    response_text: str = result["response"]
    session_id: str = result.get("session_id", request_id)
    cookie_file: str = result.get("cookie_file", "")
    conversation_url: str = result.get("conversation_url", "")

    extra_headers = {
        "X-Session-ID": session_id,
        "X-Cookie-File": cookie_file,
        "X-Conversation-URL": conversation_url,
    }

    logger.info(
        "✅ Done [%s] %d chars session=%s",
        request_id[:8], len(response_text), session_id[:8],
    )

    # Streaming
    if req.stream:
        return StreamingResponse(
            _sse_stream(req, response_text, completion_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
                **extra_headers,
            },
        )

    # Non-streaming
    pt = _token_estimate(prompt)
    ct = _token_estimate(response_text)
    return JSONResponse(
        content={
            "id": completion_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": req.model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": response_text},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": pt,
                "completion_tokens": ct,
                "total_tokens": pt + ct,
            },
            "x_meta": {
                "session_id": session_id,
                "cookie_file": cookie_file,
                "conversation_url": conversation_url,
                "think_mode": req.think_mode or "auto",
            },
        },
        headers=extra_headers,
    )


# ─── Error handler ────────────────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": {"message": str(exc), "type": "internal_server_error", "code": 500}},
    )


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="vps-server",
        description="VPS Proxy Server – forward HTTP ke Local Worker via WebSocket",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    parser.add_argument("--token", default=None, help="Token autentikasi worker (opsional)")
    parser.add_argument("--request-timeout", type=int, default=300, help="Timeout tunggu hasil worker (detik)")
    parser.add_argument("--worker-timeout", type=int, default=30, help="Timeout tunggu worker idle (detik)")
    parser.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])
    args = parser.parse_args()

    _config["auth_token"] = args.token
    _config["request_timeout"] = args.request_timeout
    _config["worker_timeout"] = args.worker_timeout

    logger.info("=" * 55)
    logger.info("  VPS Proxy Server")
    logger.info("  URL    : http://%s:%d", args.host, args.port)
    logger.info("  Worker : ws://%s:%d/ws/worker", args.host, args.port)
    logger.info("  Token  : %s", "✅ Aktif" if args.token else "❌ Tidak diset (semua worker diterima)")
    logger.info("=" * 55)

    uvicorn.run(
        "vps_server:app",
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        access_log=True,
    )


if __name__ == "__main__":
    main()