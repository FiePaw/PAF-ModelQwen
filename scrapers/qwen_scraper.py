"""
QwenScraper – concrete scraper for https://chat.qwen.ai
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal

from config import QWEN_CONFIG, ROTATION_CONFIG
from scrapers.base_scraper import BaseAIChatScraper
from scrapers.utils import contains_any, discover_cookie_files


_TIMEOUT = QWEN_CONFIG["timeouts"]
_SELECTORS = QWEN_CONFIG["selectors"]
_THINK_LABELS = QWEN_CONFIG["think_mode_labels"]

ThinkMode = Literal["auto", "thinking", "fast"]


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
        think_mode: ThinkMode | None = None,
    ) -> None:
        super().__init__(
            headless=headless,
            cookies_path=cookies_path,
            cookies_dir=cookies_dir,
        )
        self._conversation_started = False
        self._last_prompt = ""
        # Use provided think_mode or fall back to config default
        self._think_mode: ThinkMode = think_mode or QWEN_CONFIG["default_think_mode"]
        self._think_mode_applied = False   # reset when a new chat page is loaded

    # ── Context manager override ──────────────────────────────────────────────

    async def __aenter__(self) -> "QwenScraper":
        return await super().__aenter__()

    def _extra_send_kwargs(self) -> dict:
        return {"think_mode": self._think_mode}

    # ── Navigation ────────────────────────────────────────────────────────────

    async def _goto_new_chat(self) -> None:
        self.logger.info("Navigating to Qwen AI: %s", self.BASE_URL)
        await self._page.goto(
            self.BASE_URL, wait_until="domcontentloaded", timeout=_TIMEOUT["page_load"]
        )
        
        # Tunggu input field siap (bukan sleep blind)
        try:
            await self._page.wait_for_selector(
                self._SEL_TEXTAREA, 
                timeout=_TIMEOUT["between_actions"] * 3
            )
            await self._page.wait_for_load_state("networkidle", timeout=100)
        except Exception as e:
            self.logger.warning("Timeout waiting for input ready: %s", e)
        
        self._conversation_started = False
        self._think_mode_applied = False
        self.logger.debug("Landed on new-chat page")

    async def _rotate_and_reset(self) -> bool:
        """Rotate account and reset think-mode state for the new session."""
        rotated = await self._rotate_account()
        if rotated:
            self._think_mode_applied = False
        return rotated

    async def _ensure_page_ready(self, mode: str) -> None:
        if mode == "new" or not self._conversation_started:
            await self._goto_new_chat()
        else:
            self.logger.debug("Continuing existing conversation")

    # ── Think mode ────────────────────────────────────────────────────────────

    async def _get_current_think_mode(self) -> str:
        """Read the currently active think mode label from the UI."""
        _CURRENT_MODE_CANDIDATES = [
            _SELECTORS["think_mode_selected"],       # .qwen-select-option-selected-label-container
            _SELECTORS["think_mode_trigger"],        # .qwen-select-thinking-label
            "[class*='thinking-label']",
            "[class*='think-mode-label']",
            "[class*='selected-label']",
        ]
        for sel in _CURRENT_MODE_CANDIDATES:
            try:
                el = await self._page.query_selector(sel)
                if el and await el.is_visible():
                    text = (await el.inner_text()).strip().lower()
                    if text in ("auto", "thinking", "fast"):
                        return text
                    for label in ("thinking", "fast", "auto"):
                        if label in text:
                            return label
            except Exception:
                continue
        return ""

    async def debug_think_mode_selectors(self) -> dict:
        """
        Diagnostic helper: scan the page for any elements that might be
        related to the think-mode UI. Run with --no-headless to inspect.
        Returns a dict of selector → matched element info.
        """
        result = await self._page.evaluate("""
        () => {
            const knownLabels = ['auto', 'thinking', 'fast'];
            const found = [];
            const walker = document.createTreeWalker(
                document.body, NodeFilter.SHOW_ELEMENT
            );
            let node;
            while ((node = walker.nextNode())) {
                const text = (node.innerText || '').trim().toLowerCase();
                if (knownLabels.includes(text) && node.offsetParent !== null) {
                    found.push({
                        tag: node.tagName,
                        className: node.className,
                        text: text,
                        id: node.id,
                        parentClass: node.parentElement
                            ? node.parentElement.className : '',
                    });
                }
            }
            return found;
        }
        """)
        self.logger.info("Think-mode debug scan found %d element(s): %s", len(result), result)
        return {"elements": result}

    async def _set_think_mode(self, mode: ThinkMode) -> bool:
        """
        Select *mode* in the Qwen think-mode dropdown.
        Returns True if the selection was applied (or already correct).

        Strategy (cascading fallbacks):
          1. Skip if the UI already shows the correct mode.
          2. Try multiple trigger selectors to open the dropdown.
          3. Wait briefly for the dropdown/popover to appear.
          4. Attempt to click the matching option via several selector patterns.
          5. JS brute-force fallback scanning all visible text nodes.
          6. Verify and mark as applied regardless (don't block scraping).
        """
        target_label = _THINK_LABELS.get(mode, mode).lower()

        # ── Step 1: skip if already correct ──────────────────────────────────
        current = await self._get_current_think_mode()
        if current and target_label in current:
            self.logger.debug("Think mode already '%s' – skipping", mode)
            self._think_mode_applied = True
            return True

        self.logger.info("Setting think mode → '%s'", mode)

        # ── Step 2: open the dropdown via multiple trigger candidates ─────────
        _TRIGGER_CANDIDATES = [
            # Confirmed class names seen on chat.qwen.ai
            ".qwen-select-thinking-label",
            "[class*='thinking-label']",
            "[class*='think-mode']",
            "[class*='qwen-select']",
            # Generic fallbacks: buttons / divs near the textarea
            "button[class*='think']",
            "div[class*='think']",
            # Any element whose visible text matches known mode labels
        ]

        trigger_clicked = False
        for sel in _TRIGGER_CANDIDATES:
            try:
                el = await self._page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click()
                    trigger_clicked = True
                    self.logger.debug("Opened think-mode dropdown via selector: %s", sel)
                    break
            except Exception:
                continue

        # JS fallback: find any visible element whose text is a known mode label
        if not trigger_clicked:
            trigger_clicked = await self._page.evaluate("""
            () => {
                const knownLabels = ['auto', 'thinking', 'fast'];
                const walk = document.createTreeWalker(
                    document.body, NodeFilter.SHOW_ELEMENT
                );
                let node;
                while ((node = walk.nextNode())) {
                    const text = (node.innerText || '').trim().toLowerCase();
                    if (
                        knownLabels.includes(text) &&
                        node.offsetParent !== null &&
                        !['INPUT','TEXTAREA'].includes(node.tagName)
                    ) {
                        node.click();
                        return true;
                    }
                }
                return false;
            }
            """)
            if trigger_clicked:
                self.logger.debug("Opened think-mode dropdown via JS label scan")

        if not trigger_clicked:
            self.logger.warning("Could not locate think-mode trigger – skipping mode set")
            return False

        await asyncio.sleep(0.5)  # allow dropdown animation to complete

        # ── Step 3 & 4: find and click the target option ──────────────────────
        _OPTION_SELECTORS = [
            # rc-select virtual list (Ant Design)
            ".rc-virtual-list-holder-inner > *",
            ".rc-virtual-list-holder-inner li",
            # Ant Design Select options
            ".ant-select-item",
            ".ant-select-item-option",
            # Generic dropdown patterns
            "[class*='option-item']",
            "[class*='select-option']",
            "[class*='dropdown-item']",
            "[role='option']",
            "[role='menuitem']",
        ]

        clicked = False
        for sel in _OPTION_SELECTORS:
            try:
                items = await self._page.query_selector_all(sel)
                for item in items:
                    try:
                        if not await item.is_visible():
                            continue
                        text = (await item.inner_text()).strip().lower()
                        if target_label in text:
                            await item.click()
                            clicked = True
                            self.logger.debug("Clicked option '%s' via selector: %s", text, sel)
                            break
                    except Exception:
                        continue
                if clicked:
                    break
            except Exception:
                continue

        # ── Step 5: JS brute-force scan all visible text nodes ────────────────
        if not clicked:
            clicked = await self._page.evaluate(f"""
            () => {{
                const target = '{target_label}';
                // Prefer elements with role=option or inside a listbox/popover
                const candidates = [
                    ...document.querySelectorAll('[role="option"],[role="menuitem"],[role="listitem"]'),
                    ...document.querySelectorAll('[class*="option"],[class*="item"],[class*="list"] > *'),
                ];
                for (const el of candidates) {{
                    const text = (el.innerText || el.textContent || '').trim().toLowerCase();
                    if (text === target && el.offsetParent !== null) {{
                        el.click();
                        return true;
                    }}
                }}
                // Wider scan: any element whose exact text matches
                const walker = document.createTreeWalker(
                    document.body, NodeFilter.SHOW_ELEMENT
                );
                let node;
                while ((node = walker.nextNode())) {{
                    const text = (node.innerText || '').trim().toLowerCase();
                    if (text === target && node.offsetParent !== null &&
                        !['INPUT','TEXTAREA','BODY','HTML'].includes(node.tagName)) {{
                        node.click();
                        return true;
                    }}
                }}
                return false;
            }}
            """)
            if clicked:
                self.logger.debug("Clicked option via JS brute-force scan")

        if not clicked:
            self.logger.warning("Could not click think mode option '%s' – proceeding anyway", mode)
            # Don't return False; mark applied so we don't retry on every prompt
            self._think_mode_applied = True
            return False

        await asyncio.sleep(0.4)

        # ── Step 6: verify ────────────────────────────────────────────────────
        new_label = await self._get_current_think_mode()
        if target_label in new_label:
            self.logger.info("Think mode confirmed → '%s' ✓", mode)
        else:
            self.logger.warning(
                "Think mode may not have applied (expected '%s', got '%s') – proceeding",
                target_label, new_label,
            )
        self._think_mode_applied = True
        return True

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
                el = await self._page.wait_for_selector(sel, timeout=1_000, state="visible")
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

    async def send_prompt(
        self,
        prompt: str,
        mode: str = "new",
        think_mode: ThinkMode | None = None,
    ) -> str:
        await self._ensure_page_ready(mode)
        self._last_prompt = prompt

        effective_think = think_mode or self._think_mode

        # Apply think mode whenever: new page loaded, explicit per-call override,
        # or not yet applied in this session.
        if not self._think_mode_applied or think_mode is not None:
            success = await self._set_think_mode(effective_think)
            if not success:
                # Run diagnostic scan so the user can identify correct selectors
                await self.debug_think_mode_selectors()

        input_el = await self._find_input()

        self.logger.info(
            "Submitting prompt (%d chars) [think_mode=%s]", len(prompt), effective_think
        )
        await input_el.click()
        await input_el.fill("")
        await input_el.type(prompt, delay=30)
        await asyncio.sleep(0.5)

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
        return await self._wait_for_generation(pre_count)

    async def _wait_for_generation(self, pre_count: int = 0) -> str:
        timeout_s = _TIMEOUT["response_wait"] // 1000
        stability_interval = _TIMEOUT["stability_check"] / 1000
        stability_needed = 2

        deadline = asyncio.get_event_loop().time() + timeout_s
        prev_text = ""
        stable_count = 0
        appeared = False

        while asyncio.get_event_loop().time() < deadline:
            if not appeared:
                cur_count = await self._page.evaluate("""
                () => document.querySelectorAll(
                    '.chat-message-container .chat-response-message, .qwen-markdown-loose, .qwen-markdown'
                ).length
                """)
                if cur_count > pre_count:
                    appeared = True
                    self.logger.debug(
                        "New response element appeared (count: %d → %d)", pre_count, cur_count
                    )
                else:
                    await asyncio.sleep(0.5)
                    continue

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
        think_mode: ThinkMode | None = None,
        headless: bool = True,
        cookies_dir: Path | str | None = None,
        max_concurrent: int = 3,
    ) -> list[dict]:
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _single(prompt: str) -> dict:
            async with semaphore:
                async with cls(
                    headless=headless,
                    cookies_dir=cookies_dir,
                    think_mode=think_mode,
                ) as scraper:
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