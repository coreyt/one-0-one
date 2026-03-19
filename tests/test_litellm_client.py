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
    )


class TestLiteLLMClient:
    async def test_airlock_requests_include_airlock_client_env_and_header(self):
        with patch.dict(os.environ, {}, clear=False):
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
                        assert kwargs["extra_headers"]["X-Airlock-Client"] == "one-0-one-tests"
