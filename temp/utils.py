"""
Utility / helper functions for AIChatScraper
"""

import json
import logging
import re
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from config import LOG_CONFIG, OUTPUT_CONFIG


# ─── Logging Setup ────────────────────────────────────────────────────────────

def setup_logger(name: str) -> logging.Logger:
    """Create a named logger with both console and rotating file handlers."""
    logger = logging.getLogger(name)
    if logger.handlers:          # avoid duplicate handlers on re-import
        return logger

    logger.setLevel(LOG_CONFIG["level"])
    formatter = logging.Formatter(
        fmt=LOG_CONFIG["format"],
        datefmt=LOG_CONFIG["date_format"],
    )

    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # File handler (rotating)
    fh = RotatingFileHandler(
        LOG_CONFIG["log_file"],
        maxBytes=LOG_CONFIG["max_bytes"],
        backupCount=LOG_CONFIG["backup_count"],
        encoding="utf-8",
    )
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    return logger


# ─── File Helpers ─────────────────────────────────────────────────────────────

def safe_filename(text: str, max_len: int = OUTPUT_CONFIG["max_filename_length"]) -> str:
    """Convert arbitrary text into a safe filename fragment."""
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    slug = re.sub(r"[\s_-]+", "_", slug).strip("_")
    return slug[:max_len]


def timestamped_filename(prefix: str, ext: str = "json") -> str:
    """Return a filename like  prefix_20240524_153012.json ."""
    ts = datetime.now().strftime(OUTPUT_CONFIG["timestamp_format"])
    return f"{prefix}_{ts}.{ext}"


def save_json(data: Any, path: Path | str, indent: int = OUTPUT_CONFIG["json_indent"]) -> None:
    """Serialise *data* to a UTF-8 JSON file, creating parent dirs as needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding=OUTPUT_CONFIG["encoding"]) as fh:
        json.dump(data, fh, indent=indent, ensure_ascii=False)


def load_json(path: Path | str) -> Any:
    """Load and return a JSON file; raises FileNotFoundError if missing."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"JSON file not found: {path}")
    with path.open("r", encoding=OUTPUT_CONFIG["encoding"]) as fh:
        return json.load(fh)


# ─── Cookie Helpers ───────────────────────────────────────────────────────────

def discover_cookie_files(cookies_dir: Path) -> list[Path]:
    """Return all .json files inside *cookies_dir*, sorted by name."""
    files = sorted(cookies_dir.glob("*.json"))
    return files


def normalize_cookies(raw: list[dict]) -> list[dict]:
    """
    Normalise cookies exported by Cookie-Editor extension.
    Playwright expects: name, value, domain, path, secure, httpOnly, sameSite.
    Cookie-Editor may use 'expirationDate' instead of 'expires', etc.
    """
    normalized = []
    for c in raw:
        cookie: dict[str, Any] = {
            "name": c.get("name", ""),
            "value": c.get("value", ""),
            "domain": c.get("domain", ""),
            "path": c.get("path", "/"),
            "secure": c.get("secure", False),
            "httpOnly": c.get("httpOnly", False),
        }
        # sameSite
        same_site = c.get("sameSite", "Lax")
        if isinstance(same_site, str):
            same_site = same_site.capitalize()
        if same_site not in ("Strict", "Lax", "None"):
            same_site = "Lax"
        cookie["sameSite"] = same_site

        # expiry / expirationDate
        for key in ("expires", "expirationDate", "expiry"):
            if key in c and isinstance(c[key], (int, float)):
                cookie["expires"] = int(c[key])
                break

        normalized.append(cookie)
    return normalized


# ─── Code-Block Extraction ────────────────────────────────────────────────────

LANG_EXTENSIONS: dict[str, str] = {
    "python": "py",
    "py": "py",
    "javascript": "js",
    "js": "js",
    "typescript": "ts",
    "ts": "ts",
    "bash": "sh",
    "sh": "sh",
    "shell": "sh",
    "html": "html",
    "css": "css",
    "json": "json",
    "yaml": "yaml",
    "yml": "yaml",
    "sql": "sql",
    "java": "java",
    "cpp": "cpp",
    "c": "c",
    "go": "go",
    "rust": "rs",
    "php": "php",
    "ruby": "rb",
    "swift": "swift",
    "kotlin": "kt",
    "r": "r",
    "markdown": "md",
    "md": "md",
    "xml": "xml",
    "dockerfile": "dockerfile",
    "toml": "toml",
    "ini": "ini",
}

CODE_BLOCK_RE = re.compile(
    r"```(?P<lang>[a-zA-Z0-9_+-]*)\n(?P<code>.*?)```",
    re.DOTALL,
)


def extract_code_blocks(text: str) -> list[dict]:
    """
    Parse all fenced code blocks from *text*.
    Returns a list of dicts: {lang, extension, code, index}.
    """
    blocks = []
    for idx, match in enumerate(CODE_BLOCK_RE.finditer(text), start=1):
        lang = match.group("lang").strip().lower() or "text"
        ext = LANG_EXTENSIONS.get(lang, "txt")
        blocks.append({
            "index": idx,
            "lang": lang,
            "extension": ext,
            "code": match.group("code"),
        })
    return blocks


def detect_file_type(content: str) -> str:
    """
    Heuristic: guess the primary file type present in *content*.
    Returns a language string such as 'python', 'javascript', 'text'.
    """
    # Check for explicit code block
    m = CODE_BLOCK_RE.search(content)
    if m and m.group("lang"):
        return m.group("lang").lower()

    # Simple heuristics
    if re.search(r"def |import |from .+ import |class .+:", content):
        return "python"
    if re.search(r"function |const |let |var |=>", content):
        return "javascript"
    if re.search(r"<html|<!DOCTYPE", content, re.IGNORECASE):
        return "html"
    if re.search(r"\$\w+\s*=|echo ", content):
        return "bash"
    return "text"


def save_code_files(blocks: list[dict], output_dir: Path, prefix: str = "snippet") -> list[Path]:
    """
    Write each code block to its own file inside *output_dir*.
    Returns a list of the created Path objects.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    for block in blocks:
        filename = f"{prefix}_{block['index']:02d}.{block['extension']}"
        path = output_dir / filename
        path.write_text(block["code"], encoding="utf-8")
        saved.append(path)
    return saved


# ─── Misc ─────────────────────────────────────────────────────────────────────

def contains_any(text: str, phrases: list[str]) -> bool:
    """Return True if *text* (case-insensitive) contains any of *phrases*."""
    text_lower = text.lower()
    return any(p.lower() in text_lower for p in phrases)


def retry_sleep(seconds: float) -> None:
    """Block for *seconds*; used between retries / rotations."""
    time.sleep(seconds)
