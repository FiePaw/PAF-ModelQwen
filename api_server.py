#!/usr/bin/env python3
"""
api_server.py – OpenAI-Compatible Local API Server for AIChatScraper (Qwen AI)

Exposes endpoints compatible with OpenAI's Chat Completions API so any
OpenAI SDK / client can be pointed at this server instead.

Endpoints
---------
  GET  /                          → health check
  GET  /v1/models                 → list available "models"
  POST /v1/chat/completions       → chat completion (streaming & non-streaming)

Usage
-----
  # Install dependencies first:
  pip install fastapi uvicorn sse-starlette

  # Start the server:
  python api_server.py
  python api_server.py --host 0.0.0.0 --port 8000
  python api_server.py --no-headless   # show browser window
  python api_server.py --workers 2     # allow 2 concurrent browser sessions

  # Then use with any OpenAI client:
  curl http://localhost:8000/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model":"qwen","messages":[{"role":"user","content":"Hello!"}]}'
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Literal, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

# ── Ensure project root is importable ────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

from config import COOKIES_DIR
from scrapers.qwen_scraper import QwenScraper
from scrapers.utils import setup_logger

logger = setup_logger("api_server")


# ─── Global State ─────────────────────────────────────────────────────────────

class ScraperPool:
    """Manages a pool of QwenScraper instances with a semaphore."""

    def __init__(self, max_workers: int, headless: bool, cookies_dir: Path):
        self.max_workers = max_workers
        self.headless = headless
        self.cookies_dir = cookies_dir
        self._semaphore = asyncio.Semaphore(max_workers)
        self._request_count = 0
        self._active = 0

    @asynccontextmanager
    async def get_scraper(self) -> AsyncIterator[QwenScraper]:
        async with self._semaphore:
            self._active += 1
            self._request_count += 1
            scraper = QwenScraper(
                headless=self.headless,
                cookies_dir=self.cookies_dir,
            )
            try:
                await scraper.launch_browser()
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
        }


# Pool config is stored here before uvicorn forks; lifespan reads it.
_pool_config: dict = {
    "max_workers": 1,
    "headless": True,
    "cookies_dir": COOKIES_DIR,
}


# ─── Pydantic models (OpenAI-compatible) ─────────────────────────────────────

class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"] = "user"
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "qwen"
    messages: list[ChatMessage]
    stream: bool = False
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=None, ge=1)
    # Ignored but accepted to stay compatible
    top_p: Optional[float] = None
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None
    stop: Optional[list[str] | str] = None
    user: Optional[str] = None

    @property
    def last_user_message(self) -> str:
        """Extract the last user message as the prompt."""
        user_msgs = [m.content for m in self.messages if m.role == "user"]
        return user_msgs[-1] if user_msgs else ""

    @property
    def conversation_mode(self) -> str:
        """'continue' if there is prior assistant context, else 'new'."""
        has_prior = any(m.role == "assistant" for m in self.messages)
        return "continue" if has_prior else "new"


class UsageInfo(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChoiceMessage(BaseModel):
    role: str = "assistant"
    content: str


class Choice(BaseModel):
    index: int = 0
    message: ChoiceMessage
    finish_reason: str = "stop"


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[Choice]
    usage: UsageInfo


# ─── FastAPI app ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Inisialisasi pool di sini agar berjalan di event loop yang sama
    # dengan uvicorn, bukan di proses induk yang berbeda.
    app.state.pool = ScraperPool(
        max_workers=_pool_config["max_workers"],
        headless=_pool_config["headless"],
        cookies_dir=_pool_config["cookies_dir"],
    )
    logger.info("🚀 AIChatScraper API Server starting up (workers=%d)", _pool_config["max_workers"])
    yield
    logger.info("🛑 AIChatScraper API Server shutting down")


app = FastAPI(
    title="AIChatScraper – OpenAI-Compatible API",
    description="Drop-in OpenAI-compatible API backed by Qwen AI via browser automation",
    version="1.0.0",
    lifespan=lifespan,
)

# Allow all origins (for local dev; tighten for production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _token_estimate(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return max(1, len(text) // 4)


def _make_completion_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"


def _build_response(
    request: ChatCompletionRequest,
    response_text: str,
    completion_id: str | None = None,
) -> ChatCompletionResponse:
    cid = completion_id or _make_completion_id()
    prompt_tokens = _token_estimate(request.last_user_message)
    completion_tokens = _token_estimate(response_text)
    return ChatCompletionResponse(
        id=cid,
        created=int(time.time()),
        model=request.model,
        choices=[
            Choice(
                message=ChoiceMessage(content=response_text),
                finish_reason="stop",
            )
        ],
        usage=UsageInfo(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )


async def _stream_response(
    request: ChatCompletionRequest,
    response_text: str,
    completion_id: str,
) -> AsyncIterator[str]:
    """Yield SSE chunks that mimic OpenAI's streaming format."""
    chunk_size = 8  # chars per stream chunk
    words = response_text.split(" ")

    created = int(time.time())

    # Role chunk (first)
    role_chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": request.model,
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}],
    }
    yield f"data: {json.dumps(role_chunk)}\n\n"

    # Content chunks
    buffer = ""
    for word in words:
        buffer += word + " "
        if len(buffer) >= chunk_size:
            content_chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": request.model,
                "choices": [{"index": 0, "delta": {"content": buffer}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(content_chunk)}\n\n"
            buffer = ""
            await asyncio.sleep(0.01)  # slight delay for natural feel

    # Flush remaining
    if buffer:
        content_chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": request.model,
            "choices": [{"index": 0, "delta": {"content": buffer}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(content_chunk)}\n\n"

    # Final [DONE] chunk
    stop_chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": request.model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(stop_chunk)}\n\n"
    yield "data: [DONE]\n\n"


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/")
async def root(request: Request):
    """Health check & server info."""
    pool: ScraperPool | None = getattr(request.app.state, "pool", None)
    pool_stats = pool.stats if pool else {}
    return {
        "status": "ok",
        "service": "AIChatScraper – OpenAI-Compatible API",
        "version": "1.0.0",
        "backend": "Qwen AI (chat.qwen.ai)",
        "pool": pool_stats,
        "endpoints": {
            "models": "/v1/models",
            "chat": "/v1/chat/completions",
            "health": "/health",
        },
    }


@app.get("/health")
async def health():
    """Minimal health check endpoint."""
    return {"status": "ok", "timestamp": int(time.time())}


@app.get("/v1/models")
async def list_models():
    """Return fake model list compatible with OpenAI's /v1/models response."""
    now = int(time.time())
    models = [
        {
            "id": "qwen",
            "object": "model",
            "created": now,
            "owned_by": "qwen-ai",
            "permission": [],
            "root": "qwen",
            "parent": None,
        },
        {
            "id": "qwen-turbo",
            "object": "model",
            "created": now,
            "owned_by": "qwen-ai",
            "permission": [],
            "root": "qwen-turbo",
            "parent": None,
        },
    ]
    return {"object": "list", "data": models}


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest, raw_request: Request):
    """
    OpenAI-compatible chat completions endpoint.

    Supports both streaming (stream=true) and non-streaming responses.
    Automatically handles conversation mode detection.
    """
    pool: ScraperPool = raw_request.app.state.pool

    prompt = request.last_user_message
    if not prompt:
        raise HTTPException(status_code=400, detail="No user message found in messages array")

    mode = request.conversation_mode
    completion_id = _make_completion_id()

    logger.info(
        "Request [%s] model=%s mode=%s stream=%s prompt_len=%d",
        completion_id, request.model, mode, request.stream, len(prompt),
    )

    try:
        async with pool.get_scraper() as scraper:
            result = await scraper.scrape(prompt, mode=mode)

        if not result["success"]:
            error_msg = result.get("error", "Unknown scraper error")
            logger.error("Scrape failed [%s]: %s", completion_id, error_msg)
            raise HTTPException(status_code=502, detail=f"Scraper error: {error_msg}")

        response_text: str = result["response"]
        logger.info(
            "Request [%s] completed: %d chars, %d code blocks",
            completion_id, len(response_text), result.get("code_block_count", 0),
        )

    except HTTPException:
        raise
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Qwen AI did not respond in time")
    except Exception as exc:
        logger.error("Unexpected error [%s]: %s", completion_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {exc}")

    # ── Streaming response ────────────────────────────────────────────────────
    if request.stream:
        return StreamingResponse(
            _stream_response(request, response_text, completion_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    # ── Non-streaming response ────────────────────────────────────────────────
    return _build_response(request, response_text, completion_id)


# ─── Error handlers ───────────────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "message": str(exc),
                "type": "internal_server_error",
                "code": 500,
            }
        },
    )


# ─── CLI entry point ──────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="api-server",
        description="OpenAI-Compatible Local API Server for AIChatScraper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    p.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    p.add_argument("--workers", type=int, default=1, help="Max concurrent browser sessions (default: 1)")
    p.add_argument("--no-headless", action="store_true", help="Show browser window")
    p.add_argument(
        "--cookies-dir",
        metavar="DIR",
        type=Path,
        default=COOKIES_DIR,
        help=f"Directory with cookie .json files (default: {COOKIES_DIR})",
    )
    p.add_argument("--reload", action="store_true", help="Enable auto-reload (dev mode)")
    p.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])
    return p


def main() -> None:
    args = build_parser().parse_args()

    # Tulis config ke _pool_config; lifespan() akan membaca ini
    # saat uvicorn menjalankan app di event loop barunya.
    global _pool_config
    _pool_config.update({
        "max_workers": args.workers,
        "headless": not args.no_headless,
        "cookies_dir": args.cookies_dir,
    })

    logger.info("=" * 60)
    logger.info("  AIChatScraper – OpenAI-Compatible API Server")
    logger.info("  Host    : http://%s:%d", args.host, args.port)
    logger.info("  Workers : %d concurrent browser session(s)", args.workers)
    logger.info("  Headless: %s", not args.no_headless)
    logger.info("  Docs    : http://%s:%d/docs", args.host, args.port)
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