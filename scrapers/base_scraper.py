"""
BaseAIChatScraper – abstract async base class for AI chat scrapers.

Provides:
  • Browser lifecycle  (launch / close)
  • Cookie management  (load / save)
  • Dynamic-content waiting with stability detection
  • Output helpers     (JSON, code files)
  • Account-rotation   framework
"""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    async_playwright,
)

from config import (
    BROWSER_CONFIG,
    CODE_OUTPUT_DIR,
    COOKIES_DIR,
    OUTPUT_DIR,
    OUTPUT_CONFIG,
    ROTATION_CONFIG,
)
from scrapers.utils import (
    contains_any,
    detect_file_type,
    discover_cookie_files,
    extract_code_blocks,
    load_json,
    normalize_cookies,
    retry_sleep,
    safe_filename,
    save_code_files,
    save_json,
    setup_logger,
    timestamped_filename,
)


class BaseAIChatScraper(ABC):
    """
    Abstract base class for async AI-chat scrapers.

    Subclasses must implement:
        • send_prompt(prompt)   → str      (raw AI response text)
        • is_rate_limited()     → bool
        • is_session_expired()  → bool

    Optional override:
        • wait_for_response()   (if selector logic differs)
    """

    # ── Construction ──────────────────────────────────────────────────────────

    def __init__(
        self,
        headless: bool = True,
        cookies_path: Path | str | None = None,
        *,
        cookies_dir: Path | str | None = None,
    ) -> None:
        self.logger = setup_logger(self.__class__.__name__)
        self.headless = headless

        # Single-cookie-file mode
        self.cookies_path: Path | None = Path(cookies_path) if cookies_path else None

        # Multi-account rotation mode
        self.cookies_dir: Path = Path(cookies_dir) if cookies_dir else COOKIES_DIR
        self._cookie_files: list[Path] = []
        self._cookie_index: int = 0          # which account is active

        # Playwright objects
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    # ── Browser lifecycle ─────────────────────────────────────────────────────

    async def launch_browser(self) -> None:
        """Start Playwright, launch Chromium and create a fresh context."""
        self.logger.info("Launching browser (headless=%s)", self.headless)
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            slow_mo=BROWSER_CONFIG["slow_mo"],
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
            ],
        )
        self._context = await self._browser.new_context(
            viewport=BROWSER_CONFIG["viewport"],
            user_agent=BROWSER_CONFIG["user_agent"],
            locale=BROWSER_CONFIG["locale"],
            timezone_id=BROWSER_CONFIG["timezone_id"],
        )
        self._page = await self._context.new_page()
        self.logger.debug("Browser launched successfully")

    async def close_browser(self) -> None:
        """Gracefully close browser and Playwright."""
        self.logger.info("Closing browser")
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    # ── Cookie management ─────────────────────────────────────────────────────

    async def load_cookies(self, path: Path | str | None = None) -> bool:
        """
        Load cookies from *path* (or self.cookies_path) into the current context.
        Returns True on success.
        """
        target = Path(path) if path else self.cookies_path
        if not target:
            self.logger.warning("No cookies path provided – skipping load")
            return False
        if not target.exists():
            self.logger.warning("Cookie file not found: %s", target)
            return False

        try:
            raw = load_json(target)
            cookies = normalize_cookies(raw if isinstance(raw, list) else [])
            await self._context.add_cookies(cookies)
            self.logger.info("Loaded %d cookie(s) from %s", len(cookies), target.name)
            return True
        except Exception as exc:
            self.logger.error("Failed to load cookies from %s: %s", target, exc)
            return False

    async def save_cookies(self, path: Path | str | None = None) -> bool:
        """
        Dump current browser cookies to *path* (or self.cookies_path).
        Returns True on success.
        """
        target = Path(path) if path else self.cookies_path
        if not target:
            self.logger.warning("No cookies path provided – skipping save")
            return False

        try:
            cookies = await self._context.cookies()
            save_json(cookies, target)
            self.logger.info("Saved %d cookie(s) to %s", len(cookies), target.name)
            return True
        except Exception as exc:
            self.logger.error("Failed to save cookies: %s", exc)
            return False

    # ── Multi-account rotation ────────────────────────────────────────────────

    def _discover_accounts(self) -> None:
        """Discover all .json cookie files in cookies_dir."""
        self._cookie_files = discover_cookie_files(self.cookies_dir)
        if not self._cookie_files:
            self.logger.warning("No cookie files found in %s", self.cookies_dir)
        else:
            self.logger.info(
                "Discovered %d account cookie file(s): %s",
                len(self._cookie_files),
                [f.name for f in self._cookie_files],
            )

    @property
    def _current_cookie_file(self) -> Path | None:
        if not self._cookie_files:
            return None
        return self._cookie_files[self._cookie_index % len(self._cookie_files)]

    async def _rotate_account(self) -> bool:
        """
        Switch to the next available cookie file and reload.
        Returns False if we have cycled through all accounts.
        """
        total = len(self._cookie_files)
        if total <= 1:
            self.logger.error("No additional accounts available for rotation")
            return False

        next_index = (self._cookie_index + 1) % total
        if next_index == 0:
            self.logger.error("All accounts exhausted – no more rotation possible")
            return False

        self.logger.warning(
            "Rotating account: %s → %s",
            self._cookie_files[self._cookie_index].name,
            self._cookie_files[next_index].name,
        )
        self._cookie_index = next_index
        retry_sleep(ROTATION_CONFIG["rotation_delay"])

        # Re-create context with new cookies
        if self._context:
            await self._context.close()
        self._context = await self._browser.new_context(
            viewport=BROWSER_CONFIG["viewport"],
            user_agent=BROWSER_CONFIG["user_agent"],
            locale=BROWSER_CONFIG["locale"],
            timezone_id=BROWSER_CONFIG["timezone_id"],
        )
        self._page = await self._context.new_page()
        await self.load_cookies(self._current_cookie_file)
        return True

    # ── Waiting helpers ───────────────────────────────────────────────────────

    async def wait_for_response(
        self,
        response_selector: str,
        loading_selector: str | None = None,
        timeout: int = 300,
        stability_checks: int = 2,
        stability_interval: float = 3.0,
    ) -> str:
        """
        Poll the page until the AI has finished generating a response.

        Strategy:
          1. Wait for the loading indicator to disappear (if selector given).
          2. Capture response-container text.
          3. Compare across *stability_checks* intervals; if stable → done.

        Returns the final response text.
        Raises TimeoutError if *timeout* seconds elapse.
        """
        self.logger.debug("Waiting for response (timeout=%ds)", timeout)
        deadline = asyncio.get_event_loop().time() + timeout
        prev_texts: list[str] = []
        stable_count = 0

        while asyncio.get_event_loop().time() < deadline:
            # Check if loading indicator is gone
            if loading_selector:
                try:
                    await self._page.wait_for_selector(
                        loading_selector,
                        state="hidden",
                        timeout=5_000,
                    )
                except Exception:
                    pass  # Selector might not exist at all – that's fine

            # Grab current text of all response containers
            try:
                elements = await self._page.query_selector_all(response_selector)
                texts = []
                for el in elements:
                    t = await el.inner_text()
                    texts.append(t.strip())
                current_text = "\n".join(texts)
            except Exception:
                current_text = ""

            if not current_text:
                await asyncio.sleep(1)
                continue

            if prev_texts and current_text == prev_texts[-1]:
                stable_count += 1
                if stable_count >= stability_checks:
                    self.logger.debug("Response stable after %d check(s)", stable_count)
                    return current_text
            else:
                stable_count = 0

            prev_texts.append(current_text)
            await asyncio.sleep(stability_interval)

        raise TimeoutError(
            f"Response not stable after {timeout}s – last text length: {len(prev_texts[-1]) if prev_texts else 0}"
        )

    # ── Output helpers ────────────────────────────────────────────────────────

    def detect_file_type(self, content: str) -> str:
        return detect_file_type(content)

    def extract_code_blocks(self, content: str) -> list[dict]:
        return extract_code_blocks(content)

    def save_to_json(self, data: Any, filename: str | None = None) -> Path:
        if filename is None:
            filename = timestamped_filename("response")
        path = OUTPUT_DIR / filename
        save_json(data, path)
        self.logger.info("Saved JSON → %s", path)
        return path

    def save_code_files(self, blocks: list[dict], output_dir: Path | None = None, prefix: str = "snippet") -> list[Path]:
        target = output_dir or CODE_OUTPUT_DIR
        paths = save_code_files(blocks, target, prefix)
        self.logger.info("Saved %d code file(s) to %s", len(paths), target)
        return paths

    # ── Abstract interface (subclasses implement these) ───────────────────────

    @abstractmethod
    async def send_prompt(self, prompt: str, mode: str = "new") -> str:
        """
        Send *prompt* to the AI chat and return the raw response text.
        *mode* is 'new' or 'continue'.
        """
        ...

    @abstractmethod
    async def is_rate_limited(self) -> bool:
        """Return True if the current page/response signals a rate limit."""
        ...

    @abstractmethod
    async def is_session_expired(self) -> bool:
        """Return True if the session has expired and re-login is required."""
        ...

    # ── High-level scrape with auto-rotation ─────────────────────────────────

    async def scrape(self, prompt: str, mode: str = "new") -> dict:
        """
        Full scrape cycle with automatic account rotation on failure.

        Returns a result dict:
          {
            prompt, response, file_type, code_blocks,
            account_used, timestamp, success, error
          }
        """
        self._discover_accounts()

        # Load cookies (multi-account or single)
        initial_cookie = self._current_cookie_file or self.cookies_path
        if initial_cookie:
            await self.load_cookies(initial_cookie)

        max_total_attempts = max(len(self._cookie_files), 1) * ROTATION_CONFIG["max_retries_per_account"]
        attempt = 0

        while attempt < max_total_attempts:
            attempt += 1
            account_name = (
                self._current_cookie_file.stem if self._current_cookie_file else "default"
            )
            self.logger.info(
                "Attempt %d/%d using account '%s'",
                attempt, max_total_attempts, account_name,
            )

            try:
                response_text = await self.send_prompt(prompt, mode)

                # Check for soft errors embedded in the response
                if contains_any(response_text, ROTATION_CONFIG["rate_limit_phrases"]):
                    self.logger.warning("Rate limit detected in response – rotating account")
                    if not await self._rotate_account():
                        break
                    continue

                if contains_any(response_text, ROTATION_CONFIG["session_expired_phrases"]):
                    self.logger.warning("Session expired detected – rotating account")
                    if not await self._rotate_account():
                        break
                    continue

                # Success!
                blocks = self.extract_code_blocks(response_text)
                result = {
                    "prompt": prompt,
                    "response": response_text,
                    "file_type": self.detect_file_type(response_text),
                    "code_blocks": blocks,
                    "code_block_count": len(blocks),
                    "account_used": account_name,
                    "timestamp": datetime.now().isoformat(),
                    "success": True,
                    "error": None,
                }
                self.logger.info(
                    "Scrape successful – %d char(s), %d code block(s)",
                    len(response_text), len(blocks),
                )
                return result

            except TimeoutError as exc:
                self.logger.error("Timeout on attempt %d: %s", attempt, exc)
                retry_sleep(ROTATION_CONFIG["retry_delay"])

            except Exception as exc:
                self.logger.error("Error on attempt %d: %s", attempt, exc, exc_info=True)

                # Check if this looks like a rate-limit / auth error
                if await self.is_rate_limited() or await self.is_session_expired():
                    if not await self._rotate_account():
                        break
                else:
                    retry_sleep(ROTATION_CONFIG["retry_delay"])

        return {
            "prompt": prompt,
            "response": None,
            "file_type": None,
            "code_blocks": [],
            "code_block_count": 0,
            "account_used": None,
            "timestamp": datetime.now().isoformat(),
            "success": False,
            "error": "All attempts exhausted",
        }

    # ── Context manager ───────────────────────────────────────────────────────

    async def __aenter__(self) -> "BaseAIChatScraper":
        await self.launch_browser()
        return self

    async def __aexit__(self, *_) -> None:
        await self.close_browser()
