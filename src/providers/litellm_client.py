"""
LiteLLM-backed provider client.

All calls route through the local LiteLLM router (airlock) at
settings.litellm_router_url. The router handles auth, model routing,
and rate limiting for all configured providers.

Model string format:
    Pinned provider/model:
        "anthropic/claude-sonnet-4-6"
        "openai/gpt-4o"
    Airlock-routed model:
        "gpt-4o"
        "gemini-pro"

Native thinking support (monologue_mode="native"):
    Claude extended thinking → extra_body={"thinking": {"type": "enabled", ...}}
    OpenAI o-series         → extra_body={"reasoning_effort": "high"}

Usage:
    from src.providers.litellm_client import LiteLLMClient
    client = LiteLLMClient()
    result = await client.complete("anthropic/claude-sonnet-4-6", messages)
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any

import litellm

from src.logging import get_logger
from src.providers import (
    CommunicationSegment,
    CompletionResult,
    MonologueSegment,
    ProviderError,
    TokenUsage,
)
from src.settings import settings

if TYPE_CHECKING:
    pass

log = get_logger(__name__)

# Push provider API keys into os.environ so LiteLLM can find them in direct mode.
# pydantic-settings reads .env into model fields but doesn't write back to os.environ.
def _sync_api_keys() -> None:
    pairs = [
        ("ANTHROPIC_API_KEY", settings.anthropic_api_key),
        ("OPENAI_API_KEY", settings.openai_api_key),
        ("MISTRAL_API_KEY", settings.mistral_api_key),
        # Google AI Studio key — LiteLLM looks for GEMINI_API_KEY or GOOGLE_API_KEY
        ("GEMINI_API_KEY", settings.google_aistudio_api_key or settings.google_api_key),
        ("GOOGLE_API_KEY", settings.google_aistudio_api_key or settings.google_api_key),
    ]
    for name, value in pairs:
        if value and not os.environ.get(name):
            os.environ[name] = value

_sync_api_keys()

# Allow LiteLLM to inject a dummy user message when only a system message is present
# (Anthropic and some other providers require at least one non-system message)
litellm.modify_params = True

# Providers that support native extended thinking
_NATIVE_THINKING_PROVIDERS = frozenset(["anthropic"])
_REASONING_EFFORT_PROVIDERS = frozenset(["openai"])  # o-series models

# Claude models that support extended thinking (prefix match)
_CLAUDE_THINKING_MODELS = ("claude-3-7", "claude-3-5", "claude-opus-4", "claude-sonnet-4")


def _resolve_router_url(explicit_url: str | None) -> str:
    """Resolve router URL from explicit settings or AIRLOCK_HOST/AIRLOCK_PORT."""
    if explicit_url:
        return explicit_url
    host = os.environ.get("AIRLOCK_HOST", "").strip()
    port = os.environ.get("AIRLOCK_PORT", "").strip()
    if not host and not port:
        return ""
    if not host:
        host = "localhost"
    if not port:
        port = "4000"
    if host == "0.0.0.0":
        host = "127.0.0.1"
    return f"http://{host}:{port}/v1"


def _airlock_env_headers(default_client: str) -> dict[str, str]:
    """Project AIRLOCK_* environment context into request headers for the gateway."""
    headers: dict[str, str] = {}
    env_values = {
        name: value.strip()
        for name, value in os.environ.items()
        if name.startswith("AIRLOCK_") and isinstance(value, str) and value.strip()
    }
    if "AIRLOCK_CLIENT" not in env_values and default_client:
        env_values["AIRLOCK_CLIENT"] = default_client

    excluded = {"AIRLOCK_MASTER_KEY", "AIRLOCK_HOST", "AIRLOCK_PORT"}
    for name, value in env_values.items():
        if name in excluded:
            continue
        headers[name] = value
        suffix = name.removeprefix("AIRLOCK_").lower().split("_")
        canonical = "-".join(part.capitalize() for part in suffix if part)
        if canonical:
            headers[f"X-Airlock-{canonical}"] = value
    return headers


def _extract_airlock_response_metadata(response: Any) -> dict[str, Any]:
    """Capture Airlock-specific response metadata from LiteLLM response headers."""
    metadata: dict[str, Any] = {}
    headers = getattr(response, "_response_headers", None) or {}
    if not isinstance(headers, dict):
        return metadata
    normalized_headers = {
        str(key).lower(): value
        for key, value in headers.items()
    }

    interesting = {
        "x-airlock-model-override": "airlock_model_override",
        "x-airlock-provider-mode": "airlock_provider_mode",
        "x-airlock-reasoning-mode": "airlock_reasoning_mode",
        "x-airlock-provider-state": "airlock_provider_state",
        "x-airlock-empty-text-success": "airlock_empty_text_success",
    }
    for header_name, metadata_key in interesting.items():
        value = normalized_headers.get(header_name)
        if value is None:
            continue
        metadata[metadata_key] = value
    return metadata


def _supports_native_thinking(
    model: str,
    provider_hint: str | None = None,
) -> tuple[bool, str]:
    """
    Return (supports_thinking, provider) for a model string.
    Model format: "provider/model-name"
    """
    if "/" in model:
        provider, model_name = model.split("/", 1)
    else:
        provider = (provider_hint or "").strip()
        model_name = model
    if not provider:
        return False, ""
    if provider in _NATIVE_THINKING_PROVIDERS:
        if any(model_name.startswith(m) for m in _CLAUDE_THINKING_MODELS):
            return True, provider
    if provider in _REASONING_EFFORT_PROVIDERS and model_name.startswith("o"):
        return True, provider
    return False, provider


def _extract_text(value: Any) -> str:
    """Best-effort extraction of readable text from LiteLLM response fields."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    if isinstance(value, dict):
        text = value.get("text") or value.get("content")
        if isinstance(text, str):
            return text
    return str(value)


