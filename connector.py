"""
Grok Build Connector - buat akun xAI baru lalu sambungkan ke 9router (provider grok-cli).

Bukan login akun yang sudah ada. Tiap akun didaftarkan dari nol:

  1. GET /api/oauth/grok-cli/device-code        -> user_code + verification_uri
  2. Tempik membuat email sekali pakai.
  3. Browser (Camoufox) di accounts.x.ai:
       isi device code -> Continue -> Sign up -> Sign up with email
       -> isi email -> ambil kode 6 digit dari inbox Tempik -> Confirm email
       -> isi nama + password acak -> Complete sign up
       -> Continue pada kode kedua -> Allow.
  4. POST /api/oauth/grok-cli/poll sampai 9router menyimpan koneksi.

Token tidak lewat skrip ini. 9router yang menukar device code dan menyimpannya.

Run:
  python connector.py --headed        # satu akun (config count), browser tampil
  python connector.py 5               # lima akun
  python connector.py --headed 1      # satu akun, browser tampil
"""

from __future__ import annotations

import hashlib
import json
import random
import string
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import httpx
from camoufox.sync_api import Camoufox
from playwright.sync_api import Page

from config import (
    HEADLESS,
    NINEROUTER_BASE,
    POLL_INTERVAL,
    POLL_TIMEOUT,
    PROXY_MODE,
    PROXY_POOL_FILE,
    PURGE_ERROR_PROVIDER,
    PURGE_ERRORS,
    SIGNUP_COUNT,
    SIGNUP_DELAY,
    SIGNUP_RETRY,
)
from proxy import ProxyPool
from tempik import TempikClient

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOG_FILE = DATA_DIR / "connector.log"
RESULTS_FILE = DATA_DIR / "results.jsonl"
DONE_FILE = DATA_DIR / "done.txt"
ACCOUNTS_OUT = DATA_DIR / "accounts.jsonl"

PROVIDER = "grok-cli"

DEFAULT_DATA_DIR = Path(__import__("os").environ.get("APPDATA", "")) / "9router"

NAV_TIMEOUT_MS = 60_000
STEP_TIMEOUT_MS = 25_000

CAMOUFOX_EXE = Path.home() / ".camoufox" / "camoufox-152.0.4-beta.30-win.x86_64" / "camoufox.exe"

DEVICE_URL = "https://accounts.x.ai/oauth2/device"

FIRST_NAMES = ["Budi", "Sari", "Andi", "Dewi", "Rudi", "Maya", "Agus", "Rina", "Dian", "Hendra"]
LAST_NAMES = ["Santoso", "Wijaya", "Kusuma", "Pratama", "Saputra", "Lestari", "Hidayat", "Putra"]


# ==================== LOGGING / HASIL ====================
def log(msg: str, level: str = "INFO") -> None:
    ts = datetime.now(timezone.utc).isoformat()
    line = f"[{ts}] [{level}] {msg}"
    print(line, flush=True)
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def derive_cli_token(data_dir: Path = None) -> Optional[str]:
    data_dir = data_dir or DEFAULT_DATA_DIR
    mid_file = data_dir / "machine-id"
    secret_file = data_dir / "auth" / "cli-secret"
    if not mid_file.exists() or not secret_file.exists():
        return None
    raw = mid_file.read_text(encoding="utf-8").strip()
    secret = secret_file.read_text(encoding="utf-8").strip()
    if not raw or not secret:
        return None
    digest = hashlib.sha256((raw + "9r-cli-auth" + secret).encode("utf-8")).hexdigest()
    return digest[:16]


def load_done(path: Path) -> set:
    if not path.exists():
        return set()
    return {l.strip().lower() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()}


