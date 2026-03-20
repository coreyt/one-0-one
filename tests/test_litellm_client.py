from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.providers.litellm_client import LiteLLMClient


def _mock_litellm_response() -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="Hello"))],
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=1),
        model="openai/gpt-4o",
        _response_headers={
            "x-airlock-model-override": "gpt-4o-mini",
            "x-airlock-provider-mode": "openai",
            "x-airlock-provider-state": "text",
        },
    )


class TestLiteLLMClient:
    async def test_airlock_requests_include_airlock_env_context_in_headers(self):
        with patch.dict(
            os.environ,
            {
                "AIRLOCK_TRACE_ID": "trace-123",
                "AIRLOCK_HOST": "0.0.0.0",
                "AIRLOCK_PORT": "4000",
            },
            clear=False,
        ):
            with patch("src.providers.litellm_client.settings.litellm_router_url", "http://localhost:4000"):
                with patch("src.providers.litellm_client.settings.airlock_client", "one-0-one-tests"):
                    with patch(
                        "src.providers.litellm_client.litellm.acompletion",
                        AsyncMock(return_value=_mock_litellm_response()),
                    ) as completion_mock:
                        client = LiteLLMClient()
                        await client.complete(
                            model="openai/gpt-4o",
                            messages=[{"role": "user", "content": "Hi"}],
                        )
                        assert os.environ["AIRLOCK_CLIENT"] == "one-0-one-tests"
                        kwargs = completion_mock.await_args.kwargs
                        assert kwargs["api_base"] == "http://localhost:4000"
                        assert kwargs["extra_headers"]["AIRLOCK_CLIENT"] == "one-0-one-tests"
                        assert kwargs["extra_headers"]["X-Airlock-Client"] == "one-0-one-tests"
                        assert kwargs["extra_headers"]["AIRLOCK_TRACE_ID"] == "trace-123"
                        assert kwargs["extra_headers"]["X-Airlock-Trace-Id"] == "trace-123"
                        assert "AIRLOCK_HOST" not in kwargs["extra_headers"]
                        assert "AIRLOCK_PORT" not in kwargs["extra_headers"]

    async def test_airlock_response_headers_are_captured_in_metadata(self):
        with patch("src.providers.litellm_client.settings.litellm_router_url", "http://localhost:4000"):
            with patch(
                "src.providers.litellm_client.litellm.acompletion",
                AsyncMock(return_value=_mock_litellm_response()),
            ):
                client = LiteLLMClient()
                result = await client.complete(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": "Hi"}],
                )

        assert result.metadata["airlock_model_override"] == "gpt-4o-mini"
        assert result.metadata["airlock_provider_mode"] == "openai"
        assert result.metadata["airlock_provider_state"] == "text"
