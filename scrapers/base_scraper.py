"""
BaseAIChatScraper – abstract async base class for AI chat scrapers.

Provides:
  • Browser lifecycle  (launch / close) – supports both persistent-context
                        and legacy ephemeral-context modes
  • Cookie management  (load / save / seed into profile)
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
    PERSISTENT_CONTEXT_CONFIG,
    PROFILES_DIR,
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

    Persistent-context mode (default, PERSISTENT_CONTEXT_CONFIG["enabled"]=True):
        • Playwright's launch_persistent_context() is used.
        • Each account maps to a dedicated profile directory under PROFILES_DIR.
        • On the very first run for an account the cookie file is seeded into
          the profile; subsequent runs reuse the stored browser state directly
          (no cookie injection needed → faster startup, cookies/localStorage
          survive across restarts).
        • self._browser is None in this mode; self._context is the persistent
          BrowserContext returned by Playwright.

    Ephemeral mode (PERSISTENT_CONTEXT_CONFIG["enabled"]=False):
        • Original Browser + BrowserContext flow; no state is persisted.
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

        self.cookies_path: Path | None = Path(cookies_path) if cookies_path else None
        self.cookies_dir: Path = Path(cookies_dir) if cookies_dir else COOKIES_DIR
        self._cookie_files: list[Path] = []
        self._cookie_index: int = 0

        self._playwright = None
        self._browser: Browser | None = None       # None in persistent mode
        self._context: BrowserContext | None = None
        self._page: Page | None = None

        self._persistent_mode: bool = PERSISTENT_CONTEXT_CONFIG["enabled"]

    # ── Profile path helpers ──────────────────────────────────────────────────

    def _profile_dir_for(self, cookie_file: Path | None) -> Path:
        """Return the profile directory that corresponds to *cookie_file*."""
        if cookie_file:
            return PROFILES_DIR / cookie_file.stem
        return PROFILES_DIR / PERSISTENT_CONTEXT_CONFIG["default_profile"]

    def _profile_seeded(self, profile_dir: Path) -> bool:
        """
        Return True if this profile has already been bootstrapped.
        We consider it seeded when the Chromium 'Default' sub-dir exists,
        which Playwright creates on first launch_persistent_context() call.
        """
        return (profile_dir / "Default").exists()

    # ── Browser lifecycle ─────────────────────────────────────────────────────

    async def launch_browser(self, cookie_file: Path | None = None) -> None:
        """
        Start Playwright and either:
          • Open a persistent context for *cookie_file*'s profile (default), or
          • Launch an ephemeral browser + context (legacy mode).

        *cookie_file* is used only to determine which profile directory to open;
        if None the default profile is used.
        """
        self.logger.info(
            "Launching browser (headless=%s, persistent=%s)",
            self.headless, self._persistent_mode,
        )
        self._playwright = await async_playwright().start()

        if self._persistent_mode:
            await self._launch_persistent(cookie_file)
        else:
            await self._launch_ephemeral()

        self.logger.debug("Browser launched successfully")

    async def _launch_persistent(self, cookie_file: Path | None) -> None:
        """Launch a persistent browser context, seeding cookies on first run."""
        cfg = PERSISTENT_CONTEXT_CONFIG
        profile_dir = self._profile_dir_for(cookie_file)
        profile_dir.mkdir(parents=True, exist_ok=True)

        first_run = not self._profile_seeded(profile_dir)

        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=self.headless,
            slow_mo=BROWSER_CONFIG["slow_mo"],
            viewport=BROWSER_CONFIG["viewport"],
            user_agent=BROWSER_CONFIG["user_agent"],
            locale=BROWSER_CONFIG["locale"],
            timezone_id=BROWSER_CONFIG["timezone_id"],
            args=cfg["args"],
        )
        # Reuse existing page or open a new one
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        self._browser = None  # not used in persistent mode

        # Seed cookies into the profile on the very first run
        if first_run and cookie_file and cookie_file.exists():
            self.logger.info("First run for profile '%s' – seeding cookies", profile_dir.name)
            await self.load_cookies(cookie_file)
        elif not first_run:
            self.logger.info("Reusing existing profile '%s'", profile_dir.name)

    async def _launch_ephemeral(self) -> None:
        """Original ephemeral browser + context launch."""
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

    async def close_browser(self) -> None:
        """Gracefully close the context / browser and stop Playwright."""
        self.logger.info("Closing browser")
        if self._context:
            await self._context.close()
        if self._browser:          # None in persistent mode – skip
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    # ── Cookie management ─────────────────────────────────────────────────────

    async def load_cookies(self, path: Path | str | None = None) -> bool:
        """
        Inject cookies from *path* into the current context.
        In persistent mode this is only needed once (profile seeding).
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
        In persistent mode the profile already persists cookies automatically;
        this method is kept for explicit export / debugging.
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
        Switch to the next available account.
        In persistent mode: close the current context and open the next
        account's profile directory.
        In ephemeral mode: close the current context and inject the next
        account's cookies into a fresh context.
        Returns False if all accounts are exhausted.
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

        next_cookie_file = self._cookie_files[self._cookie_index]

        if self._persistent_mode:
            # Close current persistent context and open the next profile
            if self._context:
                await self._context.close()
            await self._launch_persistent(next_cookie_file)
        else:
            # Ephemeral: recreate context and inject cookies
            if self._context:
                await self._context.close()
            self._context = await self._browser.new_context(
                viewport=BROWSER_CONFIG["viewport"],
                user_agent=BROWSER_CONFIG["user_agent"],
                locale=BROWSER_CONFIG["locale"],
                timezone_id=BROWSER_CONFIG["timezone_id"],
            )
            self._page = await self._context.new_page()
            await self.load_cookies(next_cookie_file)

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
        self.logger.debug("Waiting for response (timeout=%ds)", timeout)
        deadline = asyncio.get_event_loop().time() + timeout
        prev_texts: list[str] = []
        stable_count = 0

        while asyncio.get_event_loop().time() < deadline:
            if loading_selector:
                try:
                    await self._page.wait_for_selector(
                        loading_selector, state="hidden", timeout=5_000,
                    )
                except Exception:
                    pass

            try:
                elements = await self._page.query_selector_all(response_selector)
                texts = [((await el.inner_text()) or "").strip() for el in elements]
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
            f"Response not stable after {timeout}s – "
            f"last text length: {len(prev_texts[-1]) if prev_texts else 0}"
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

    def save_code_files(
        self,
        blocks: list[dict],
        output_dir: Path | None = None,
        prefix: str = "snippet",
    ) -> list[Path]:
        target = output_dir or CODE_OUTPUT_DIR
        paths = save_code_files(blocks, target, prefix)
        self.logger.info("Saved %d code file(s) to %s", len(paths), target)
        return paths

    # ── Abstract interface ────────────────────────────────────────────────────

    @abstractmethod
    async def send_prompt(self, prompt: str, mode: str = "new", **kwargs) -> str: ...

    @abstractmethod
    async def is_rate_limited(self) -> bool: ...

    @abstractmethod
    async def is_session_expired(self) -> bool: ...

    def _extra_send_kwargs(self) -> dict:
        """
        Subclasses can override this to inject extra keyword arguments into
        the send_prompt() call made by scrape(). For example, QwenScraper
        returns {"think_mode": self._think_mode}.
        """
        return {}

    # ── High-level scrape with auto-rotation ─────────────────────────────────

    async def scrape(self, prompt: str, mode: str = "new") -> dict:
        self._discover_accounts()

        # In persistent mode, launch_browser() already handled cookie seeding.
        # In ephemeral mode, inject cookies manually.
        if not self._persistent_mode:
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
                response_text = await self.send_prompt(prompt, mode, **self._extra_send_kwargs())

                if contains_any(response_text, ROTATION_CONFIG["rate_limit_phrases"]):
                    self.logger.warning("Rate limit detected – rotating account")
                    if not await self._rotate_account():
                        break
                    continue

                if contains_any(response_text, ROTATION_CONFIG["session_expired_phrases"]):
                    self.logger.warning("Session expired – rotating account")
                    if not await self._rotate_account():
                        break
                    continue

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
        # Pass the initial cookie file so the correct profile is opened
        initial_cookie = self.cookies_path or (
            self._cookie_files[self._cookie_index]
            if self._cookie_files
            else None
        )
        await self.launch_browser(cookie_file=initial_cookie)
        return self

    async def __aexit__(self, *_) -> None:
        await self.close_browser()