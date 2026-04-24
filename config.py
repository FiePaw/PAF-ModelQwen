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

# ─── Ensure directories exist ────────────────────────────────────────────────
for d in [COOKIES_DIR, OUTPUT_DIR, LOGS_DIR, CODE_OUTPUT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ─── Browser Settings ────────────────────────────────────────────────────────
BROWSER_CONFIG = {
    "headless": True,
    "slow_mo": 50,                    # ms delay between actions (helps with anti-bot)
    "viewport": {"width": 1280, "height": 800},
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "locale": "en-US",
    "timezone_id": "Asia/Jakarta",
}

# ─── Qwen AI Settings ────────────────────────────────────────────────────────
QWEN_CONFIG = {
    "base_url": "https://chat.qwen.ai",
    "new_chat_url": "https://chat.qwen.ai",
    "selectors": {
        # Input area
        "prompt_textarea": 'textarea[placeholder], div[contenteditable="true"], #chat-input',
        "send_button": 'button[type="submit"], button[aria-label*="Send"], button[aria-label*="send"]',

        # Response detection
        "response_container": '.message-content, .chat-message, [class*="response"], [class*="message"]',
        "loading_indicator": '[class*="loading"], [class*="spinner"], [class*="typing"]',
        "stop_button": 'button[aria-label*="Stop"], button[title*="Stop"]',

        # Navigation
        "new_chat_button": 'button[aria-label*="New chat"], a[href*="new"], [class*="new-chat"]',
    },
    "timeouts": {
        "page_load": 30_000,          # ms
        "response_wait": 300_000,     # 5 minutes max for AI response
        "stability_check": 3_000,     # ms between stability checks
        "between_actions": 1_500,     # ms between UI actions
    },
}

# ─── Rate Limit / Account Rotation ───────────────────────────────────────────
ROTATION_CONFIG = {
    # Triggers that indicate we should rotate to next account
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
    "retry_delay": 5,              # seconds between retries
    "rotation_delay": 3,           # seconds before switching account
}

# ─── Output Settings ─────────────────────────────────────────────────────────
OUTPUT_CONFIG = {
    "json_indent": 2,
    "encoding": "utf-8",
    "timestamp_format": "%Y%m%d_%H%M%S",
    "max_filename_length": 50,      # truncate long prompts for filenames
}

# ─── Logging ─────────────────────────────────────────────────────────────────
LOG_CONFIG = {
    "level": "INFO",                # DEBUG | INFO | WARNING | ERROR
    "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    "date_format": "%Y-%m-%d %H:%M:%S",
    "log_file": LOGS_DIR / "scraper.log",
    "max_bytes": 10 * 1024 * 1024, # 10 MB
    "backup_count": 3,
}
