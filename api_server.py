#!/usr/bin/env python3
"""
api_server.py – OpenAI-Compatible Local API Server for AIChatScraper (Qwen AI)

Endpoints
---------
  GET  /                          → health check & server info
  GET  /health                    → minimal health check
  GET  /v1/models                 → list available "models"
  GET  /v1/sessions               → list active continue-mode sessions
  DELETE /v1/sessions/{id}        → destroy a session
  POST /v1/chat/completions       → chat completion (streaming & non-streaming)

Session / Continue Mode
-----------------------
  Kirim  X-Session-ID  di header untuk melanjutkan percakapan yang sama.
  Server akan menggunakan cookie file dan conversation URL yang sama
  dari sesi sebelumnya (mode 'continue').

  Response selalu menyertakan header  X-Session-ID  dan  X-Cookie-File
  agar client bisa menyimpannya.

  Jika tanpa X-Session-ID → mode 'new', cookie diambil dari rotasi.

Usage
-----
  python api_server.py
  python api_server.py --host 0.0.0.0 --port 8000 --workers 2
  python api_server.py --no-headless
  python api_server.py --session-ttl 7200
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, Literal, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field as PydanticField

sys.path.insert(0, str(Path(__file__).parent))

from config import COOKIES_DIR
from scrapers.qwen_scraper import QwenScraper
from scrapers.utils import discover_cookie_files, setup_logger

logger = setup_logger("api_server")


# ─── Session Store ────────────────────────────────────────────────────────────

@dataclass
class Session:
    """Represents one ongoing conversation session."""
    session_id: str
    cookie_file: Path
    conversation_url: str | None = None
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    turn_count: int = 0

    def touch(self) -> None:
        self.last_used = time.time()
        self.turn_count += 1

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "cookie_file": self.cookie_file.name,
            "conversation_url": self.conversation_url,
            "created_at": datetime.fromtimestamp(self.created_at).isoformat(),
            "last_used": datetime.fromtimestamp(self.last_used).isoformat(),
            "turn_count": self.turn_count,
        }


class SessionStore:
    """Thread-safe in-memory session store with TTL eviction."""

    def __init__(self, ttl_seconds: int = 3600):
        self._sessions: dict[str, Session] = {}
        self._lock = asyncio.Lock()
        self.ttl = ttl_seconds

    async def create(self, cookie_file: Path) -> Session:
        async with self._lock:
            sid = uuid.uuid4().hex
            session = Session(session_id=sid, cookie_file=cookie_file)
            self._sessions[sid] = session
            logger.info("Session created [%s] cookie=%s", sid[:8], cookie_file.name)
            return session

    async def get(self, session_id: str) -> Session | None:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            if time.time() - session.last_used > self.ttl:
                del self._sessions[session_id]
                logger.info("Session expired [%s]", session_id[:8])
                return None
            return session

    async def delete(self, session_id: str) -> bool:
        async with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                logger.info("Session deleted [%s]", session_id[:8])
                return True
            return False

    async def list_all(self) -> list[dict]:
        async with self._lock:
            now = time.time()
            expired = [sid for sid, s in self._sessions.items() if now - s.last_used > self.ttl]
            for sid in expired:
                del self._sessions[sid]
            return [s.to_dict() for s in self._sessions.values()]

    @property
    def count(self) -> int:
        return len(self._sessions)


# ─── Cookie Rotator ───────────────────────────────────────────────────────────

class CookieRotator:
    """Round-robin cookie rotator — only used for NEW mode requests."""

    def __init__(self, cookies_dir: Path):
        self.cookies_dir = cookies_dir
        self._files: list[Path] = []
        self._index: int = 0
        self._lock = asyncio.Lock()
        self._refresh()

    def _refresh(self) -> None:
        self._files = discover_cookie_files(self.cookies_dir)
        if not self._files:
            logger.warning("No cookie files found in %s", self.cookies_dir)
        else:
            logger.info(
                "CookieRotator: %d file(s) available: %s",
                len(self._files), [f.name for f in self._files],
            )

    async def next_cookie(self) -> Path | None:
        async with self._lock:
            if not self._files:
                self._refresh()
            if not self._files:
                return None
            cookie = self._files[self._index % len(self._files)]
            self._index += 1
            logger.debug("Rotator → %s (slot %d)", cookie.name, self._index - 1)
            return cookie

    @property
    def available(self) -> list[str]:
        return [f.name for f in self._files]


# ─── Scraper Pool ─────────────────────────────────────────────────────────────

class ScraperPool:
    """
    Concurrency-limited pool of QwenScraper instances.

    NEW mode    : cookie chosen by CookieRotator (round-robin).
    CONTINUE mode: cookie_file locked to the session, browser navigates
                   to the saved conversation_url before sending the prompt.
    """

    def __init__(self, max_workers: int, headless: bool, cookies_dir: Path):
        self.max_workers = max_workers
        self.headless = headless
        self.cookies_dir = cookies_dir
        self._semaphore = asyncio.Semaphore(max_workers)
        self.rotator = CookieRotator(cookies_dir)
        self._request_count = 0
        self._active = 0

    @asynccontextmanager
    async def get_scraper(
        self,
        cookie_file: Path | None = None,
        conversation_url: str | None = None,
    ) -> AsyncIterator[QwenScraper]:
        """
        Yield a ready QwenScraper.

        cookie_file      – if given, skip rotation and use this file (continue mode)
        conversation_url – if given, navigate there before yielding (continue mode)
        """
        async with self._semaphore:
            self._active += 1
            self._request_count += 1

            effective_cookie = cookie_file or await self.rotator.next_cookie()

            scraper = QwenScraper(
                headless=self.headless,
                cookies_path=effective_cookie,
                cookies_dir=self.cookies_dir,
            )
            try:
                await scraper.launch_browser()
                if effective_cookie:
                    await scraper.load_cookies(effective_cookie)

                # Navigate to existing conversation when continuing
                if conversation_url and "chat.qwen.ai" in conversation_url:
                    logger.info("Navigating to conversation: %s", conversation_url)
                    await scraper._page.goto(
                        conversation_url,
                        wait_until="domcontentloaded",
                        timeout=30_000,
                    )
                    await asyncio.sleep(2)
                    scraper._conversation_started = True

                yield scraper
            finally:
                await scraper.close_browser()
                self._active -= 1

    @property
    def stats(self) -> dict:
        return {
            "max_workers": self.max_workers,
            "active_sessions": self._active,
            "total_requests": self._request_count,
            "available_cookies": self.rotator.available,
        }


# ─── Global config ────────────────────────────────────────────────────────────

_pool_config: dict = {
    "max_workers": 1,
    "headless": True,
    "cookies_dir": COOKIES_DIR,
    "session_ttl": 3600,
}


# ─── Pydantic models ──────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"] = "user"
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "qwen"
    messages: list[ChatMessage]
    stream: bool = False
    temperature: Optional[float] = PydanticField(default=None, ge=0.0, le=2.0)
    max_tokens: Optional[int] = PydanticField(default=None, ge=1)
    top_p: Optional[float] = None
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None
    stop: Optional[list[str] | str] = None
    user: Optional[str] = None

    @property
    def last_user_message(self) -> str:
        user_msgs = [m.content for m in self.messages if m.role == "user"]
        return user_msgs[-1] if user_msgs else ""


def _token_estimate(text: str) -> int:
    return max(1, len(text) // 4)


def _make_completion_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"


# ─── FastAPI ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = ScraperPool(
        max_workers=_pool_config["max_workers"],
        headless=_pool_config["headless"],
        cookies_dir=_pool_config["cookies_dir"],
    )
    app.state.sessions = SessionStore(ttl_seconds=_pool_config["session_ttl"])
    logger.info(
        "Server ready (workers=%d, session_ttl=%ds)",
        _pool_config["max_workers"],
        _pool_config["session_ttl"],
    )
    yield
    logger.info("Server shutting down")


app = FastAPI(
    title="AIChatScraper – OpenAI-Compatible API",
    description=(
        "Drop-in OpenAI-compatible API backed by Qwen AI. "
        "Send **X-Session-ID** header to continue an existing conversation."
    ),
    version="2.0.0",
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


# ─── SSE helper ───────────────────────────────────────────────────────────────

async def _sse_chunks(
    request: ChatCompletionRequest,
    response_text: str,
    completion_id: str,
) -> AsyncIterator[str]:
    created = int(time.time())
    model = request.model

    def _chunk(delta: dict, finish: str | None = None) -> str:
        return "data: " + json.dumps({
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }) + "\n\n"

    yield _chunk({"role": "assistant", "content": ""})

    buffer = ""
    for word in response_text.split(" "):
        buffer += word + " "
        if len(buffer) >= 8:
            yield _chunk({"content": buffer})
            buffer = ""
            await asyncio.sleep(0.01)
    if buffer:
        yield _chunk({"content": buffer})

    yield _chunk({}, finish="stop")
    yield "data: [DONE]\n\n"


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/")
async def root(request: Request):
    pool: ScraperPool | None = getattr(request.app.state, "pool", None)
    store: SessionStore | None = getattr(request.app.state, "sessions", None)
    return {
        "status": "ok",
        "service": "AIChatScraper – OpenAI-Compatible API",
        "version": "2.0.0",
        "pool": pool.stats if pool else {},
        "sessions": {"active": store.count if store else 0},
    }


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": int(time.time())}


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


@app.get("/v1/sessions")
async def list_sessions(raw_request: Request):
    """List all active sessions (each maps a session_id → cookie_file + conversation_url)."""
    store: SessionStore = raw_request.app.state.sessions
    data = await store.list_all()
    return {"object": "list", "count": len(data), "data": data}


@app.delete("/v1/sessions/{session_id}")
async def delete_session(session_id: str, raw_request: Request):
    """Terminate a session. Next request with this ID will start a new conversation."""
    store: SessionStore = raw_request.app.state.sessions
    if not await store.delete(session_id):
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    return {"deleted": True, "session_id": session_id}


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest, raw_request: Request):
    """
    OpenAI-compatible chat completions.

    **Headers (request)**

    | Header | Keterangan |
    |---|---|
    | `X-Session-ID` | (opsional) ID sesi untuk melanjutkan percakapan (mode continue) |

    **Headers (response)**

    | Header | Keterangan |
    |---|---|
    | `X-Session-ID` | ID sesi aktif — simpan dan kirim kembali untuk continue |
    | `X-Cookie-File` | Nama cookie file yang digunakan sesi ini |
    | `X-Conversation-URL` | URL percakapan Qwen yang sedang aktif |
    """
    pool: ScraperPool = raw_request.app.state.pool
    store: SessionStore = raw_request.app.state.sessions

    prompt = request.last_user_message
    if not prompt:
        raise HTTPException(status_code=400, detail="No user message found in messages array")

    completion_id = _make_completion_id()

    # ── Resolusi session & mode ───────────────────────────────────────────────
    incoming_sid = raw_request.headers.get("X-Session-ID", "").strip()
    session: Session | None = None
    mode = "new"

    if incoming_sid:
        session = await store.get(incoming_sid)
        if session:
            mode = "continue"
            logger.info(
                "CONTINUE [%s] session=%s cookie=%s url=%s",
                completion_id[:8],
                session.session_id[:8],
                session.cookie_file.name,
                session.conversation_url or "pending-first-turn",
            )
        else:
            logger.warning(
                "X-Session-ID [%s] not found/expired → starting new session",
                incoming_sid[:8],
            )

    if mode == "new":
        logger.info(
            "NEW [%s] — cookie rotation will assign a file",
            completion_id[:8],
        )

    # ── Eksekusi scraper ──────────────────────────────────────────────────────
    cookie_file = session.cookie_file if session else None
    conv_url = session.conversation_url if session else None

    try:
        async with pool.get_scraper(
            cookie_file=cookie_file,
            conversation_url=conv_url,
        ) as scraper:
            result = await scraper.scrape(prompt, mode=mode)
            current_url: str = scraper._page.url

    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Qwen AI did not respond in time")
    except Exception as exc:
        logger.error("Error [%s]: %s", completion_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {exc}")

    if not result["success"]:
        raise HTTPException(
            status_code=502,
            detail=f"Scraper error: {result.get('error', 'Unknown')}",
        )

    response_text: str = result["response"]
    account_used: str = result.get("account_used", "unknown")

    # ── Buat / update session ─────────────────────────────────────────────────
    if session is None:
        # Mode new: cari file cookie yang dipakai scraper berdasarkan nama akun
        matched = next(
            (f for f in pool.rotator._files if f.stem == account_used),
            pool.rotator._files[0] if pool.rotator._files else None,
        )
        fallback = matched or Path(pool.cookies_dir) / f"{account_used}.json"
        session = await store.create(cookie_file=fallback)

    if current_url and "chat.qwen.ai" in current_url and current_url != pool.rotator.available:
        session.conversation_url = current_url

    session.touch()

    logger.info(
        "Done [%s] %d chars | session=%s | cookie=%s | url=%s",
        completion_id[:8],
        len(response_text),
        session.session_id[:8],
        session.cookie_file.name,
        session.conversation_url or "-",
    )

    # ── Response headers ──────────────────────────────────────────────────────
    extra_headers = {
        "X-Session-ID": session.session_id,
        "X-Cookie-File": session.cookie_file.name,
        "X-Conversation-URL": session.conversation_url or "",
    }

    # ── Streaming ─────────────────────────────────────────────────────────────
    if request.stream:
        return StreamingResponse(
            _sse_chunks(request, response_text, completion_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
                **extra_headers,
            },
        )

    # ── Non-streaming ─────────────────────────────────────────────────────────
    pt = _token_estimate(prompt)
    ct = _token_estimate(response_text)

    body = {
        "id": completion_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": request.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": response_text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "total_tokens": pt + ct,
        },
        "x_meta": {
            "session_id": session.session_id,
            "cookie_file": session.cookie_file.name,
            "conversation_url": session.conversation_url,
            "account_used": account_used,
        },
    }
    return JSONResponse(content=body, headers=extra_headers)


# ─── Error handler ────────────────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": {"message": str(exc), "type": "internal_server_error", "code": 500}},
    )


# ─── CLI ──────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="api-server",
        description="OpenAI-Compatible Local API Server for AIChatScraper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--workers", type=int, default=1, help="Max concurrent browser sessions (default: 1)")
    p.add_argument("--no-headless", action="store_true", help="Show browser window")
    p.add_argument("--cookies-dir", metavar="DIR", type=Path, default=COOKIES_DIR)
    p.add_argument(
        "--session-ttl",
        metavar="SECONDS",
        type=int,
        default=3600,
        help="Idle seconds before a session expires (default: 3600)",
    )
    p.add_argument("--reload", action="store_true", help="Auto-reload on code change (dev)")
    p.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])
    return p


def main() -> None:
    args = build_parser().parse_args()

    global _pool_config
    _pool_config.update({
        "max_workers": args.workers,
        "headless": not args.no_headless,
        "cookies_dir": args.cookies_dir,
        "session_ttl": args.session_ttl,
    })

    logger.info("=" * 60)
    logger.info("  AIChatScraper – OpenAI-Compatible API Server v2.0")
    logger.info("  Host       : http://%s:%d", args.host, args.port)
    logger.info("  Workers    : %d", args.workers)
    logger.info("  Session TTL: %ds", args.session_ttl)
    logger.info("  Docs       : http://%s:%d/docs", args.host, args.port)
    logger.info("=" * 60)

    uvicorn.run(
        "api_server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
        access_log=True,
    )


if __name__ == "__main__":
    main()