from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.providers.model_catalog import (
    load_cached_airlock_model_ids,
    refresh_airlock_model_ids,
)


class TestModelCatalog:
    def test_load_cached_airlock_model_ids_reads_cache_file(self, tmp_path: Path):
        cache = tmp_path / "airlock-models-cache.json"
        cache.write_text(json.dumps({"models": ["gpt-4o", "gemini-pro"]}), encoding="utf-8")

        with patch("src.providers.model_catalog.settings.logs_path", tmp_path):
            assert load_cached_airlock_model_ids() == ["gpt-4o", "gemini-pro"]

    def test_refresh_airlock_model_ids_writes_cache(self, tmp_path: Path):
        response = SimpleNamespace(
            json=lambda: {"data": [{"id": "gpt-4o"}, {"id": "gemini-pro"}]},
            raise_for_status=lambda: None,
        )
        client = SimpleNamespace(
            uses_router=True,
            _airlock_api_key="secret",
            _airlock_headers={"X-Airlock-Client": "one-0-one"},
            _router_url="http://127.0.0.1:4000/v1",
        )

        with patch("src.providers.model_catalog.settings.logs_path", tmp_path):
            with patch("src.providers.model_catalog.LiteLLMClient", return_value=client):
                with patch("src.providers.model_catalog.httpx.get", return_value=response):
                    models = refresh_airlock_model_ids()

        assert models == ["gemini-pro", "gpt-4o"]
        payload = json.loads((tmp_path / "airlock-models-cache.json").read_text(encoding="utf-8"))
        assert payload["models"] == ["gemini-pro", "gpt-4o"]

    def test_refresh_airlock_model_ids_falls_back_to_cache_on_error(self, tmp_path: Path):
        cache = tmp_path / "airlock-models-cache.json"
        cache.write_text(json.dumps({"models": ["gpt-4o"]}), encoding="utf-8")
        client = SimpleNamespace(
            uses_router=True,
            _airlock_api_key="secret",
            _airlock_headers={"X-Airlock-Client": "one-0-one"},
            _router_url="http://127.0.0.1:4000/v1",
        )

        with patch("src.providers.model_catalog.settings.logs_path", tmp_path):
            with patch("src.providers.model_catalog.LiteLLMClient", return_value=client):
                with patch("src.providers.model_catalog.httpx.get", side_effect=RuntimeError("down")):
                    models = refresh_airlock_model_ids()

        assert models == ["gpt-4o"]