def mark_done(path: Path, email: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(email.lower() + "\n")


def unmark_done(path: Path, email: str) -> None:
    if not path.exists():
        return
    lines = [l for l in path.read_text(encoding="utf-8").splitlines()
             if l.strip().lower() != email.lower()]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def save_result(email: str, status: str, detail: str = "", extra: dict = None) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    record = {"email": email, "status": status, "detail": detail,
              "ts": datetime.now(timezone.utc).isoformat()}
    if extra:
        record.update(extra)
    with open(RESULTS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def save_account(email: str, password: str, first: str, last: str) -> None:
    """Catat kredensial akun yang berhasil didaftarkan, supaya tidak hilang."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    record = {"email": email, "password": password, "first": first, "last": last,
              "ts": datetime.now(timezone.utc).isoformat()}
    with open(ACCOUNTS_OUT, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def random_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits
    chars = [random.choice(string.ascii_lowercase), random.choice(string.ascii_uppercase),
             random.choice(string.digits)]
    chars += [random.choice(alphabet) for _ in range(length - 3)]
    random.shuffle(chars)
    return "".join(chars)


# ==================== 9ROUTER ====================
class NineRouterClient:
    def __init__(self, base: str = NINEROUTER_BASE, cli_token: str = None):
        self.base = base.rstrip("/")
        self.cli_token = cli_token
        self.client = httpx.Client(timeout=90, headers=self._headers())

    def _headers(self) -> Dict[str, str]:
        return {"x-9r-cli-token": self.cli_token} if self.cli_token else {}

    def device_code(self) -> Dict:
        r = self.client.get(f"{self.base}/api/oauth/{PROVIDER}/device-code")
        r.raise_for_status()
        data = r.json()
        if "device_code" not in data or "user_code" not in data:
            raise RuntimeError(f"device-code tidak lengkap: {list(data)}")
        return data

    def poll(self, device_code: str, code_verifier: str) -> Dict:
        r = self.client.post(f"{self.base}/api/oauth/{PROVIDER}/poll",
                             json={"deviceCode": device_code, "codeVerifier": code_verifier})
        if r.status_code >= 400:
            try:
                err = r.json().get("error", r.text)
            except Exception:
                err = r.text
            raise RuntimeError(f"poll failed ({r.status_code}): {err}")
        return r.json()

    def poll_until_done(self, device_code: str, code_verifier: str,
                        interval: float, timeout: float) -> Dict:
        deadline = time.time() + timeout
        wait = max(interval, 1)
        while True:
            data = self.poll(device_code, code_verifier)
            if data.get("success"):
                return data
            error = (data.get("error") or "").lower()
            if error in {"expired_token", "access_denied"}:
                raise RuntimeError(data.get("errorDescription") or error)
            if not data.get("pending") and error not in {"authorization_pending", "slow_down", ""}:
                raise RuntimeError(data.get("errorDescription") or error or "poll rejected")
            if error == "slow_down":
                wait += 2
            if time.time() >= deadline:
                raise TimeoutError(f"authorize tidak selesai dalam {int(timeout)}s")
            time.sleep(wait)

    def list_connections(self) -> List[Dict]:
        r = self.client.get(f"{self.base}/api/providers")
        r.raise_for_status()
        return r.json().get("connections", [])

    def get_connection(self, conn_id: str) -> Optional[Dict]:
        r = self.client.get(f"{self.base}/api/providers/{conn_id}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json().get("connection")

    def delete_connection(self, conn_id: str) -> bool:
        r = self.client.delete(f"{self.base}/api/providers/{conn_id}")
        if r.status_code == 404:
            return False
        if r.status_code >= 400:
            try:
                err = r.json().get("error", r.text)
            except Exception:
                err = r.text
            raise RuntimeError(f"delete failed ({r.status_code}): {err}")
        return True

    def purge_error_connections(self, provider: str, on_removed=None) -> int:
        try:
            conns = self.list_connections()
        except Exception as e:
            log(f"  [purge] cannot list connections: {e}", "ERROR")
            return 0

        removed = 0
        for c in conns:
            if (c.get("provider") or "").lower() != provider.lower():
                continue
            cid = c.get("id")
            email = c.get("email") or c.get("name") or cid
            if not cid:
                continue
            last_error = c.get("lastError")
            test_status = c.get("testStatus")
            if not last_error:
                data = c.get("data")
                if isinstance(data, str):
                    try:
                        data = json.loads(data)
                    except Exception:
                        data = {}
                if isinstance(data, dict):
                    last_error = data.get("lastError") or last_error
            if not last_error:
                try:
                    detail = self.get_connection(cid) or c
                except Exception as e:
                    log(f"  [purge] cannot fetch {cid}: {e}", "WARN")
                    detail = c
                last_error = detail.get("lastError")
            if not (bool(last_error) or test_status == "error"):
                continue
            log(f"  [purge] removing errored {provider}: {email} "
                f"({str(last_error or test_status)[:80]})")
            try:
                ok = self.delete_connection(cid)
            except Exception as e:
                log(f"  [purge] delete {email} failed: {e}", "ERROR")
                continue
            if ok:
                removed += 1
                if on_removed:
                    try:
                        on_removed(c)
                    except Exception:
                        pass
        log(f"  [purge] removed {removed} errored {provider} connection(s)" if removed
            else f"  [purge] no errored {provider} connections")
        return removed


# ==================== BROWSER ====================
class XaiSignup:
    """Daftarkan akun xAI lewat halaman device code, sampai klik Allow."""

    def __init__(self, headless: bool = HEADLESS, proxy: Optional[Dict[str, str]] = None):
        self.headless = headless
        self.proxy = proxy
        self._tag = "signup"

    def _kwargs(self) -> Dict:
        kwargs: Dict = {
            "headless": self.headless,
            "humanize": True,
            "locale": "en-US",
            "os": "windows",
            "block_webrtc": True,
            "firefox_user_prefs": {
                "signon.rememberSignons": False,
                "signon.autofillForms": False,
                "browser.formfill.enable": False,
            },
        }
        if CAMOUFOX_EXE.exists():
            kwargs["executable_path"] = str(CAMOUFOX_EXE)
        if self.proxy and self.proxy.get("server"):
            kwargs["proxy"] = self.proxy
        return kwargs

    def _shot(self, page: Page, name: str) -> None:
        try:
            d = DATA_DIR / "shots"
            d.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(d / f"{self._tag}_{name}.png"))
        except Exception as e:
            log(f"    [dbg] screenshot failed: {e}", "WARN")

    def _dismiss_cookies(self, page: Page) -> None:
        for label in ("Reject All", "Reject all", "Tolak Semua"):
            try:
                el = page.query_selector(f'button:has-text("{label}")')
                if el and el.is_visible():
                    el.click(timeout=3000)
                    page.wait_for_timeout(400)
                    return
            except Exception:
                continue

    def _click(self, page: Page, labels: List[str], timeout: int = 6000) -> str:
        deadline = time.time() + STEP_TIMEOUT_MS / 1000
        while time.time() < deadline:
            for label in labels:
                try:
                    el = page.query_selector(f'button:has-text("{label}")')
                    if el and el.is_visible():
                        el.click(timeout=timeout)
                        return label
                except Exception:
                    continue
            page.wait_for_timeout(400)
        raise RuntimeError(f"tombol tidak ketemu: {labels}")

    def _fill(self, page: Page, selector: str, value: str) -> None:
        el = page.wait_for_selector(selector, state="visible", timeout=STEP_TIMEOUT_MS)
        el.click()
        el.fill(value)

    def register(self, user_code: str, verify_url: str, tempik: TempikClient,
                 email: str, password: str, first: str, last: str) -> None:
        self._tag = email.split("@")[0]
        log(f"    [browser] {email} code={user_code}")

        # Pakai browser yang sama dengan Tempik (sudah dibuka di tempik.new_address)
        # supaya tidak bentrok dengan event loop
        if tempik._page and tempik._page.context:
            page = tempik._page.context.new_page()
        else:
            # Fallback: buka browser baru (kalau Tempik belum buka atau sudah tutup)
            with Camoufox(**self._kwargs()) as browser:
                page = browser.new_context().new_page()

        page.set_default_timeout(STEP_TIMEOUT_MS)
        page.set_default_navigation_timeout(NAV_TIMEOUT_MS)
        try:
            self._open_and_code(page, verify_url, user_code)
            self._signup_email(page, email)
            self._confirm_email(page, tempik, email)
            self._complete_profile(page, first, last, password)
            self._allow(page)
            self._shot(page, "done")
        except Exception:
            self._shot(page, "error")
            raise
        finally:
            # Tutup tab xAI, tapi jangan tutup browser (Tempik masih pakai)
            if tempik._page and tempik._page.context:
                try:
                    page.close()
                except Exception:
                    pass

    def _open_and_code(self, page: Page, verify_url: str, user_code: str) -> None:
        page.goto(verify_url or DEVICE_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        page.wait_for_timeout(1500)
        self._dismiss_cookies(page)
        self._fill(page, 'input[inputmode="numeric"]', user_code.replace("-", ""))
        page.wait_for_timeout(300)
        self._click(page, ["Continue", "Lanjutkan"])
        log("    [1] device code diisi")

    def _signup_email(self, page: Page, email: str) -> None:
        link = page.wait_for_selector('a:has-text("Sign up"), button:has-text("Sign up")',
                                      state="visible", timeout=STEP_TIMEOUT_MS)
        link.click()
        page.wait_for_timeout(800)
        self._click(page, ["Sign up with email"])
        self._fill(page, 'input[name="email"]', email)
        page.wait_for_timeout(300)
        self._click(page, ["Sign up"])
        log("    [2] email didaftarkan, menunggu kode")

    def _confirm_email(self, page: Page, tempik: TempikClient, email: str) -> None:
        page.wait_for_selector('input[name="code"]', state="visible", timeout=STEP_TIMEOUT_MS)
        code = tempik.wait_for_code(email)
        log(f"    [3] kode verifikasi masuk ({code})")
        self._fill(page, 'input[name="code"]', code)
        page.wait_for_timeout(300)
        # xAI auto-submit setelah 6 digit, tidak perlu klik Confirm
        page.wait_for_timeout(2000)

    def _complete_profile(self, page: Page, first: str, last: str, password: str) -> None:
        # Form nama + password. Selector persisnya belum kelihatan tanpa kode email
        # sungguhan, jadi dicocokkan dari name dan placeholder yang umum.
        first_box = page.wait_for_selector(
            'input[name="firstName"], input[name="first_name"], input[autocomplete="given-name"], '
            'input[placeholder*="First" i]', state="visible", timeout=STEP_TIMEOUT_MS)
        first_box.fill(first)
        last_box = page.query_selector(
            'input[name="lastName"], input[name="last_name"], input[autocomplete="family-name"], '
            'input[placeholder*="Last" i]')
        if last_box and last_box.is_visible():
            last_box.fill(last)
        self._fill(page, 'input[type="password"]', password)
        page.wait_for_timeout(300)
        self._click(page, ["Complete sign up", "Complete signup", "Sign up", "Create account"])
        log("    [4] profil dilengkapi")

    def _allow(self, page: Page) -> None:
        # Setelah profil, xAI menampilkan device code sekali lagi lalu tombol Allow.
        deadline = time.time() + 40
        continued = False
        while time.time() < deadline:
            code_box = page.query_selector('input[inputmode="numeric"]')
            if code_box and code_box.is_visible() and not continued:
                try:
                    self._click(page, ["Continue", "Lanjutkan"])
                    continued = True
                    log("    [5] kode kedua dikonfirmasi")
                    page.wait_for_timeout(1500)
                    continue
                except Exception:
                    pass
            allow = page.query_selector('button:has-text("Allow"), button:has-text("Izinkan")')
            if allow and allow.is_visible():
                allow.click(timeout=6000)
                log("    [6] Allow diklik")
                page.wait_for_timeout(2000)
                return
            page.wait_for_timeout(700)
        raise RuntimeError("halaman Allow tidak muncul")


# ==================== FLOW ====================
def create_one(nr: NineRouterClient, tempik: TempikClient, idx: int, total: int,
               proxy: Optional[Dict[str, str]] = None) -> bool:
    # Email sudah dibuat di main() lewat tempik.new_address()
    email = tempik._address
    if not email:
        log("  [fail] email belum dibuat", "ERROR")
        return False
    password = random_password()
    first = random.choice(FIRST_NAMES)
    last = random.choice(LAST_NAMES)
    log(f"[{idx}/{total}] {email}")

    if email.lower() in load_done(DONE_FILE):
        log("  [skip] alamat ini sudah terhubung")
        return False

    try:
        dc = nr.device_code()
    except Exception as e:
        log(f"  [fail] device-code: {e}", "ERROR")
        save_result(email, "failed", f"device-code: {e}")
        return False

    user_code = dc["user_code"]
    verify_url = dc.get("verification_uri_complete") or dc.get("verification_uri") or DEVICE_URL
    interval = max(POLL_INTERVAL, int(dc.get("interval") or 5))

    try:
        XaiSignup(headless=HEADLESS, proxy=proxy).register(
            user_code, verify_url, tempik, email, password, first, last)
    except Exception as e:
        log(f"  [fail] browser: {e}", "ERROR")
        save_result(email, "failed", f"browser: {e}")
        return False

    save_account(email, password, first, last)

    try:
        result = nr.poll_until_done(dc["device_code"], dc["codeVerifier"], interval, POLL_TIMEOUT)
    except Exception as e:
        log(f"  [fail] poll: {e}", "ERROR")
        save_result(email, "failed", f"poll: {e}")
        return False

    conn = result.get("connection") or {}
    log(f"  [ok] terhubung -> {conn.get('email') or conn.get('displayName') or email}")
    save_result(email, "connected", "", {"connection": conn})
    mark_done(DONE_FILE, email)
    return True


def main() -> None:
    args = sys.argv[1:]
    global HEADLESS
    HEADLESS = "--headed" not in args

    purge = PURGE_ERRORS and "--no-purge-errors" not in args
    if "--purge-errors" in args:
        purge = True
    numeric = [a for a in args if a.lstrip("-").isdigit()]
    count = int(numeric[0]) if numeric else SIGNUP_COUNT

    log(f"Grok Build Connector (provider={PROVIDER}, headless={HEADLESS}, "
        f"count={count}, proxy={PROXY_MODE}, purge_errors={purge})")

    cli_token = derive_cli_token()
    if not cli_token:
        log("Token CLI 9router tidak ditemukan di %APPDATA%\\9router.", "ERROR")
        sys.exit(1)
    log(f"token CLI 9router siap ({cli_token[:4]}...)")

    nr = NineRouterClient(cli_token=cli_token)
    try:
        conns = nr.list_connections()
        log(f"9router terjangkau ({len(conns)} koneksi)")
    except Exception as e:
        log(f"9router tidak terjangkau di {NINEROUTER_BASE}: {e}", "ERROR")
        sys.exit(1)

    if purge:
        def _on_removed(conn: Dict) -> None:
            em = (conn.get("email") or conn.get("name") or "").strip().lower()
            if em:
                unmark_done(DONE_FILE, em)
        try:
            nr.purge_error_connections(PURGE_ERROR_PROVIDER, on_removed=_on_removed)
        except Exception as e:
            log(f"  [purge] dibatalkan: {e}", "ERROR")

    proxy_pool: Optional[ProxyPool] = None
    if PROXY_MODE == "file":
        proxy_pool = ProxyPool(pool_path=str(PROXY_POOL_FILE))
        log(f"proxy pool: {proxy_pool.count}")
    else:
        log("proxy: tidak dipakai")

    ok = fail = 0
    for i in range(1, count + 1):
        proxy = proxy_pool.next() if proxy_pool else None
        if proxy:
            log(f"  [proxy] {proxy.get('server')}")

        # TempikClient versi browser: butuh headless & proxy, dan close() tiap akun
        tempik = TempikClient(headless=HEADLESS, proxy=proxy)
        try:
            probe = tempik.new_address()
            log(f"tempik siap, email {probe}")
        except Exception as e:
            log(f"tempik gagal buat email: {e}", "ERROR")
            tempik.close()
            fail += 1
            if i < count:
                wait = SIGNUP_DELAY + random.randint(0, 5)
                log(f"  [wait] {wait}s")
                time.sleep(wait)
            continue

        if create_one(nr, tempik, i, count, proxy):
            ok += 1
        else:
            fail += 1
        tempik.close()

        if i < count:
            wait = SIGNUP_DELAY + random.randint(0, 5)
            log(f"  [wait] {wait}s")
            time.sleep(wait)

    log(f"selesai. terhubung={ok} gagal={fail}")
    log(f"hasil: {RESULTS_FILE}")
    log(f"akun: {ACCOUNTS_OUT}")


if __name__ == "__main__":
    main()
