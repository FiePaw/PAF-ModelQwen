"""
Configuration and paths for AIChatScraper
"""

import os
from pathlib import Path

# ─── Base Directories ───────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.resolve()
COOKIES_DIR = BASE_DIR / "cookies"
OUTPUT_DIR = BASE_DIR / "output"
LOGS_DIR = BASE_DIR / "logs"
CODE_OUTPUT_DIR = OUTPUT_DIR / "code"
PROFILES_DIR = BASE_DIR / "profiles"          # persistent browser profile storage

# ─── Ensure directories exist ────────────────────────────────────────────────
for d in [COOKIES_DIR, OUTPUT_DIR, LOGS_DIR, CODE_OUTPUT_DIR, PROFILES_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ─── Browser Settings ────────────────────────────────────────────────────────
BROWSER_CONFIG = {
    "headless": True,
    "slow_mo": 0,
    "viewport": {"width": 1280, "height": 800},
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "locale": "en-US",
    "timezone_id": "Asia/Jakarta",
}

# ─── Persistent Context Settings ─────────────────────────────────────────────
PERSISTENT_CONTEXT_CONFIG = {
    # When True  → launch_persistent_context() is used; browser state
    #              (cookies, localStorage, cache) persists across runs
    #              inside the profile directory automatically.
    # When False → legacy Browser + BrowserContext flow (ephemeral).
    "enabled": True,

    # Default profile sub-directory name inside PROFILES_DIR.
    # Multi-account rotation creates one profile dir per cookie file stem:
    #   profiles/account1/   profiles/account2/
    "default_profile": "default",

    # Extra Chromium launch args for persistent mode.
    "args": [
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
        "--disable-dev-shm-usage",
    ],
}

# ─── Qwen AI Settings ────────────────────────────────────────────────────────
QWEN_CONFIG = {
    "base_url": "https://chat.qwen.ai",
    "new_chat_url": "https://chat.qwen.ai",
    "selectors": {
        "prompt_textarea": 'textarea[placeholder], div[contenteditable="true"], #chat-input',
        "send_button": 'button[type="submit"], button[aria-label*="Send"], button[aria-label*="send"]',
        "response_container": '.message-content, .chat-message, [class*="response"], [class*="message"]',
        "loading_indicator": '[class*="loading"], [class*="spinner"], [class*="typing"]',
        "stop_button": 'button[aria-label*="Stop"], button[title*="Stop"]',
        "new_chat_button": 'button[aria-label*="New chat"], a[href*="new"], [class*="new-chat"]',

        # Think mode dropdown
        "think_mode_trigger": ".qwen-select-thinking-label",
        "think_mode_selected": ".qwen-select-option-selected-label-container",
        "think_mode_options": ".rc-virtual-list-holder-inner",
    },
    "timeouts": {
        "page_load": 10_000,
        "response_wait": 300_000,
        "stability_check": 1_000,
        "between_actions": 800,
    },

    # ── Think mode ────────────────────────────────────────────────────────────
    # Valid values: "auto" | "thinking" | "fast"
    # This is the global default; can be overridden per-request via the
    # think_mode argument of send_prompt() / scrape().
    "default_think_mode": "fast",

    # Label text as it appears in the Qwen dropdown (case-insensitive match)
    "think_mode_labels": {
        "auto":     "auto",
        "thinking": "thinking",
        "fast":     "fast",
    },
}

# ─── Rate Limit / Account Rotation ───────────────────────────────────────────
ROTATION_CONFIG = {
    "rate_limit_phrases": [
        "rate limit",
        "too many requests",
        "please try again later",
        "usage limit",
        "quota exceeded",
        "you've reached",
        "daily limit",
        "request limit",
    ],
    "session_expired_phrases": [
        "session expired",
        "please log in",
        "sign in to continue",
        "unauthorized",
        "login required",
    ],
    "max_retries_per_account": 2,
    "retry_delay": 5,
    "rotation_delay": 3,
}

# ─── Output Settings ─────────────────────────────────────────────────────────
OUTPUT_CONFIG = {
    "json_indent": 2,
    "encoding": "utf-8",
    "timestamp_format": "%Y%m%d_%H%M%S",
    "max_filename_length": 50,
}

# ─── Logging ─────────────────────────────────────────────────────────────────
LOG_CONFIG = {
    "level": "INFO",
    "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    "date_format": "%Y-%m-%d %H:%M:%S",
    "log_file": LOGS_DIR / "scraper.log",
    "max_bytes": 10 * 1024 * 1024,
    "backup_count": 3,
}