"""VLM provider isolation.

The rest of the app depends only on `VLMClient.extract(image_path, prompt) -> str`.
To swap providers, implement another class with that method and change
`create_client()`. The default implementation speaks the OpenAI-compatible
chat-completions API, which most hosted and local VLM servers support.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Protocol

from .utils import (
    MIME_TYPES,
    EmptyResponseError,
    InputError,
    VLMAuthError,
    VLMError,
    VLMTimeoutError,
)


class VLMClient(Protocol):
    model: str

    def extract(self, image_path: Path, prompt: str) -> str:
        """Send the image + prompt, return the raw text response."""
        ...


class OpenAICompatibleClient:
    def __init__(self, api_key: str, model: str, base_url: str | None = None,
                 timeout: float = 180.0, max_tokens: int = 8000, json_mode: bool = False):
        from openai import OpenAI  # imported lazily so other providers don't need it

        self.model = model
        self.max_tokens = max_tokens
        self.json_mode = json_mode
        self._client = OpenAI(api_key=api_key, base_url=base_url or None, timeout=timeout, max_retries=1)

    def extract(self, image_path: Path, prompt: str) -> str:
        import openai

        suffix = image_path.suffix.lower()
        try:
            b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
        except OSError as exc:
            raise InputError(f"Could not read image {image_path}: {exc}") from exc
        data_url = f"data:{MIME_TYPES[suffix]};base64,{b64}"

        kwargs = {}
        if self.json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                temperature=0,
                max_tokens=self.max_tokens,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
                    ],
                }],
                **kwargs,
            )
        except openai.AuthenticationError as exc:
            raise VLMAuthError("Invalid VLM credentials (authentication failed). Check VLM_API_KEY.") from exc
        except openai.PermissionDeniedError as exc:
            raise VLMAuthError("VLM access denied. Check that your key may use this model.") from exc
        except openai.APITimeoutError as exc:
            raise VLMTimeoutError("VLM request timed out. Increase VLM_TIMEOUT_SECONDS or retry.") from exc
        except openai.APIConnectionError as exc:
            raise VLMError(f"Could not connect to the VLM endpoint (check VLM_BASE_URL): {exc}") from exc
        except openai.NotFoundError as exc:
            raise VLMError(f"VLM endpoint or model not found (check VLM_MODEL / VLM_BASE_URL): {exc}") from exc
        except openai.APIStatusError as exc:
            raise VLMError(f"VLM API error (HTTP {exc.status_code}): {exc}") from exc

        text = resp.choices[0].message.content if resp.choices else None
        if not text or not text.strip():
            raise EmptyResponseError("The VLM returned an empty response.")
        return text


class DomoClient:
    """Domo AI Service Layer (image-to-text). Auth: X-DOMO-Developer-Token.

    NOTE: /ai/v1/text/generation silently ignores images (answers are hallucinated),
    so this must use /ai/v1/image/text.
    """

    def __init__(self, api_key: str, base_url: str, model: str, timeout: float = 180.0,
                 max_tokens: int = 16000):
        self.model = model
        self.max_tokens = max_tokens
        self._url = base_url.rstrip("/") + "/ai/v1/image/text"
        self._key = api_key
        self._timeout = timeout

    def extract(self, image_path: Path, prompt: str) -> str:
        import httpx

        try:
            b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
        except OSError as exc:
            raise InputError(f"Could not read image {image_path}: {exc}") from exc
        body = {
            "input": prompt,
            "model": self.model,
            # Without maxTokens Domo truncates output at ~4k tokens, which cuts the JSON off.
            "maxTokens": self.max_tokens,
            "image": {"type": "base64", "mediaType": MIME_TYPES[image_path.suffix.lower()], "data": b64},
        }
        try:
            r = httpx.post(self._url, json=body, headers={"X-DOMO-Developer-Token": self._key},
                           timeout=self._timeout)
        except httpx.TimeoutException as exc:
            raise VLMTimeoutError("Domo request timed out. Increase VLM_TIMEOUT_SECONDS or retry.") from exc
        except httpx.HTTPError as exc:
            raise VLMError(f"Could not reach Domo ({type(exc).__name__}). Check DOMO_BASE_URL.") from exc
        if r.status_code in (401, 403):
            raise VLMAuthError("Domo rejected the credentials. Check DOMO_API_KEY (developer token).")
        if r.status_code >= 400:
            raise VLMError(f"Domo API error (HTTP {r.status_code}): {r.text[:300].replace(self._key, '<key>')}")
        try:
            data = r.json()
        except ValueError as exc:
            raise VLMError("Domo returned a non-JSON response.") from exc
        text = data.get("output")
        if not text and data.get("choices"):
            text = data["choices"][0].get("output")
        if not text or not str(text).strip():
            raise EmptyResponseError("The VLM returned an empty response.")
        return str(text)


def create_client() -> VLMClient:
    """Build a client from environment variables (never logs the key).

    VLM_PROVIDER = openai | domo. If unset: domo when DOMO_API_KEY is set and VLM_API_KEY is not.
    """
    from dotenv import load_dotenv

    load_dotenv()
    provider = os.getenv("VLM_PROVIDER", "").strip().lower()
    if not provider:
        provider = "domo" if os.getenv("DOMO_API_KEY", "").strip() and not os.getenv("VLM_API_KEY", "").strip() else "openai"
    if provider == "domo":
        key = os.getenv("DOMO_API_KEY", "").strip()
        base = os.getenv("DOMO_BASE_URL", "").strip()
        model = os.getenv("DOMO_MODEL", "").strip()
        if not key:
            raise VLMAuthError("DOMO_API_KEY is not set in .env.")
        if not base or not model:
            raise VLMError("DOMO_BASE_URL and DOMO_MODEL must be set in .env.")
        try:
            timeout = float(os.getenv("VLM_TIMEOUT_SECONDS", "180"))
            max_tokens = int(os.getenv("VLM_MAX_TOKENS", "16000"))
        except ValueError as exc:
            raise VLMError(f"Invalid numeric VLM_* setting: {exc}") from exc
        return DomoClient(key, base, model, timeout, max_tokens)
    if provider != "openai":
        raise VLMError(f"Unknown VLM_PROVIDER '{provider}' (use 'openai' or 'domo').")
    api_key = os.getenv("VLM_API_KEY", "").strip()
    model = os.getenv("VLM_MODEL", "").strip()
    if not api_key:
        raise VLMAuthError("VLM_API_KEY is not set. Copy .env.example to .env and fill it in.")
    if not model:
        raise VLMError("VLM_MODEL is not set. Copy .env.example to .env and fill it in.")
    try:
        timeout = float(os.getenv("VLM_TIMEOUT_SECONDS", "180"))
        max_tokens = int(os.getenv("VLM_MAX_TOKENS", "16000"))
    except ValueError as exc:
        raise VLMError(f"Invalid numeric VLM_* setting: {exc}") from exc
    return OpenAICompatibleClient(
        api_key=api_key,
        model=model,
        base_url=os.getenv("VLM_BASE_URL", "").strip() or None,
        timeout=timeout,
        max_tokens=max_tokens,
        json_mode=os.getenv("VLM_JSON_MODE", "false").strip().lower() in ("1", "true", "yes"),
    )
