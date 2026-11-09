import logging
import re
from typing import Optional

import httpx

log = logging.getLogger("paypilot.llm")
_NUM = re.compile(r"\d[\d,]*\.?\d*")


def _numbers(text: str) -> set:
    return {n.replace(",", "").rstrip(".") for n in _NUM.findall(text)}


def grounded(output: str, source: str) -> bool:
    """True if every number in `output` also appears in `source` (blocks invented figures)."""
    return _numbers(output) <= _numbers(source)


class LLMClient:
    """Optional OpenAI-compatible chat client. Returns None when disabled or on any failure,
    so callers always have a deterministic fallback."""

    def __init__(self, api_key: str, base_url: str, model: str, timeout: float = 20.0):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def generate(self, prompt: str, system: str = "", max_tokens: int = 400) -> Optional[str]:
        if not self.enabled:
            return None
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"model": self.model, "messages": messages,
                          "max_tokens": max_tokens, "temperature": 0.2},
                )
            r.raise_for_status()
            return (r.json()["choices"][0]["message"]["content"] or "").strip() or None
        except Exception as exc:  # network, HTTP, or shape errors: fall back silently
            log.warning("LLM call failed: %s", type(exc).__name__)
            return None
