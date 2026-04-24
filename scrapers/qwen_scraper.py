"""
QwenScraper – concrete scraper for https://chat.qwen.ai
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from config import QWEN_CONFIG, ROTATION_CONFIG
from scrapers.base_scraper import BaseAIChatScraper
from scrapers.utils import contains_any


_TIMEOUT = QWEN_CONFIG["timeouts"]


class QwenScraper(BaseAIChatScraper):
    """Scraper for Qwen AI chat (chat.qwen.ai)."""

    BASE_URL: str = QWEN_CONFIG["base_url"]

    _SEL_TEXTAREA = "textarea[placeholder], div[contenteditable='true']"
    _SEL_SEND_BTN = "button[aria-label*='send' i], button[class*='send' i]"
    _SEL_STOP_BTN = "button[aria-label*='stop' i], button[class*='stop' i]"
    _SEL_THINKING = ".thinking-indicator, .loading-dots, [class*='thinking']"

    def __init__(
        self,
        headless: bool = True,
        cookies_path: Path | str | None = None,
        cookies_dir: Path | str | None = None,
    ) -> None:
        super().__init__(
            headless=headless,
            cookies_path=cookies_path,
            cookies_dir=cookies_dir,
        )
        self._conversation_started = False
        self._last_prompt = ""

    # ── Navigation ────────────────────────────────────────────────────────────

    async def _goto_new_chat(self) -> None:
        self.logger.info("Navigating to Qwen AI: %s", self.BASE_URL)
        await self._page.goto(
            self.BASE_URL, wait_until="domcontentloaded", timeout=_TIMEOUT["page_load"]
        )
        await asyncio.sleep(3)
        self._conversation_started = False
        self.logger.debug("Landed on new-chat page")

    async def _ensure_page_ready(self, mode: str) -> None:
        if mode == "new" or not self._conversation_started:
            await self._goto_new_chat()
        else:
            self.logger.debug("Continuing existing conversation")

    # ── Input ─────────────────────────────────────────────────────────────────

    async def _find_input(self):
        candidates = [
            "textarea#chat-input",
            "textarea[data-testid='chat-input']",
            "div[contenteditable='true'][data-testid]",
            "textarea[placeholder]",
            "div[contenteditable='true']",
            "textarea",
        ]
        for sel in candidates:
            try:
                el = await self._page.wait_for_selector(sel, timeout=5_000, state="visible")
                if el:
                    self.logger.debug("Found input: %s", sel)
                    return el
            except Exception:
                continue
        raise RuntimeError("Could not locate chat input field")

    async def _find_send_button(self):
        candidates = [
            "button[data-testid='send-button']",
            "button[aria-label='Send message']",
            "button[aria-label='Send']",
            "button[type='submit']",
            self._SEL_SEND_BTN,
        ]
        for sel in candidates:
            try:
                el = await self._page.query_selector(sel)
                if el:
                    return el
            except Exception:
                continue
        return None

    # ── Response extraction ───────────────────────────────────────────────────

    async def _extract_last_response(self) -> str:
        text = await self._page.evaluate("""
        () => {
            const strategies = [
                () => {
                    const containers = document.querySelectorAll(
                        '.chat-message-container .chat-response-message'
                    );
                    if (!containers.length) return null;
                    const last = containers[containers.length - 1];
                    const md = last.querySelector('.qwen-markdown-loose, .qwen-markdown');
                    const t = ((md || last).innerText || '').trim();
                    return t.length ? t : null;
                },
                () => {
                    const els = document.querySelectorAll('.qwen-markdown-loose, .qwen-markdown');
                    if (!els.length) return null;
                    const t = (els[els.length - 1].innerText || '').trim();
                    return t.length ? t : null;
                },
                () => {
                    const els = document.querySelectorAll('.chat-response-message');
                    if (!els.length) return null;
                    const t = (els[els.length - 1].innerText || '').trim();
                    return t.length ? t : null;
                },
                () => {
                    const sels = [
                        '[class*="response-message"]',
                        '[class*="chat-response"]',
                        '[class*="assistant-message"]',
                        '[data-role="assistant"]',
                        '[data-author="assistant"]',
                    ];
                    for (const s of sels) {
                        const els = document.querySelectorAll(s);
                        if (!els.length) continue;
                        const t = (els[els.length - 1].innerText || '').trim();
                        if (t.length > 3) return t;
                    }
                    return null;
                },
            ];
            for (const fn of strategies) {
                try { const r = fn(); if (r && r.length > 0) return r; } catch (e) {}
            }
            return '';
        }
        """)
        return (text or "").strip()

    async def _is_generating(self) -> bool:
        result = await self._page.evaluate("""
        () => {
            const stopSels = [
                'button[class*="stop"]',
                'button[aria-label*="stop" i]',
                'button[title*="stop" i]',
                '[class*="stop-btn"]',
                '[class*="abort"]',
            ];
            for (const s of stopSels) {
                const el = document.querySelector(s);
                if (el && el.offsetParent !== null) return true;
            }
            const streamSels = [
                '[class*="streaming"]',
                '[class*="typing"]',
                '[class*="loading-dots"]',
                '[class*="thinking-indicator"]',
                '[class*="cursor-blink"]',
                '.result-streaming',
            ];
            for (const s of streamSels) {
                const el = document.querySelector(s);
                if (el && el.offsetParent !== null) return true;
            }
            return false;
        }
        """)
        return bool(result)

    # ── Core send_prompt ──────────────────────────────────────────────────────

    async def send_prompt(self, prompt: str, mode: str = "new") -> str:
        await self._ensure_page_ready(mode)
        self._last_prompt = prompt

        input_el = await self._find_input()

        self.logger.info("Submitting prompt (%d chars)", len(prompt))
        await input_el.click()
        await asyncio.sleep(0.5)
        await input_el.fill("")
        await input_el.type(prompt, delay=30)
        await asyncio.sleep(0.5)

        # Snapshot count of response elements before submitting
        pre_count = await self._page.evaluate("""
        () => document.querySelectorAll(
            '.chat-message-container .chat-response-message, .qwen-markdown-loose, .qwen-markdown'
        ).length
        """)

        send_btn = await self._find_send_button()
        if send_btn:
            await send_btn.click()
        else:
            await input_el.press("Enter")
            self.logger.debug("Used Enter key to submit")

        self._conversation_started = True
        response_text = await self._wait_for_generation(pre_count)
        return response_text

    async def _wait_for_generation(self, pre_count: int = 0) -> str:
        """
        Wait until:
          1. A new response element appeared (count > pre_count)
          2. Qwen stopped generating (no stop/stream indicators)
          3. Text is stable for two consecutive intervals
        """
        timeout_s = _TIMEOUT["response_wait"] // 1000
        stability_interval = _TIMEOUT["stability_check"] / 1000
        stability_needed = 2

        deadline = asyncio.get_event_loop().time() + timeout_s
        prev_text = ""
        stable_count = 0
        appeared = False

        while asyncio.get_event_loop().time() < deadline:
            # Phase 1: wait for a new response element
            if not appeared:
                cur_count = await self._page.evaluate("""
                () => document.querySelectorAll(
                    '.chat-message-container .chat-response-message, .qwen-markdown-loose, .qwen-markdown'
                ).length
                """)
                if cur_count > pre_count:
                    appeared = True
                    self.logger.debug("New response element appeared (count: %d → %d)", pre_count, cur_count)
                else:
                    await asyncio.sleep(0.5)
                    continue

            # Phase 2: stability check
            generating = await self._is_generating()
            current_text = await self._extract_last_response()

            self.logger.debug(
                "generating=%s text_len=%d stable=%d/%d",
                generating, len(current_text), stable_count, stability_needed,
            )

            if current_text and not generating:
                if current_text == prev_text:
                    stable_count += 1
                    if stable_count >= stability_needed:
                        self.logger.info("Response ready (%d chars)", len(current_text))
                        return current_text
                else:
                    stable_count = 0
            else:
                stable_count = 0

            prev_text = current_text
            await asyncio.sleep(stability_interval)

        raise TimeoutError(f"Qwen did not finish responding within {timeout_s}s")

    # ── Error detection ───────────────────────────────────────────────────────

    async def is_rate_limited(self) -> bool:
        try:
            body = await self._page.inner_text("body")
            return contains_any(body, ROTATION_CONFIG["rate_limit_phrases"])
        except Exception:
            return False

    async def is_session_expired(self) -> bool:
        try:
            url = self._page.url
            body = await self._page.inner_text("body")
            if "login" in url.lower() or "signin" in url.lower():
                return True
            return contains_any(body, ROTATION_CONFIG["session_expired_phrases"])
        except Exception:
            return False

    # ── Concurrent scraping ───────────────────────────────────────────────────

    @classmethod
    async def scrape_many(
        cls,
        prompts: list[str],
        mode: str = "new",
        headless: bool = True,
        cookies_dir: Path | str | None = None,
        max_concurrent: int = 3,
    ) -> list[dict]:
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _single(prompt: str) -> dict:
            async with semaphore:
                async with cls(headless=headless, cookies_dir=cookies_dir) as scraper:
                    return await scraper.scrape(prompt, mode=mode)

        tasks = [_single(p) for p in prompts]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        final = []
        for p, r in zip(prompts, results):
            if isinstance(r, Exception):
                final.append({
                    "prompt": p,
                    "response": None,
                    "success": False,
                    "error": str(r),
                })
            else:
                final.append(r)
        return final