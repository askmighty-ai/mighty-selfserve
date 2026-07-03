"""
AI provider abstraction for field discovery.

OpenAI is the default provider; Gemini remains available as an explicit choice
or optional fallback when AI_ALLOW_FALLBACK=true.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from mighty.ai_metrics import build_metrics, record_metrics
from mighty.ai_retry import call_with_retry
from mighty.field_discovery import (
    DiscoveryError,
    DiscoveryUnavailableError,
    _env_bool,
)


class DiscoveryProviderError(DiscoveryError):
    """The configured AI provider call failed."""


class DiscoveryValidationError(DiscoveryError):
    """The AI provider returned JSON that failed field validation."""


FIELD_DISCOVERY_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "label": {"type": "string"},
                    "value": {"type": "string"},
                    "value_type": {"type": "string"},
                    "confidence": {"type": "number"},
                    "source_snippet": {"type": "string"},
                    "expiry_date": {"type": ["string", "null"]},
                    "points": {"type": ["string", "null"]},
                    "currency": {"type": ["string", "null"]},
                },
                "required": [
                    "key",
                    "label",
                    "value",
                    "value_type",
                    "confidence",
                    "source_snippet",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["fields"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class DiscoveryContext:
    site_name: str
    source: str | None
    prompt: str
    prompt_id: str = "field_discovery"
    prompt_version: str = "1.0.0"
    today: str = ""
    category_hint: str = ""


@dataclass(frozen=True)
class DiscoveryResult:
    fields: list[dict[str, Any]]
    provider: str
    model: str
    metrics: Any | None = None


@dataclass
class OpenAIProvider:
    provider_name: str = field(default="openai", init=False)
    api_key: str = ""
    model: str = ""

    def __post_init__(self) -> None:
        if not self.api_key:
            self.api_key = os.environ.get("OPENAI_API_KEY", "")
        if not self.model:
            self.model = os.environ.get("OPENAI_FIELD_DISCOVERY_MODEL", "gpt-5.4-mini")
        self._client: Any | None = None
        if self.api_key:
            try:
                from openai import OpenAI

                self._client = OpenAI(api_key=self.api_key)
            except ImportError:
                self._client = None

    def is_configured(self) -> bool:
        return self._client is not None

    def unavailable_message(self) -> str:
        return "OpenAI API not configured — add OPENAI_API_KEY to Railway"

    def discover_fields(
        self,
        source: str | None,
        content: str,
        context: DiscoveryContext,
    ) -> DiscoveryResult:
        if not self.is_configured():
            raise DiscoveryUnavailableError(self.unavailable_message())

        started = time.perf_counter()
        try:
            response = call_with_retry(
                lambda: self._client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": context.prompt}],
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "discovered_fields",
                            "strict": True,
                            "schema": FIELD_DISCOVERY_JSON_SCHEMA,
                        },
                    },
                    temperature=0,
                ),
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            record_metrics(
                build_metrics(
                    provider=self.provider_name,
                    model=self.model,
                    latency_ms=latency_ms,
                    cache_hit=False,
                    prompt_id=context.prompt_id,
                    prompt_version=context.prompt_version,
                    input_text=context.prompt,
                    output_text="",
                ),
                failure_reason=str(exc),
                source=source,
            )
            raise DiscoveryProviderError(
                f"OpenAI field discovery failed ({self.model}): {exc}"
            ) from exc

        raw_text = (response.choices[0].message.content or "").strip()
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            record_metrics(
                build_metrics(
                    provider=self.provider_name,
                    model=self.model,
                    latency_ms=latency_ms,
                    cache_hit=False,
                    prompt_id=context.prompt_id,
                    prompt_version=context.prompt_version,
                    input_text=context.prompt,
                    output_text=raw_text,
                ),
                failure_reason=str(exc),
                source=source,
            )
            raise DiscoveryValidationError(
                f"OpenAI returned invalid JSON: {exc}"
            ) from exc

        fields = validate_discovered_fields(parsed)
        latency_ms = (time.perf_counter() - started) * 1000
        metrics = record_metrics(
            build_metrics(
                provider=self.provider_name,
                model=self.model,
                latency_ms=latency_ms,
                cache_hit=False,
                prompt_id=context.prompt_id,
                prompt_version=context.prompt_version,
                input_text=context.prompt,
                output_text=raw_text,
            ),
            source=source,
        )
        return DiscoveryResult(
            fields=fields,
            provider=self.provider_name,
            model=self.model,
            metrics=metrics,
        )


@dataclass
class GeminiProvider:
    provider_name: str = field(default="gemini", init=False)
    api_key: str = ""
    models: tuple[str, ...] = ("gemini-2.5-flash", "gemini-2.5-pro")

    def __post_init__(self) -> None:
        if not self.api_key:
            self.api_key = os.environ.get("GEMINI_API_KEY", "")
        self._client: Any | None = None
        if self.api_key:
            try:
                from google import genai as genai_sdk

                self._client = genai_sdk.Client(api_key=self.api_key)
            except ImportError:
                self._client = None

    def is_configured(self) -> bool:
        return self._client is not None

    def unavailable_message(self) -> str:
        return "Gemini API not configured — add GEMINI_API_KEY to Railway"

    def discover_fields(
        self,
        source: str | None,
        content: str,
        context: DiscoveryContext,
    ) -> DiscoveryResult:
        if not self.is_configured():
            raise DiscoveryUnavailableError(self.unavailable_message())

        from google import genai as genai_sdk

        started = time.perf_counter()
        model_errors: list[str] = []
        response = None
        used_model = ""
        for model_name in self.models:
            try:
                response = call_with_retry(
                    lambda model_name=model_name: self._client.models.generate_content(
                        model=model_name,
                        contents=context.prompt,
                        config=genai_sdk.types.GenerateContentConfig(
                            response_mime_type="application/json",
                            temperature=0,
                        ),
                    ),
                )
                used_model = model_name
                break
            except Exception as exc:
                model_errors.append(f"{model_name}: {exc}")

        if response is None:
            latency_ms = (time.perf_counter() - started) * 1000
            failure = "; ".join(model_errors)
            record_metrics(
                build_metrics(
                    provider=self.provider_name,
                    model=self.models[0],
                    latency_ms=latency_ms,
                    cache_hit=False,
                    prompt_id=context.prompt_id,
                    prompt_version=context.prompt_version,
                    input_text=context.prompt,
                    output_text="",
                ),
                failure_reason=failure,
                source=source,
            )
            raise DiscoveryProviderError(
                "All Gemini models failed: " + failure
            )

        raw_text = (response.text or "").strip()
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            match = re.search(r"\[.*\]", raw_text, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group())
                except json.JSONDecodeError as exc:
                    latency_ms = (time.perf_counter() - started) * 1000
                    record_metrics(
                        build_metrics(
                            provider=self.provider_name,
                            model=used_model,
                            latency_ms=latency_ms,
                            cache_hit=False,
                            prompt_id=context.prompt_id,
                            prompt_version=context.prompt_version,
                            input_text=context.prompt,
                            output_text=raw_text,
                        ),
                        failure_reason=str(exc),
                        source=source,
                    )
                    raise DiscoveryValidationError(
                        f"Gemini returned invalid JSON: {exc}"
                    ) from exc
            else:
                latency_ms = (time.perf_counter() - started) * 1000
                record_metrics(
                    build_metrics(
                        provider=self.provider_name,
                        model=used_model,
                        latency_ms=latency_ms,
                        cache_hit=False,
                        prompt_id=context.prompt_id,
                        prompt_version=context.prompt_version,
                        input_text=context.prompt,
                        output_text=raw_text,
                    ),
                    failure_reason="invalid JSON",
                    source=source,
                )
                raise DiscoveryValidationError("Gemini returned invalid JSON")

        fields = validate_discovered_fields(parsed)
        latency_ms = (time.perf_counter() - started) * 1000
        metrics = record_metrics(
            build_metrics(
                provider=self.provider_name,
                model=used_model,
                latency_ms=latency_ms,
                cache_hit=False,
                prompt_id=context.prompt_id,
                prompt_version=context.prompt_version,
                input_text=context.prompt,
                output_text=raw_text,
            ),
            source=source,
        )
        return DiscoveryResult(
            fields=fields,
            provider=self.provider_name,
            model=used_model,
            metrics=metrics,
        )


FieldDiscoveryProvider = OpenAIProvider | GeminiProvider


def ai_provider_name() -> str:
    return os.environ.get("AI_PROVIDER", "openai").strip().lower() or "openai"


def ai_allow_fallback() -> bool:
    return _env_bool("AI_ALLOW_FALLBACK", default=False)


def get_field_discovery_provider(name: str | None = None) -> FieldDiscoveryProvider:
    provider_name = (name or ai_provider_name()).strip().lower()
    if provider_name == "openai":
        return OpenAIProvider()
    if provider_name == "gemini":
        return GeminiProvider()
    raise DiscoveryUnavailableError(
        f"Unknown AI_PROVIDER '{provider_name}' — use openai or gemini"
    )


def get_configured_field_discovery_provider(
    name: str | None = None,
) -> FieldDiscoveryProvider:
    provider = get_field_discovery_provider(name)
    if not provider.is_configured():
        raise DiscoveryUnavailableError(provider.unavailable_message())
    return provider


def validate_discovered_fields(raw: Any) -> list[dict[str, Any]]:
    """Validate and normalize provider JSON before downstream filtering/saving."""
    items: list[Any]
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        items = None
        for key in ("fields", "data", "items", "results"):
            candidate = raw.get(key)
            if isinstance(candidate, list):
                items = candidate
                break
        if items is None:
            raise DiscoveryValidationError("Provider JSON missing fields array")
    else:
        raise DiscoveryValidationError("Provider JSON must be an array or object")

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise DiscoveryValidationError(f"Field at index {index} is not an object")

        key = str(item.get("key") or "").strip()
        label = str(item.get("label") or "").strip()
        value = str(item.get("value") or "").strip()
        value_type = str(item.get("value_type") or "").strip()
        source_snippet = str(item.get("source_snippet") or "").strip()
        confidence_raw = item.get("confidence")

        if not key or not label or not value_type:
            raise DiscoveryValidationError(
                f"Field at index {index} missing required key, label, or value_type"
            )
        if not isinstance(confidence_raw, (int, float)):
            raise DiscoveryValidationError(
                f"Field '{key}' has invalid confidence"
            )
        confidence = float(confidence_raw)
        if confidence < 0.0 or confidence > 1.0:
            raise DiscoveryValidationError(
                f"Field '{key}' confidence out of range"
            )

        field_dict: dict[str, Any] = {
            "key": key,
            "label": label,
            "value": value,
            "value_type": value_type,
            "confidence": confidence,
            "source_snippet": source_snippet,
        }
        for optional_key in ("expiry_date", "points", "currency"):
            optional_val = item.get(optional_key)
            if optional_val not in (None, ""):
                field_dict[optional_key] = str(optional_val).strip()

        normalized.append(field_dict)

    return normalized


def discover_fields_with_provider(
    source: str | None,
    content: str,
    context: DiscoveryContext,
) -> DiscoveryResult:
    """Run field discovery on the configured provider with optional Gemini fallback."""
    provider_name = ai_provider_name()
    provider = get_configured_field_discovery_provider(provider_name)
    try:
        return provider.discover_fields(source, content, context)
    except DiscoveryError:
        if ai_allow_fallback() and provider_name == "openai":
            fallback = GeminiProvider()
            if fallback.is_configured():
                return fallback.discover_fields(source, content, context)
        raise
