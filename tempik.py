"""Klien Tempik via UI web (Camoufox) — buat inbox & ambil kode verifikasi.

Alur:
  1. Buka https://tempik.miraclelabs.my.id/
  2. Klik "Create new email" / tombol serupa -> dapat alamat
  3. Tunggu email masuk di daftar inbox (halaman yang sama)
  4. Klik email -> baca body -> ekstrak kode 6 digit
"""

from __future__ import annotations

import re
import time
from typing import Optional

from camoufox.sync_api import Camoufox
from playwright.sync_api import Page

from config import CODE_TIMEOUT, TEMPIK_BASE

# Diambil dari connector.py, atau bisa dioper lewat __init__
from pathlib import Path
CAMOUFOX_EXE = Path.home() / ".camoufox" / "camoufox-152.0.4-beta.30-win.x86_64" / "camoufox.exe"
STEP_TIMEOUT_MS = 25_000
HEADLESS = True  # default, dioverride lewat __init__


# Kode verifikasi xAI 6 digit. Hindari placeholder/template.
# Format: 6 digit (123456) atau 3-3 digit (123-456)
_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)|(?<!\d)(\d{3})-(\d{3})(?!\d)")

# Kata kunci yang biasanya mendahului kode asli
_CODE_KEYWORDS = [
    "security code",
    "verification code",
    "confirmation code",
    "one time code",
    "one-time code",
    "confirm your email",
    "verify your email",
    "your code is",
    "code is",
]

# Pola angka placeholder/template yang diabaikan
_PLACEHOLDER_PATTERNS = {
    "111111", "222222", "333333", "444444", "555555",
    "666666", "777777", "888888", "999999", "000000",
    "123456", "654321",
}


def _extract_code(text: str) -> str | None:
    low = text.lower()
    for kw in _CODE_KEYWORDS:
        idx = low.find(kw)
        if idx >= 0:
            window = text[max(0, idx - 80):idx + 120]
            for m in _CODE_RE.finditer(window):
                # Group 1: 6 digit, Group 2-3: XXX-XXX
                code = m.group(1) or (m.group(2) + m.group(3))
                if code not in _PLACEHOLDER_PATTERNS:
                    return code
    for m in _CODE_RE.finditer(text):
        code = m.group(1) or (m.group(2) + m.group(3))
        if code not in _PLACEHOLDER_PATTERNS:
            return code
    return None


class TempikClient:
    """Otomatisasi Tempik web UI lewat Camoufox."""

    def __init__(self, headless: bool = HEADLESS, proxy: Optional[dict] = None):
        self.headless = headless
        self.proxy = proxy
        self._browser_ctx = None
        self._page: Optional[Page] = None
        self._address: Optional[str] = None

    def _kwargs(self) -> dict:
        kwargs = {
            "headless": self.headless,
            "humanize": True,
            "locale": "en-US",
            "os": "windows",
            "block_webrtc": True,
        }
        if CAMOUFOX_EXE.exists():
            kwargs["executable_path"] = str(CAMOUFOX_EXE)
        if self.proxy and self.proxy.get("server"):
            kwargs["proxy"] = self.proxy
        return kwargs

    def _launch(self) -> Page:
        self._browser_ctx = Camoufox(**self._kwargs())
        browser = self._browser_ctx.__enter__()
        ctx = browser.new_context()
        self._page = ctx.new_page()
        self._page.set_default_timeout(STEP_TIMEOUT_MS)
        return self._page

    def close(self) -> None:
        if self._browser_ctx:
            try:
                self._browser_ctx.__exit__(None, None, None)
            except Exception:
                pass

    def new_address(self) -> str:
        """Buka Tempik, klik New -> Random untuk buat email acak, kembalikan alamat."""
        page = self._launch()
        page.goto(TEMPIK_BASE, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)

        # Klik tombol New
        for sel in ['button:has-text("New")', 'button:has-text("＋")']:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    btn.click()
                    page.wait_for_timeout(1500)
                    break
            except Exception:
                continue

        # Klik Random atau Create untuk generate email
        for label in ["Random", "Create", "🎲 Random", "✦ Create"]:
            try:
                btn = page.query_selector(f'button:has-text("{label}")')
                if btn and btn.is_visible():
                    btn.click()
                    page.wait_for_timeout(2000)
                    break
            except Exception:
                continue

        # Alamat muncul di dropdown atau body setelah generate
        # Coba ambil dari select, input, atau body text
        selectors = [
            'select option[selected]',
            'select option:first-child',
            'input[readonly][value*="@"]',
            'input[value*="miraclelabs.my.id"]',
            '[class*="email"]',
        ]
        for sel in selectors:
            try:
                el = page.query_selector(sel)
                if el:
                    val = el.get_attribute("value") or el.inner_text()
                    if val and "@miraclelabs.my.id" in val:
                        self._address = val.strip()
                        return self._address
            except Exception:
                continue

        # Fallback: regex di body (alamat mungkin muncul di dropdown text)
        body = page.inner_text("body")
        m = re.search(r"([a-z0-9._%+-]+@miraclelabs\.my\.id)", body, re.IGNORECASE)
        if m:
            self._address = m.group(1)
            return self._address

        raise RuntimeError("Tidak menemukan alamat email baru di Tempik")

    def wait_for_code(self, address: str, timeout: float = CODE_TIMEOUT) -> str:
        """Poll inbox di halaman Tempik sampai kode 6 digit valid ketemu."""
        if not self._page:
            raise RuntimeError("Browser belum dibuka")
        
        try:
            self._page.bring_to_front()
            self._page.wait_for_timeout(1000)
        except Exception:
            pass
        
        deadline = time.time() + timeout
        seen_ids = set()

        while time.time() < deadline:
            try:
                btn = self._page.query_selector('button:has-text("Refresh"), button:has-text("🔄")')
                if btn and btn.is_visible():
                    btn.click()
                    self._page.wait_for_timeout(1000)
            except Exception:
                pass

            items = self._page.query_selector_all('tr, li, [class*="message"], [class*="mail"]')
            
            for item in items:
                try:
                    txt = item.inner_text()
                    if not txt or len(txt) < 10:
                        continue
                    mid = hash(txt[:200])
                    if mid in seen_ids:
                        continue
                    seen_ids.add(mid)

                    if any(kw in txt.lower() for kw in ["xai", "x.ai", "grok", "verification", "security code", "confirm"]):
                        item.click()
                        self._page.wait_for_timeout(1500)
                        detail_text = self._page.inner_text("body")
                        code = _extract_code(detail_text)
                        if code:
                            return code
                        try:
                            self._page.go_back()
                            self._page.wait_for_timeout(500)
                        except Exception:
                            pass
                except Exception:
                    continue

            time.sleep(3)

        raise TimeoutError(f"Kode verifikasi tidak masuk ke {address} dalam {int(timeout)}s")