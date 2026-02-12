from __future__ import annotations

import time
from typing import Any, Dict, Optional

import requests


def request_json(
    method: str,
    url: str,
    timeout_s: int = 30,
    retries: int = 2,
    backoff_s: float = 0.8,
    **kwargs: Any,
) -> Dict[str, Any]:
    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            response = requests.request(method, url, timeout=timeout_s, **kwargs)
            response.raise_for_status()
            if response.text.strip():
                return response.json()
            return {}
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt >= retries:
                break
            time.sleep(backoff_s * (attempt + 1))
    raise RuntimeError(f"HTTP request failed: {method} {url}") from last_exc

