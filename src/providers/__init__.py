"""
Provider layer public API.

Defines the ProviderClient protocol and CompletionResult dataclass.
All provider implementations must satisfy the ProviderClient protocol.

Usage:
    from src.providers import ProviderClient, CompletionResult
    from src.providers.litellm_client import LiteLLMClient

    client = LiteLLMClient()
    result = await client.complete(
        model="anthropic/claude-sonnet-4-6",
        messages=[{"role": "user", "content": "Hello"}],
    )
    print(result.text)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class CompletionResult:
    """Result of a single LLM completion call."""

    text: str
    """Full raw response text (may contain XML routing tags)."""

    usage: TokenUsage = field(default_factory=TokenUsage)
    """Token consumption for this call."""

    model: str = ""
    """Actual model used (may differ from requested on router fallback)."""


class ProviderError(Exception):
    """Raised when an LLM provider call fails."""

    def __init__(self, message: str, provider: str = "", model: str = "") -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model


@runtime_checkable
class ProviderClient(Protocol):
    """Interface all provider implementations must satisfy."""

    async def complete(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        **kwargs,
    ) -> CompletionResult:
        """
        Send a completion request and return the result.

        Args:
            model: Provider-prefixed model string, e.g. "anthropic/claude-sonnet-4-6"
            messages: OpenAI-format message list [{"role": ..., "content": ...}]
            temperature: Sampling temperature (0.0–1.0)
            **kwargs: Additional provider-specific parameters

        Returns:
            CompletionResult with text, usage, and actual model used

        Raises:
            ProviderError: on API failure, timeout, or quota exceeded
        """
        ...
