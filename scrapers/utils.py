"""
Utility / helper functions for AIChatScraper
"""

import json
import logging
import re
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from config import LOG_CONFIG, OUTPUT_CONFIG


# ─── Logging Setup ────────────────────────────────────────────────────────────

# ── ANSI support detection ────────────────────────────────────────────────────

def _enable_windows_ansi() -> bool:
    """Aktifkan Virtual Terminal Processing di Windows cmd/PowerShell."""
    try:
        import ctypes
        import ctypes.wintypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        ENABLE_VT = 0x0004
        if mode.value & ENABLE_VT:
            return True
        return bool(kernel32.SetConsoleMode(handle, mode.value | ENABLE_VT))
    except Exception:
        return False


def _supports_color() -> bool:
    """Deteksi apakah stderr mendukung ANSI color output."""
    if not hasattr(sys.stderr, "isatty") or not sys.stderr.isatty():
        return False
    if sys.platform == "win32":
        return _enable_windows_ansi()
    return True


_COLOR_ON = _supports_color()


def _c(code: str) -> str:
    """Return ANSI code jika warna didukung, string kosong jika tidak."""
    return code if _COLOR_ON else ""


# ── ANSI color codes ──────────────────────────────────────────────────────────
_RESET   = _c("\033[0m")
_BOLD    = _c("\033[1m")
_DIM     = _c("\033[2m")
_RED     = _c("\033[91m")
_GREEN   = _c("\033[92m")
_YELLOW  = _c("\033[93m")
_BLUE    = _c("\033[94m")
_MAGENTA = _c("\033[95m")
_CYAN    = _c("\033[96m")
_WHITE   = _c("\033[37m")
_BG_RED  = _c("\033[41m")

_LEVEL_STYLES: dict[int, tuple[str, str]] = {
    logging.DEBUG:    (_DIM + _CYAN,            "DEBUG  "),
    logging.INFO:     (_GREEN,                  "INFO   "),
    logging.WARNING:  (_YELLOW,                 "WARN   "),
    logging.ERROR:    (_RED,                    "ERROR  "),
    logging.CRITICAL: (_BOLD + _BG_RED + _WHITE, "CRIT   "),
}

_HIGHLIGHTS: list[tuple[str, str]] = [
    ("✅",        f"{_GREEN}✅{_RESET}"),
    ("❌",        f"{_RED}❌{_RESET}"),
    ("🔌",        f"{_CYAN}🔌{_RESET}"),
    ("🔄",        f"{_YELLOW}🔄{_RESET}"),
    ("Pool ready", f"{_GREEN}{_BOLD}Pool ready{_RESET}"),
    ("Terhubung",  f"{_GREEN}Terhubung{_RESET}"),
    ("Warming up", f"{_CYAN}Warming up{_RESET}"),
    ("CONTINUE",   f"{_MAGENTA}{_BOLD}CONTINUE{_RESET}"),
    ("NEW",        f"{_CYAN}{_BOLD}NEW{_RESET}"),
    ("Gagal",      f"{_RED}Gagal{_RESET}"),
    ("Error",      f"{_RED}Error{_RESET}"),
    ("error",      f"{_RED}error{_RESET}"),
    ("Timeout",    f"{_RED}Timeout{_RESET}"),
    ("Reconnect",  f"{_YELLOW}Reconnect{_RESET}"),
    ("reconnect",  f"{_YELLOW}reconnect{_RESET}"),
    ("Konek",      f"{_CYAN}Konek{_RESET}"),
    ("Menutup",    f"{_YELLOW}Menutup{_RESET}"),
    ("dihentikan", f"{_YELLOW}dihentikan{_RESET}"),
    ("idle=",      f"{_GREEN}idle={_RESET}"),
    ("busy=",      f"{_YELLOW}busy={_RESET}"),
    ("dead=",      f"{_RED}dead={_RESET}"),
    ("starting=",  f"{_CYAN}starting={_RESET}"),
    ("total=",     f"{_WHITE}total={_RESET}"),
]


def _colorize(msg: str) -> str:
    msg = re.sub(r"(Worker#\d+)", lambda m: f"{_BLUE}{_BOLD}{m.group(1)}{_RESET}", msg)
    msg = re.sub(r"(\[[0-9a-f]{6,}\])", lambda m: f"{_YELLOW}{m.group(1)}{_RESET}", msg)
    for kw, colored in _HIGHLIGHTS:
        if kw in msg:
            msg = msg.replace(kw, colored, 1)
    return msg


class _PrettyConsoleFormatter(logging.Formatter):
    """Colorful, compact formatter for console output only."""
    _SEP = f"{_DIM}│{_RESET}"

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        ts = self.formatTime(record, "%H:%M:%S")
        color, label = _LEVEL_STYLES.get(record.levelno, (_WHITE, f"{record.levelname:<7}"))
        name = record.name[:14]
        msg  = _colorize(record.getMessage())
        if record.exc_info:
            exc = self.formatException(record.exc_info)
            msg += "\n" + "\n".join(f"  {_RED}{l}{_RESET}" for l in exc.splitlines())
        return (
            f"{_DIM}{ts}{_RESET}  "
            f"{color}{label}{_RESET}  "
            f"{_DIM}{name:<14}{_RESET}  "
            f"{self._SEP}  {msg}"
        )


def setup_logger(name: str) -> logging.Logger:
    """Create a named logger with both console and rotating file handlers."""
    logger = logging.getLogger(name)
    if logger.handlers:          # avoid duplicate handlers on re-import
        return logger

    logger.setLevel(LOG_CONFIG["level"])

    # Plain formatter untuk file (tanpa ANSI agar log file tetap bersih)
    plain_formatter = logging.Formatter(
        fmt=LOG_CONFIG["format"],
        datefmt=LOG_CONFIG["date_format"],
    )

    # Console handler — pakai pretty formatter berwarna
    ch = logging.StreamHandler(sys.stderr)
    ch.setFormatter(_PrettyConsoleFormatter())
    logger.addHandler(ch)

    # File handler (rotating) — tetap plain text, tidak berubah
    fh = RotatingFileHandler(
        LOG_CONFIG["log_file"],
        maxBytes=LOG_CONFIG["max_bytes"],
        backupCount=LOG_CONFIG["backup_count"],
        encoding="utf-8",
    )
    fh.setFormatter(plain_formatter)
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