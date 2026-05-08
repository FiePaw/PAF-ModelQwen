"""
pretty_logger.py – Drop-in pengganti setup_logger dari scrapers/utils.py
=========================================================================
Cara pakai:
  Di scrapers/utils.py, ganti / tambahkan:

      from pretty_logger import setup_logger          # ← ganti baris lama

  Atau jika mau tetap pakai nama lama:

      from pretty_logger import setup_logger as setup_logger

Tidak ada perubahan di public.py sama sekali.
"""

from __future__ import annotations

import logging
import sys

# ── ANSI color codes ───────────────────────────────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"

# Foreground
BLACK   = "\033[30m"
RED     = "\033[31m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
BLUE    = "\033[34m"
MAGENTA = "\033[35m"
CYAN    = "\033[36m"
WHITE   = "\033[37m"

# Bright foreground
BRED    = "\033[91m"
BGREEN  = "\033[92m"
BYELLOW = "\033[93m"
BBLUE   = "\033[94m"
BMAGENTA= "\033[95m"
BCYAN   = "\033[96m"
BWHITE  = "\033[97m"

# Background
BG_RED    = "\033[41m"
BG_YELLOW = "\033[43m"
BG_BLUE   = "\033[44m"
BG_CYAN   = "\033[46m"


# ── Level styling ──────────────────────────────────────────────────────────────
LEVEL_STYLES: dict[int, tuple[str, str]] = {
    logging.DEBUG:    (DIM + CYAN,          "DEBUG  "),
    logging.INFO:     (BGREEN,              "INFO   "),
    logging.WARNING:  (BYELLOW,             "WARN   "),
    logging.ERROR:    (BRED,                "ERROR  "),
    logging.CRITICAL: (BOLD + BG_RED + WHITE, "CRIT   "),
}

# ── Keyword highlights dalam message ──────────────────────────────────────────
# Format: (string_to_find, replacement_with_color)
# Urutan penting — lebih spesifik dulu.
_HIGHLIGHTS: list[tuple[str, str]] = [
    # Status sukses
    ("✅",          f"{BGREEN}✅{RESET}"),
    ("Pool ready",  f"{BGREEN}{BOLD}Pool ready{RESET}"),
    ("Terhubung",   f"{BGREEN}Terhubung{RESET}"),

    # Status gagal / error
    ("❌",          f"{BRED}❌{RESET}"),
    ("Gagal",       f"{BRED}Gagal{RESET}"),
    ("Error",       f"{BRED}Error{RESET}"),
    ("error",       f"{BRED}error{RESET}"),
    ("Timeout",     f"{BRED}Timeout{RESET}"),
    ("timeout",     f"{BRED}timeout{RESET}"),
    ("dead",        f"{BRED}dead{RESET}"),

    # Status koneksi / reconnect
    ("🔌",          f"{BCYAN}🔌{RESET}"),
    ("🔄",          f"{BYELLOW}🔄{RESET}"),
    ("Reconnect",   f"{BYELLOW}Reconnect{RESET}"),
    ("reconnect",   f"{BYELLOW}reconnect{RESET}"),
    ("Konek",       f"{BCYAN}Konek{RESET}"),

    # Mode task
    ("CONTINUE",    f"{BMAGENTA}{BOLD}CONTINUE{RESET}"),
    ("NEW",         f"{BCYAN}{BOLD}NEW{RESET}"),

    # Pool state
    ("idle=",       f"{BGREEN}idle={RESET}"),
    ("busy=",       f"{BYELLOW}busy={RESET}"),
    ("dead=",       f"{BRED}dead={RESET}"),
    ("starting=",   f"{BCYAN}starting={RESET}"),
    ("total=",      f"{WHITE}total={RESET}"),

    # Worker label pattern (Worker#N)
    # Di-handle khusus di _colorize_message()

    # Warming up
    ("Warming up",  f"{BCYAN}Warming up{RESET}"),

    # Shutdown
    ("Menutup",     f"{BYELLOW}Menutup{RESET}"),
    ("dihentikan",  f"{BYELLOW}dihentikan{RESET}"),
]


def _colorize_message(msg: str) -> str:
    """Terapkan highlight warna ke isi pesan."""
    import re

    # Worker label: Worker#N → biru bold
    msg = re.sub(
        r"(Worker#\d+)",
        lambda m: f"{BBLUE}{BOLD}{m.group(1)}{RESET}",
        msg,
    )

    # Request ID pendek dalam kurung siku: [abc12345] → kuning
    msg = re.sub(
        r"(\[[0-9a-f]{6,}\])",
        lambda m: f"{BYELLOW}{m.group(1)}{RESET}",
        msg,
    )

    # Terapkan highlight keyword
    for keyword, colored in _HIGHLIGHTS:
        if keyword in msg:
            msg = msg.replace(keyword, colored, 1)  # replace sekali saja per keyword

    return msg


