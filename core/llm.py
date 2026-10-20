"""Thin wrapper around the Anthropic SDK. Every caller must cope with LLMError and fall back."""
import json
import logging

import anthropic
from django.conf import settings

log = logging.getLogger(__name__)


class LLMError(Exception):
    pass


def is_available() -> bool:
    return bool(settings.ANTHROPIC_API_KEY)


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=90.0, max_retries=2)


def _call(system: str, user: str, max_tokens: int, output_format: dict | None = None) -> str:
    if not is_available():
        raise LLMError("No ANTHROPIC_API_KEY configured.")
    output_config = {"effort": "low"}  # extraction and short emails do not need deep reasoning
    if output_format:
        output_config["format"] = output_format
    try:
        response = _client().messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config=output_config,
        )
    except anthropic.APIStatusError as exc:
        raise LLMError(f"LLM API error {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise LLMError("Could not reach the LLM API.") from exc
    if response.stop_reason == "refusal":
        raise LLMError("The model declined this request.")
    if response.stop_reason == "max_tokens":
        raise LLMError("The model response was cut off.")
    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text.strip():
        raise LLMError("Empty response from the model.")
    return text


def complete_json(system: str, user: str, schema: dict, max_tokens: int = 4000) -> dict:
    text = _call(system, user, max_tokens, {"type": "json_schema", "schema": schema})
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError("The model returned invalid JSON.") from exc
    if not isinstance(data, dict):
        raise LLMError("The model returned an unexpected JSON shape.")
    return data


def complete_text(system: str, user: str, max_tokens: int = 1500) -> str:
    return _call(system, user, max_tokens).strip()
