"""Rotating proxy pool. Format: ip:port:user:pass atau http://user:pass@host:port."""

from __future__ import annotations

import os
import random
from typing import Dict, List, Optional
from urllib.parse import urlparse


class ProxyPool:
    """Round-robin / random proxy pool."""

    def __init__(self, proxies: Optional[List[str]] = None, pool_path: Optional[str] = None):
        self._proxies: List[str] = []
        self._idx = 0
        if proxies:
            self._proxies = proxies
        elif pool_path and os.path.exists(pool_path):
            self.load(pool_path)

    def load(self, path: str) -> None:
        with open(path, "r", encoding="utf-8") as f:
            self._proxies = [
                line.strip()
                for line in f
                if line.strip() and not line.startswith("#")
            ]

    @property
    def count(self) -> int:
        return len(self._proxies)

    def next(self) -> Optional[Dict[str, str]]:
        if not self._proxies:
            return None
        raw = self._proxies[self._idx % len(self._proxies)]
        self._idx += 1
        return self._parse(raw)

    def random(self) -> Optional[Dict[str, str]]:
        if not self._proxies:
            return None
        return self._parse(random.choice(self._proxies))

    @staticmethod
    def _parse(raw: str) -> Dict[str, str]:
        if raw.startswith("http"):
            u = urlparse(raw)
            return {
                "server": f"{u.scheme}://{u.hostname}:{u.port}",
                "username": u.username or "",
                "password": u.password or "",
            }
        parts = raw.split(":")
        if len(parts) == 4:
            return {
                "server": f"http://{parts[0]}:{parts[1]}",
                "username": parts[2],
                "password": parts[3],
            }
        if len(parts) == 2:
            return {"server": f"http://{parts[0]}:{parts[1]}"}
        raise ValueError(f"Invalid proxy format: {raw[:30]}...")
