"""Client for a local, OpenAI-compatible model server (LM Studio, llama.cpp, vLLM…).

Only used for cheap auxiliary tasks; the heavyweight strategic report stays on
the remote model. Every failure degrades gracefully: the caller keeps working
without the local model instead of blowing up.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from fa.config import Settings

logger = logging.getLogger(__name__)


class LocalAIError(RuntimeError):
    """Raised when the local model server cannot answer."""


@dataclass(frozen=True)
class LocalAIClient:
    """Minimal chat-completions client. No third-party SDK required."""

    base_url: str
    model: str | None
    api_key: str = "not-needed"
    timeout: int = 240
    max_tokens: int = 2000

    @classmethod
    def from_settings(cls, settings: Settings) -> "LocalAIClient":
        return cls(
            base_url=settings.local_ai_url,
            model=settings.local_ai_model,
            api_key=settings.local_ai_api_key,
            timeout=settings.local_ai_timeout,
            max_tokens=settings.local_ai_max_tokens,
        )

    def available(self) -> bool:
        return bool(self.base_url and self.model)

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - user configured host
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise LocalAIError(f"local model server unreachable: {exc}") from exc

    def chat(self, system: str, user: str, *, temperature: float = 0.1, max_tokens: int | None = None) -> str:
        """Single-turn completion returning the assistant text.

        Reasoning models spend part of the budget on ``reasoning_content``; when
        that swallows the whole allowance the answer comes back empty or cut, so
        both cases are reported instead of looking like "no local model".
        """
        if not self.available():
            raise LocalAIError("FA_LOCAL_AI_MODEL is not configured")
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_tokens or self.max_tokens,
            "stream": False,
        }
        body = self._post("/chat/completions", payload)
        try:
            choice = body["choices"][0]
            content = (choice["message"].get("content") or "").strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise LocalAIError(f"unexpected response from the local model: {body}") from exc
        if not content:
            reasoning = len(choice["message"].get("reasoning_content") or "")
            raise LocalAIError(
                f"the local model returned no content (finish_reason={choice.get('finish_reason')}, "
                f"reasoning chars={reasoning}); raise FA_LOCAL_AI_MAX_TOKENS"
            )
        if choice.get("finish_reason") == "length":
            logger.warning(
                "local answer truncated at max_tokens=%s; raise FA_LOCAL_AI_MAX_TOKENS",
                payload["max_tokens"],
            )
        return content

    def ping(self) -> list[str]:
        """List the models the server exposes; used by the doctor command."""
        request = urllib.request.Request(
            f"{self.base_url}/models", headers={"Authorization": f"Bearer {self.api_key}"}
        )
        try:
            with urllib.request.urlopen(request, timeout=min(self.timeout, 15)) as response:  # noqa: S310
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise LocalAIError(f"local model server unreachable: {exc}") from exc
        return [str(item.get("id")) for item in body.get("data", []) if item.get("id")]
