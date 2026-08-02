import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import gemini_client
from gemini_client import GeminiClient, GeminiError, GeminiMalformedResponseError


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeModels:
    def __init__(self, text="hello", raise_error=False):
        self._text = text
        self._raise_error = raise_error
        self.last_kwargs = None

    def generate_content(self, **kwargs):
        self.last_kwargs = kwargs
        if self._raise_error:
            raise RuntimeError("network exploded")
        return FakeResponse(self._text)


class FakeClient:
    def __init__(self, text="hello", raise_error=False):
        self.models = FakeModels(text=text, raise_error=raise_error)


def make_client(monkeypatch, fake_client, api_key="fake-key"):
    monkeypatch.setattr(gemini_client.genai, "Client", lambda api_key: fake_client)
    return GeminiClient(api_key=api_key)


def test_missing_api_key_raises_gemini_error(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(GeminiError):
        GeminiClient(api_key=None)


def test_generate_text_returns_stripped_text(monkeypatch):
    fake_client = FakeClient(text="  a helpful answer  ")
    client = make_client(monkeypatch, fake_client)
    assert client.generate_text("some prompt") == "a helpful answer"
    assert fake_client.models.last_kwargs["contents"] == "some prompt"


def test_generate_text_wraps_sdk_exception(monkeypatch):
    fake_client = FakeClient(raise_error=True)
    client = make_client(monkeypatch, fake_client)
    with pytest.raises(GeminiError):
        client.generate_text("some prompt")


def test_generate_json_parses_valid_json(monkeypatch):
    fake_client = FakeClient(text='{"action": "add", "explanation": "ok"}')
    client = make_client(monkeypatch, fake_client)
    result = client.generate_json("some prompt", {"type": "object"})
    assert result == {"action": "add", "explanation": "ok"}


def test_generate_json_raises_malformed_on_bad_json(monkeypatch):
    fake_client = FakeClient(text="not json at all")
    client = make_client(monkeypatch, fake_client)
    with pytest.raises(GeminiMalformedResponseError):
        client.generate_json("some prompt", {"type": "object"})


def test_generate_json_wraps_sdk_exception(monkeypatch):
    fake_client = FakeClient(raise_error=True)
    client = make_client(monkeypatch, fake_client)
    with pytest.raises(GeminiError):
        client.generate_json("some prompt", {"type": "object"})
