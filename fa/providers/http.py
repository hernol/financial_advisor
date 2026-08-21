"""Thin HTTP helper shared by the REST based providers."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping

DEFAULT_TIMEOUT = 20

from fa.errors import ProviderError  # noqa: E402 - keep stdlib imports first


def get_json(url: str, params: Mapping[str, Any], *, timeout: int = DEFAULT_TIMEOUT) -> Any:
    """GET a JSON endpoint, raising :class:`ProviderError` on any failure."""
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    full_url = f"{url}?{query}" if query else url
    request = urllib.request.Request(full_url, headers={"User-Agent": "financial-analyzer/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https hosts
            payload = response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProviderError(f"HTTP request failed: {exc}") from exc
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ProviderError("provider returned a non-JSON payload") from exc
