"""
QwenScraper – concrete scraper for https://chat.qwen.ai
"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import tempfile
from pathlib import Path
from typing import Literal

from config import QWEN_CONFIG, ROTATION_CONFIG
from scrapers.base_scraper import BaseAIChatScraper
from scrapers.utils import contains_any, discover_cookie_files


# ─── Attachment helper types ──────────────────────────────────────────────────

class Attachment:
    """
    Representasi satu file attachment yang akan diupload ke Qwen.

    Bisa dibuat dari:
      • Path file lokal  : Attachment.from_path("/path/to/image.png")
      • Base64 string    : Attachment.from_base64("data:image/png;base64,iVBOR...", "image.png")
                           atau Attachment.from_base64("iVBOR...", "image.png", "image/png")

    Attribute:
      name      – nama file (ditampilkan di UI Qwen)
      data      – bytes isi file
      mime_type – MIME type, misalnya "image/png"
    """

    # Tipe file yang didukung Qwen (untuk validasi ringan)
    SUPPORTED_MIME_PREFIXES = (
        "image/",          # jpg, png, webp, gif, bmp, tiff, svg, ico
        "application/pdf",
        "text/",           # txt, csv, html, xml, markdown, dll
        "application/json",
        "application/msword",
        "application/vnd.openxmlformats-officedocument",   # docx, xlsx, pptx
        "application/vnd.ms-",                              # xls, ppt
        "audio/",
        "video/",
    )

    def __init__(self, name: str, data: bytes, mime_type: str) -> None:
        self.name = name
        self.data = data
        self.mime_type = mime_type

    # ── Factory methods ───────────────────────────────────────────────────────

    @classmethod
    def from_path(cls, path: str | Path) -> "Attachment":
        """Buat Attachment dari path file lokal."""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"File tidak ditemukan: {p}")
        mime, _ = mimetypes.guess_type(str(p))
        if not mime:
            mime = "application/octet-stream"
        return cls(name=p.name, data=p.read_bytes(), mime_type=mime)

    @classmethod
    def from_base64(
        cls,
        b64_data: str,
        filename: str,
        mime_type: str | None = None,
    ) -> "Attachment":
        """
        Buat Attachment dari string base64.

        b64_data bisa berupa:
          • Data URI: "data:image/png;base64,iVBOR..."
          • Raw base64: "iVBOR..."

        mime_type opsional; jika tidak diisi akan di-guess dari filename
        atau diambil dari Data URI prefix.
        """
        if b64_data.startswith("data:"):
            # Format: "data:<mime>;base64,<data>"
            header, _, raw = b64_data.partition(",")
            if not mime_type:
                # Ambil MIME dari header "data:image/png;base64"
                mime_part = header[5:]  # buang "data:"
                mime_type = mime_part.split(";")[0]
            b64_data = raw

        # Padding fix
        b64_data = b64_data.strip()
        missing = len(b64_data) % 4
        if missing:
            b64_data += "=" * (4 - missing)

        data = base64.b64decode(b64_data)

        if not mime_type:
            guessed, _ = mimetypes.guess_type(filename)
            mime_type = guessed or "application/octet-stream"

        return cls(name=filename, data=data, mime_type=mime_type)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def is_supported(self) -> bool:
        return any(self.mime_type.startswith(p) for p in self.SUPPORTED_MIME_PREFIXES)

    def to_temp_file(self) -> Path:
        """
        Tulis data ke file sementara dan return Path-nya.
        Caller bertanggung jawab menghapus file setelah selesai.
        """
        suffix = Path(self.name).suffix or mimetypes.guess_extension(self.mime_type) or ".bin"
        fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="qwen_attach_")
        import os
        os.close(fd)
        Path(tmp_path).write_bytes(self.data)
        return Path(tmp_path)

    def __repr__(self) -> str:
        return f"<Attachment name={self.name!r} mime={self.mime_type} size={len(self.data)}B>"


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
            # Pada mode continue, think-mode trigger tidak selalu tersedia
            # (UI Qwen menyembunyikannya di dalam conversation page).
            # Tandai sudah applied agar tidak buang waktu mencari trigger
            # yang memang tidak ada, kecuali ada explicit override.
            if not self._think_mode_applied:
                self._think_mode_applied = True
                self.logger.debug(
                    "Continue mode: think-mode UI not available in conversation page – skipping"
                )

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
                        print(f"Current think mode from selector '{sel}': {text}")  # debug
                        return text
                    for label in ("thinking", "fast", "auto"):
                        if label in text:
                            print(f"Current think mode from selector '{sel}': {text}")  # debug
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
            #"[class*='thinking-label']",
            #"[class*='think-mode']",
            #"[class*='qwen-select']",
            # Generic fallbacks: buttons / divs near the textarea
            #"button[class*='think']",
            #"div[class*='think']",
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
                    #print(f"Clicked think-mode trigger: {sel}")
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
            #".ant-select-item",
            #".ant-select-item-option",
            # Generic dropdown patterns
            #"[class*='option-item']",
            #"[class*='select-option']",
            #"[class*='dropdown-item']",
            #"[role='option']",
            #"[role='menuitem']",
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
                            #print(f"Clicked option '{text}' via selector: {sel}")
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
            "textarea[placeholder]",
            "textarea#chat-input",
            "textarea[data-testid='chat-input']",
            "div[contenteditable='true'][data-testid]",
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
            self._SEL_SEND_BTN,
            "button[data-testid='send-button']",
            "button[aria-label='Send message']",
            "button[aria-label='Send']",
            "button[type='submit']",
        ]
        for sel in candidates:
            try:
                el = await self._page.query_selector(sel)
                if el:
                    return el
            except Exception:
                continue
        return None

    async def _find_send_button_enabled(self, max_wait: float = 5.0):
        """
        Cari send button yang tidak disabled.
        Tunggu hingga max_wait detik jika tombol ditemukan tapi masih disabled
        (misalnya setelah input baru diisi, Qwen perlu sebentar untuk enable-nya).
        """
        candidates = [
            self._SEL_SEND_BTN,
            "button[data-testid='send-button']",
            "button[aria-label='Send message']",
            "button[aria-label='Send']",
            "button[type='submit']",
        ]
        deadline = asyncio.get_event_loop().time() + max_wait
        while asyncio.get_event_loop().time() < deadline:
            for sel in candidates:
                try:
                    el = await self._page.query_selector(sel)
                    if el:
                        disabled = await el.get_attribute("disabled")
                        aria_disabled = await el.get_attribute("aria-disabled")
                        if disabled is None and aria_disabled != "true":
                            return el
                except Exception:
                    continue
            await asyncio.sleep(0.2)
        # Kembalikan tombol apa pun yang ditemukan (meski disabled) atau None
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

    # ── Attachment upload ─────────────────────────────────────────────────────

    # ── Attachment upload via Clipboard ───────────────────────────────────────
    #
    # Kenapa clipboard?
    # Qwen menggunakan custom upload handler (bukan <input type="file"> standar).
    # File chooser dan set_input_files() gagal karena Playwright tidak bisa
    # intercept event handler milik Qwen. Clipboard paste adalah cara yang
    # paling reliable: kita inject ClipboardItem berisi file data langsung
    # ke clipboard browser via CDP (Chrome DevTools Protocol), lalu simulasikan
    # Ctrl+V ke textarea — persis seperti user melakukan copy-paste file.
    #
    # Urutan untuk SETIAP file:
    #   1. Encode file → base64
    #   2. Inject ke clipboard browser via CDP Page.addScriptToEvaluateOnNewDocument
    #      + Runtime.evaluate (setClipboardContents tidak perlu permission user)
    #   3. Fokus ke textarea
    #   4. Dispatch ClipboardEvent 'paste' dengan DataTransfer berisi file
    #   5. Tunggu preview/thumbnail muncul → konfirmasi berhasil
    #
    # Untuk non-image (PDF, txt, dll): gunakan DataTransfer dengan file blob,
    # bukan ImageBitmap — Qwen membaca tipe file dari DataTransfer.files[0].type.

    async def _upload_attachments(self, attachments: list[Attachment]) -> bool:
        """
        Upload semua attachment ke Qwen via clipboard paste (CDP).

        Mengembalikan True jika semua attachment berhasil di-paste,
        False jika ada yang gagal (scrape tetap dilanjutkan).
        """
        if not attachments:
            return True

        # Dapatkan CDP session untuk operasi clipboard
        cdp = await self._page.context.new_cdp_session(self._page)
        all_ok = True

        for att in attachments:
            if not att.is_supported():
                self.logger.warning(
                    "Attachment '%s' (MIME: %s) mungkin tidak didukung Qwen — tetap dicoba",
                    att.name, att.mime_type,
                )
            ok = await self._paste_attachment_via_cdp(cdp, att)
            if ok:
                # Jeda antar file agar Qwen sempat memproses upload sebelumnya
                await asyncio.sleep(1.2)
            else:
                all_ok = False
                self.logger.warning("Gagal paste attachment '%s'", att.name)

        await cdp.detach()
        return all_ok

    async def _paste_attachment_via_cdp(self, cdp, att: Attachment) -> bool:
        """
        Paste satu Attachment ke input Qwen menggunakan CDP Runtime.evaluate.

        Strategi:
        1. Encode data file ke base64.
        2. Inject JS ke halaman yang:
           a. Membuat Blob dari base64 data.
           b. Membuat File object dengan nama dan MIME type yang benar.
           c. Membuat DataTransfer dan masukkan File ke dalamnya.
           d. Dispatch ClipboardEvent 'paste' ke textarea/input yang aktif.
        3. Tunggu preview attachment muncul sebagai konfirmasi.
        """
        try:
            b64_data = base64.b64encode(att.data).decode("ascii")
            mime     = att.mime_type
            name     = att.name.replace("'", "\\'")   # escape untuk JS string

            self.logger.debug(
                "CDP paste: '%s' (%s, %d bytes)", att.name, mime, len(att.data),
            )

            # Fokus ke input area dulu agar paste event diterima
            await self._page.evaluate("""
            () => {
                const candidates = [
                    'textarea[placeholder]',
                    'textarea',
                    'div[contenteditable="true"]',
                ];
                for (const sel of candidates) {
                    const el = document.querySelector(sel);
                    if (el) { el.focus(); return; }
                }
            }
            """)
            await asyncio.sleep(0.2)

            # Inject file via DataTransfer + paste event
            result = await self._page.evaluate(f"""
            async () => {{
                try {{
                    // 1. Decode base64 → Uint8Array
                    const b64 = '{b64_data}';
                    const bin = atob(b64);
                    const arr = new Uint8Array(bin.length);
                    for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);

                    // 2. Buat File object
                    const blob = new Blob([arr], {{ type: '{mime}' }});
                    const file = new File([blob], '{name}', {{ type: '{mime}' }});

                    // 3. Coba tulis ke clipboard API (butuh permission, mungkin gagal)
                    try {{
                        const item = new ClipboardItem({{ '{mime}': blob }});
                        await navigator.clipboard.write([item]);
                    }} catch (_) {{
                        // Clipboard API tidak tersedia / blocked — lanjut dengan paste event
                    }}

                    // 4. Buat DataTransfer dengan file
                    const dt = new DataTransfer();
                    dt.items.add(file);

                    // 5. Cari target element (textarea atau contenteditable)
                    const selectors = [
                        'textarea[placeholder]',
                        'textarea',
                        'div[contenteditable="true"]',
                        '.chat-input',
                        '[class*="input-area"]',
                    ];
                    let target = null;
                    for (const sel of selectors) {{
                        const el = document.querySelector(sel);
                        if (el) {{ target = el; break; }}
                    }}
                    if (!target) return {{ ok: false, reason: 'input not found' }};

                    target.focus();

                    // 6. Dispatch paste event SAJA dulu.
                    //    Drop event hanya akan dicoba dari Python jika paste
                    //    tidak menghasilkan preview — dispatch keduanya sekaligus
                    //    menyebabkan file terdaftar 2x di Qwen.
                    const pasteEvent = new ClipboardEvent('paste', {{
                        bubbles: true,
                        cancelable: true,
                        clipboardData: dt,
                    }});
                    const accepted = target.dispatchEvent(pasteEvent);

                    return {{ ok: true, fileName: '{name}', mime: '{mime}', accepted }};

                }} catch (e) {{
                    return {{ ok: false, reason: e.toString() }};
                }}
            }}
            """)

            if not result or not result.get("ok"):
                reason = result.get("reason", "unknown") if result else "null result"
                self.logger.debug("CDP paste JS error untuk '%s': %s", att.name, reason)
                return False

            self.logger.info(
                "✅ Clipboard paste berhasil: '%s' (%s)", att.name, mime,
            )

            # Cek preview dulu dari paste event
            preview_ok = await self._wait_attachment_preview(timeout=5.0)

            # Jika paste tidak menghasilkan preview, coba drop event sebagai fallback
            if not preview_ok:
                self.logger.debug(
                    "Paste event tidak menghasilkan preview untuk '%s' — coba drop event",
                    att.name,
                )
                dropped = await self._page.evaluate(f"""
                async () => {{
                    try {{
                        const b64 = '{b64_data}';
                        const bin = atob(b64);
                        const arr = new Uint8Array(bin.length);
                        for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
                        const blob = new Blob([arr], {{ type: '{mime}' }});
                        const file = new File([blob], '{name}', {{ type: '{mime}' }});
                        const dt = new DataTransfer();
                        dt.items.add(file);
                        const selectors = [
                            'textarea[placeholder]', 'textarea',
                            'div[contenteditable="true"]', '.chat-input',
                            '[class*="input-area"]',
                        ];
                        let target = null;
                        for (const sel of selectors) {{
                            const el = document.querySelector(sel);
                            if (el) {{ target = el; break; }}
                        }}
                        if (!target) return false;
                        target.focus();
                        const dropEvent = new DragEvent('drop', {{
                            bubbles: true, cancelable: true, dataTransfer: dt,
                        }});
                        target.dispatchEvent(dropEvent);
                        return true;
                    }} catch (e) {{ return false; }}
                }}
                """)
                if dropped:
                    preview_ok = await self._wait_attachment_preview(timeout=5.0)

            if not preview_ok:
                self.logger.warning(
                    "Preview '%s' tidak terdeteksi — file mungkin tidak diterima Qwen",
                    att.name,
                )
            return preview_ok

        except Exception as e:
            self.logger.error(
                "CDP paste exception untuk '%s': %s", att.name, e, exc_info=True,
            )
            return False

    async def _wait_attachment_preview(self, timeout: float = 10.0) -> bool:
        """
        Tunggu sampai elemen preview/thumbnail attachment muncul di UI Qwen.
        Return True jika preview terdeteksi, False jika timeout.

        Qwen menampilkan preview berupa:
        - Thumbnail gambar (img) di area input
        - Badge/chip dengan nama file
        - Indikator upload (spinner yang kemudian berubah jadi preview)
        """
        _PREVIEW_SELECTORS = [
            # Selector spesifik Qwen (update jika class berubah)
            "[class*='attachment']",
            "[class*='file-preview']",
            "[class*='upload-preview']",
            "[class*='file-card']",
            "[class*='file-chip']",
            "[class*='attached']",
            # Gambar thumbnail di area input
            ".chat-input-area img:not([class*='avatar'])",
            ".input-area img:not([class*='avatar'])",
            # Generic fallback
            "[class*='preview-item']",
            "[class*='upload-item']",
        ]

        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            for sel in _PREVIEW_SELECTORS:
                try:
                    els = await self._page.query_selector_all(sel)
                    for el in els:
                        if await el.is_visible():
                            self.logger.debug(
                                "Attachment preview terdeteksi via '%s'", sel,
                            )
                            return True
                except Exception:
                    continue
            await asyncio.sleep(0.3)

        self.logger.debug("Attachment preview timeout setelah %.0fs", timeout)
        return False

    # ── Core send_prompt ──────────────────────────────────────────────────────

    async def send_prompt(
        self,
        prompt: str,
        mode: str = "new",
        think_mode: ThinkMode | None = None,
        attachments: list[Attachment] | None = None,
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

        # ── Upload attachments SEBELUM mengisi prompt ─────────────────────────
        # Urutan ini penting: Qwen membutuhkan file terupload lebih dulu
        # sebelum prompt diketik agar keduanya terkirim dalam satu request.
        if attachments:
            self.logger.info(
                "Uploading %d attachment(s): %s",
                len(attachments), [a.name for a in attachments],
            )
            await self._upload_attachments(attachments)
            # Jeda singkat agar Qwen memproses upload sebelum prompt diketik
            await asyncio.sleep(0.5)

        input_el = await self._find_input()

        # Snapshot jumlah response SEBELUM mengisi prompt dan submit,
        # supaya tidak ada race condition antara fill() dan pre_count.
        pre_count = await self._count_response_elements()

        self.logger.info(
            "Submitting prompt (%d chars) [think_mode=%s, pre_count=%d, attachments=%d]",
            len(prompt), effective_think, pre_count, len(attachments) if attachments else 0,
        )

        await input_el.click()
        #await input_el.fill()
        await input_el.type("~", delay=1)  # focus and clear existing content
        await input_el.fill(prompt, timeout=1_000)
        await asyncio.sleep(0.3)

        # Pastikan send button enabled sebelum di-click
        send_btn = await self._find_send_button_enabled()
        if send_btn:
            await send_btn.click()
            self.logger.debug("Send button clicked")
        else:
            # Fallback: Enter key
            await input_el.press("Enter")
            self.logger.debug("Used Enter key to submit (no enabled send button found)")

        self._conversation_started = True
        return await self._wait_for_generation(pre_count)

    async def _count_response_elements(self) -> int:
        """Hitung jumlah elemen response saat ini di DOM."""
        return await self._page.evaluate("""
        () => document.querySelectorAll(
            '.chat-message-container .chat-response-message, .qwen-markdown-loose, .qwen-markdown'
        ).length
        """)

    async def _wait_for_generation(self, pre_count: int = 0) -> str:
        timeout_s = _TIMEOUT["response_wait"] // 1000
        stability_interval = _TIMEOUT["stability_check"] / 1000
        stability_needed = 2

        deadline = asyncio.get_event_loop().time() + timeout_s
        prev_text = ""
        stable_count = 0
        appeared = False

        # Fase 1: tunggu sinyal bahwa generasi sudah dimulai.
        # Dua kondisi yang dianggap "dimulai":
        #   (a) is_generating() → True  (stop-button / streaming indicator muncul)
        #   (b) cur_count > pre_count   (elemen response baru muncul di DOM)
        # Jika dalam 10 detik tidak ada sinyal, log warning dan lanjut saja
        # agar tidak stuck selamanya hanya di fase deteksi awal.
        signal_deadline = asyncio.get_event_loop().time() + 10.0
        while asyncio.get_event_loop().time() < signal_deadline:
            generating = await self._is_generating()
            cur_count = await self._count_response_elements()
            if generating or cur_count > pre_count:
                appeared = True
                self.logger.debug(
                    "Generation signal detected: generating=%s, count %d→%d",
                    generating, pre_count, cur_count,
                )
                break
            await asyncio.sleep(0.4)

        if not appeared:
            self.logger.warning(
                "No generation signal after 10s (pre_count=%d) – proceeding to wait anyway",
                pre_count,
            )

        # Fase 2: tunggu konten stabil
        while asyncio.get_event_loop().time() < deadline:
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