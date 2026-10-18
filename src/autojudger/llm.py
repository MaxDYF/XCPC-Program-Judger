from __future__ import annotations

import json
import os
import ipaddress
from typing import Any
from urllib.parse import urlparse

import httpx

from .models import JudgingError


class ChatAgent:
    """Minimal JSON-only LLM adapter. It has no browser or other tool interface."""

    def __init__(self, api_key: str | None = None, model: str | None = None,
                 base_url: str | None = None, client: httpx.Client | None = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-6-luna")
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        hostname = urlparse(self.base_url).hostname or ""
        try:
            private_endpoint = ipaddress.ip_address(hostname).is_private
        except ValueError:
            private_endpoint = hostname in ("localhost",)
        self.client = client or httpx.Client(timeout=180, trust_env=not private_endpoint)
        if not self.api_key:
            raise JudgingError("OPENAI_API_KEY is required")

    def ask_json(self, system: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = {"model": self.model,
                   "response_format": {"type": "json_object"},
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]}
        if not self.model.startswith("gpt-6"):
            request["temperature"] = 0
        response = self.client.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=request,
        )
        try:
            response.raise_for_status()
            choice = response.json()["choices"][0]
            if choice.get("finish_reason") != "stop" or choice["message"].get("refusal"):
                raise JudgingError(f"Agent response was incomplete or refused: {choice.get('finish_reason')}")
            value = json.loads(choice["message"]["content"])
        except JudgingError:
            raise
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise JudgingError(f"Agent call failed: {exc}") from exc
        if not isinstance(value, dict):
            raise JudgingError("Agent returned a non-object JSON value")
        return value
