# GeminiClient: thin wrapper around the Gemini SDK (google-genai).
#
# This is the ONLY module that imports google.genai. It performs no business
# logic, no validation, and holds no Task/Owner/Scheduler awareness -- it just
# sends a prompt and returns text or parsed JSON, or raises a typed error.
# Keeping the SDK import isolated here lets everything downstream
# (llm_assistant.py) be tested against a fake implementing the same interface,
# with zero network access required.

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from google import genai
from google.genai import types


class GeminiError(Exception):
    """Raised on any Gemini API failure: missing key, auth, network, timeout."""


class GeminiMalformedResponseError(GeminiError):
    """Raised when Gemini returned a response but it wasn't parseable JSON."""


@dataclass
class GeminiClient:
    """Sends prompts to Gemini and returns text or JSON. No retries, no caching."""

    model_name: str = field(default_factory=lambda: os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"))
    api_key: Optional[str] = None
    timeout_s: int = 20

    def __post_init__(self) -> None:
        key = self.api_key or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise GeminiError("GEMINI_API_KEY is not set (check your .env file).")
        self._client = genai.Client(api_key=key)

    def generate_text(self, prompt: str) -> str:
        """Freeform text generation, used for Q&A."""
        try:
            resp = self._client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    http_options=types.HttpOptions(timeout=self.timeout_s * 1000),
                ),
            )
            return (resp.text or "").strip()
        except Exception as e:
            raise GeminiError(str(e)) from e

    def generate_json(self, prompt: str, schema: Dict[str, Any]) -> dict:
        """JSON-mode generation for structured schedule-edit proposals."""
        try:
            resp = self._client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=schema,
                    http_options=types.HttpOptions(timeout=self.timeout_s * 1000),
                ),
            )
        except Exception as e:
            raise GeminiError(str(e)) from e
        try:
            return json.loads(resp.text)
        except (json.JSONDecodeError, AttributeError, TypeError) as e:
            raise GeminiMalformedResponseError(f"Gemini returned unparseable JSON: {e}") from e