# ── Custom Formatter ───────────────────────────────────────────────────────────

class PrettyFormatter(logging.Formatter):
    """
    Format log yang rapi dan berwarna:

      HH:MM:SS  LEVEL    logger_name  │  pesan

    Contoh output:
      14:32:01  INFO     local_worker │  Worker#0 🔌 Konek ke VPS: ws://...
      14:32:02  INFO     local_worker │  Worker#0 ✅ Terhubung ke VPS! (max_concurrent=4)
      14:32:05  WARN     local_worker │  ❌ Koneksi ke VPS terputus: ...
    """

    _SEP = f"{DIM}│{RESET}"

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        # ── Timestamp ─────────────────────────────────────────────────────────
        ts = self.formatTime(record, "%H:%M:%S")
        ts_str = f"{DIM}{ts}{RESET}"

        # ── Level ─────────────────────────────────────────────────────────────
        color, label = LEVEL_STYLES.get(record.levelno, (WHITE, f"{record.levelname:<7}"))
        level_str = f"{color}{label}{RESET}"

        # ── Logger name (maks 12 char, kiri-rata) ─────────────────────────────
        name = record.name[:14]
        name_str = f"{DIM}{name:<14}{RESET}"

        # ── Pesan ─────────────────────────────────────────────────────────────
        msg = record.getMessage()
        msg = _colorize_message(msg)

        # Exception info
        if record.exc_info:
            exc_text = self.formatException(record.exc_info)
            # Indentasi exception supaya rapi
            indented = "\n".join("  " + line for line in exc_text.splitlines())
            msg = f"{msg}\n{BRED}{indented}{RESET}"

        # ── Gabungkan ─────────────────────────────────────────────────────────
        return f"{ts_str}  {level_str}  {name_str}  {self._SEP}  {msg}"


# ── Public API ─────────────────────────────────────────────────────────────────

def setup_logger(
    name: str,
    level: int = logging.DEBUG,
    stream=None,
) -> logging.Logger:
    """
    Drop-in pengganti setup_logger dari scrapers/utils.py.

    Mengembalikan logger dengan PrettyFormatter ke stderr (atau stream pilihan).
    Kalau logger sudah punya handler, tidak ditambahkan lagi (aman untuk re-import).
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:
        handler = logging.StreamHandler(stream or sys.stderr)
        handler.setLevel(level)
        handler.setFormatter(PrettyFormatter())
        logger.addHandler(handler)
        logger.propagate = False  # hindari duplikat dari root logger

    return logger


# ── Demo (jalankan langsung untuk test) ───────────────────────────────────────

if __name__ == "__main__":
    log = setup_logger("local_worker")

    log.info("=" * 58)
    log.info("  Local Worker – AIChatScraper (BrowserPool)")
    log.info("  VPS         : ws://192.168.1.1:9000/ws/worker")
    log.info("  Pool size   : 4 browser")
    log.info("  Headless    : True")
    log.info("  Token       : ✅ Aktif")
    log.info("=" * 58)

    log.info("Warming up 4 browser slot(s)...")
    log.info("Pool ready → BrowserPool(size=4)")
    log.info("Worker#0 🔌 Konek ke VPS: ws://192.168.1.1:9000/ws/worker")
    log.info("Worker#0 ✅ Terhubung ke VPS! (max_concurrent=4)")
    log.info("Worker#0 NEW [a1b2c3d4]")
    log.info("Worker#0 ▶ Request [a1b2c3d4]")
    log.info(
        "Worker#0 ✅ [a1b2c3d4] 1234 chars | session=deadbeef | cookie=acc1.json | url=https://chat.qwen.ai/c/xxx"
    )
    log.info("Worker#0 CONTINUE [e5f6a7b8] session=deadbeef cookie=acc1.json conv_url=https://chat.qwen.ai/c/xxx")
    log.info(
        "Pool status: total=4 idle=3 busy=1 dead=0 starting=0"
    )
    log.warning("❌ Koneksi ke VPS terputus: ConnectionClosed(1006)")
    log.info("🔄 Reconnect dalam 5 detik...")
    log.error("Worker#0 Error [a1b2c3d4]: Scraper gagal menemukan input field")
    log.critical("Gagal start BrowserPool: cookies dir tidak ditemukan")
    log.debug("Worker#0 Pong dari VPS")
    log.info("Worker dihentikan.")