def _extract_native_monologue(message: Any) -> list[MonologueSegment]:
    """Best-effort extraction of provider-native reasoning/thinking content."""
    segments: list[MonologueSegment] = []
    candidates: list[Any] = []

    for attr in ("reasoning_content", "reasoning", "thinking", "reasoning_text"):
        value = getattr(message, attr, None)
        if value:
            candidates.append(value)

    if isinstance(message, dict):
        for key in ("reasoning_content", "reasoning", "thinking", "reasoning_text"):
            value = message.get(key)
            if value:
                candidates.append(value)

    for candidate in candidates:
        text = _extract_text(candidate).strip()
        if text:
            segments.append(
                MonologueSegment(
                    text=text,
                    source="provider_native",
                    redaction_status="raw",
                )
            )

    # De-duplicate identical segments from overlapping provider fields.
    deduped: list[MonologueSegment] = []
    seen: set[str] = set()
    for segment in segments:
        if segment.text in seen:
            continue
        seen.add(segment.text)
        deduped.append(segment)
    return deduped


class LiteLLMClient:
    """
    Provider client backed by LiteLLM router (airlock).

    Thread-safe and reusable across agents and sessions.
    """

    def __init__(self, router_url: str | None = None) -> None:
        # Empty string = direct mode (call providers without a proxy)
        configured_url = router_url if router_url is not None else settings.litellm_router_url
        self._router_url = _resolve_router_url(configured_url)
        self._airlock_api_key = os.environ.get("AIRLOCK_MASTER_KEY", "").strip()
        self._airlock_client = os.environ.get("AIRLOCK_CLIENT") or settings.airlock_client
        self._airlock_headers = _airlock_env_headers(self._airlock_client)
        if self._router_url:
            if self._airlock_client and not os.environ.get("AIRLOCK_CLIENT"):
                os.environ["AIRLOCK_CLIENT"] = self._airlock_client
            litellm.api_base = self._router_url

    @property
    def uses_router(self) -> bool:
        return bool(self._router_url)

    async def complete(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        native_thinking: bool = False,
        thinking_budget_tokens: int = 8000,
        timeout: int = 30,
        airlock_metadata: dict | None = None,
        **kwargs: Any,
    ) -> CompletionResult:
        """
        Send a completion request through the airlock LiteLLM router.

        Args:
            model: Requested model string (provider-prefixed in pinned mode, bare
                model name in Airlock-routed mode)
            messages: OpenAI-format message list
            temperature: Sampling temperature
            native_thinking: If True and model supports it, enable native thinking
            thinking_budget_tokens: Token budget for Claude extended thinking
            **kwargs: Passed through to litellm.acompletion

        Returns:
            CompletionResult

        Raises:
            ProviderError: on any LiteLLM or upstream API failure
        """
        provider_hint = str(kwargs.pop("provider_hint", "") or "")
        provider = model.split("/")[0] if "/" in model else (provider_hint or "unknown")
        prompt_tokens_estimate = sum(len(m.get("content", "")) // 4 for m in messages)

        log.info(
            "llm.request",
            model=model,
            provider=provider,
            messages_count=len(messages),
            prompt_tokens_estimate=prompt_tokens_estimate,
            native_thinking=native_thinking,
        )

        extra_body: dict[str, Any] = {}
        if airlock_metadata and self._router_url:
            extra_body["metadata"] = {"airlock": airlock_metadata}
        if native_thinking:
            supports, detected_provider = _supports_native_thinking(
                model,
                provider_hint=provider_hint,
            )
            if supports and detected_provider in _NATIVE_THINKING_PROVIDERS:
                extra_body["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": thinking_budget_tokens,
                }
            elif supports and detected_provider in _REASONING_EFFORT_PROVIDERS:
                extra_body["reasoning_effort"] = "high"

        start_ms = time.monotonic() * 1000
        try:
            call_kwargs: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                **kwargs,
            }
            if extra_body:
                call_kwargs["extra_body"] = extra_body
            if self._router_url:
                call_kwargs["api_base"] = self._router_url
                if self._airlock_api_key:
                    call_kwargs["api_key"] = self._airlock_api_key
                if self._airlock_headers:
                    call_kwargs["extra_headers"] = {
                        **call_kwargs.get("extra_headers", {}),
                        **self._airlock_headers,
                    }
            response = await litellm.acompletion(**call_kwargs, timeout=timeout)
        except litellm.exceptions.AuthenticationError as exc:
            raise ProviderError(
                f"Authentication failed for {model}: {exc}",
                provider=provider,
                model=model,
            ) from exc
        except litellm.exceptions.RateLimitError as exc:
            raise ProviderError(
                f"Rate limit exceeded for {model}: {exc}",
                provider=provider,
                model=model,
            ) from exc
        except litellm.exceptions.APIConnectionError as exc:
            raise ProviderError(
                f"Cannot connect to LiteLLM router at {self._router_url}: {exc}",
                provider=provider,
                model=model,
            ) from exc
        except Exception as exc:
            raise ProviderError(
                f"Unexpected error calling {model}: {exc}",
                provider=provider,
                model=model,
            ) from exc

        duration_ms = int(time.monotonic() * 1000 - start_ms)
        message = response.choices[0].message
        text = _extract_text(getattr(message, "content", ""))
        monologue = _extract_native_monologue(message)
        usage = TokenUsage(
            prompt_tokens=getattr(response.usage, "prompt_tokens", 0),
            completion_tokens=getattr(response.usage, "completion_tokens", 0),
        )
        actual_model = getattr(response, "model", model)

        log.info(
            "llm.response",
            model=actual_model,
            provider=provider,
            completion_tokens=usage.completion_tokens,
            prompt_tokens=usage.prompt_tokens,
            duration_ms=duration_ms,
        )

        communication = (
            [CommunicationSegment(visibility="public", text=text)]
            if text.strip()
            else []
        )

        metadata = {
            "native_thinking_requested": native_thinking,
            **_extract_airlock_response_metadata(response),
        }

        return CompletionResult(
            text=text,
            usage=usage,
            model=actual_model,
            communication=communication,
            monologue=monologue,
            metadata=metadata,
        )
