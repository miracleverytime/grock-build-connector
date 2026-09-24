"""Grok Build Connector - config loader (config.toml)."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.toml"


def _load() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    return {}


_CONFIG = _load()


def _cfg(*keys: str, default: Any = None) -> Any:
    val = _CONFIG
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k)
        else:
            return default
    return val if val is not None else default


PROXY_MODE = _cfg("proxy", "mode", default="none")  # "none" | "file"
PROXY_POOL_FILE = _cfg("proxy", "pool_file", default="proxies.txt")
if not Path(PROXY_POOL_FILE).is_absolute():
    PROXY_POOL_FILE = BASE_DIR / PROXY_POOL_FILE

HEADLESS = bool(_cfg("signup", "headless", default=True))
SIGNUP_DELAY = int(_cfg("signup", "delay", default=3))
SIGNUP_RETRY = int(_cfg("signup", "retry", default=1))

NINEROUTER_BASE = _cfg("9router", "base", default="http://localhost:20128")
PURGE_ERRORS = bool(_cfg("9router", "purge_errors", default=True))
PURGE_ERROR_PROVIDER = _cfg("9router", "purge_error_provider", default="grok-cli")

POLL_INTERVAL = int(_cfg("grok", "poll_interval", default=5))
POLL_TIMEOUT = int(_cfg("grok", "poll_timeout", default=180))
SIGNUP_COUNT = int(_cfg("grok", "count", default=1))

TEMPIK_BASE = _cfg("tempik", "base", default="https://tempik.miraclelabs.my.id")
CODE_TIMEOUT = int(_cfg("tempik", "code_timeout", default=120))
