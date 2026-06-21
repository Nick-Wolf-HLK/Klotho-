"""Async OpenAI-compatible LLM client (works against ollama proxy or shim)."""
from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import dataclass
from typing import Optional

import httpx

# Rate-Limit-/Server-Fehler abfedern statt sofort sterben (Ollama Cloud 429).
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRIES = 4
BACKOFF_BASE = 2.0       # Sekunden; verdoppelt sich je Versuch
BACKOFF_CAP = 30.0       # Obergrenze pro Wartezeit


@dataclass
class LLMResult:
    text: str
    elapsed_ms: int
    raw: dict


class LLMClient:
    """Thin async wrapper around OpenAI-style /chat/completions."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434/v1",
        *,
        timeout: float = 300.0,
        api_key: str = "ollama",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.api_key = api_key

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    async def _post_json(self, payload: dict) -> dict:
        """POST mit Retry/Backoff bei 429 & 5xx (respektiert Retry-After).
        Wirft erst nach Ausschöpfen der Versuche."""
        url = f"{self.base_url}/chat/completions"
        last_exc: Optional[Exception] = None
        for attempt in range(MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(url, json=payload, headers=self._headers())
                if resp.status_code in RETRY_STATUSES and attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(_retry_delay(resp, attempt))
                    continue
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                status = exc.response.status_code if exc.response is not None else None
                if status in RETRY_STATUSES and attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(_retry_delay(exc.response, attempt))
                    continue
                raise
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
                last_exc = exc
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(_retry_delay(None, attempt))
                    continue
                raise
        if last_exc:
            raise last_exc
        raise RuntimeError("unreachable")

    async def chat(
        self,
        model: str,
        messages: list[dict],
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        response_format: Optional[dict] = None,
    ) -> LLMResult:
        payload: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if response_format is not None:
            payload["response_format"] = response_format

        start = time.perf_counter()
        data = await self._post_json(payload)
        elapsed = int((time.perf_counter() - start) * 1000)
        text = data["choices"][0]["message"]["content"]
        return LLMResult(text=text, elapsed_ms=elapsed, raw=data)

    async def chat_with_tools(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict],
        *,
        temperature: float = 0.3,
    ) -> dict:
        """OpenAI-style tool-calling turn. Returns the assistant *message* dict
        (which may contain ``tool_calls``)."""
        payload = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "temperature": temperature,
            "stream": False,
        }
        data = await self._post_json(payload)
        return data["choices"][0]["message"]

    async def chat_json(
        self,
        model: str,
        messages: list[dict],
        *,
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
    ) -> dict:
        """Best-effort JSON response. Falls back to extracting {...} from text."""
        try:
            result = await self.chat(
                model,
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
        except Exception:
            result = await self.chat(
                model, messages, temperature=temperature, max_tokens=max_tokens
            )
        return _parse_json(result.text)


def _retry_delay(resp, attempt: int) -> float:
    """Wartezeit vor dem nächsten Versuch: Retry-After-Header falls vorhanden,
    sonst exponentielles Backoff mit Jitter, gekappt."""
    if resp is not None:
        ra = resp.headers.get("Retry-After")
        if ra:
            try:
                return min(float(ra), BACKOFF_CAP)
            except ValueError:
                pass
    delay = min(BACKOFF_BASE * (2 ** attempt), BACKOFF_CAP)
    return delay + random.uniform(0, delay * 0.25)   # Jitter gegen Thundering Herd


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        # strip markdown code fences
        text = text.split("```", 2)[1] if "```" in text else text
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise