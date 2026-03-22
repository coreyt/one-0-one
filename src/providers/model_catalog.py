from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import litellm

from src.logging import get_logger
from src.providers.litellm_client import LiteLLMClient
from src.settings import settings

log = get_logger(__name__)


def _apply_litellm_alias_map(model_ids: list[str]) -> None:
    """Register bare Airlock model aliases in litellm.model_alias_map.

    Maps each plain alias (e.g. 'claude-haiku') to 'openai/<alias>' so
    litellm's provider detection routes it to the configured api_base
    (Airlock) without requiring a provider prefix in the model string.
    Already-prefixed IDs (containing '/') are skipped.
    """
    for model_id in model_ids:
        if "/" not in model_id:
            litellm.model_alias_map.setdefault(model_id, f"openai/{model_id}")


def _cache_path() -> Path:
    path = Path(settings.logs_path)
    path.mkdir(parents=True, exist_ok=True)
    return path / "airlock-models-cache.json"


def load_cached_airlock_model_ids() -> list[str]:
    path = _cache_path()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("airlock.models_cache_read_failed", path=str(path), error=str(exc))
        return []
    models = payload.get("models", []) if isinstance(payload, dict) else []
    if not isinstance(models, list):
        return []
    return [
        model.strip()
        for model in models
        if isinstance(model, str) and model.strip()
    ]


def _write_cached_airlock_model_ids(model_ids: list[str]) -> None:
    path = _cache_path()
    payload = {"models": model_ids}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def refresh_airlock_model_ids(*, timeout: float = 3.0) -> list[str]:
    """Fetch available Airlock-routed model IDs from the gateway and update cache."""
    client = LiteLLMClient()
    if not client.uses_router:
        return load_cached_airlock_model_ids()

    headers: dict[str, str] = {}
    if client._airlock_api_key:
        headers["Authorization"] = f"Bearer {client._airlock_api_key}"
    if client._airlock_headers.get("X-Airlock-Client"):
        headers["X-Airlock-Client"] = client._airlock_headers["X-Airlock-Client"]

    url = f"{client._router_url}/models"
    try:
        response = httpx.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        log.warning("airlock.models_fetch_failed", url=url, error=str(exc))
        return load_cached_airlock_model_ids()

    data = payload.get("data", []) if isinstance(payload, dict) else []
    model_ids = [
        item.get("id", "").strip()
        for item in data
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item.get("id", "").strip()
    ]
    model_ids = sorted(dict.fromkeys(model_ids))
    try:
        _write_cached_airlock_model_ids(model_ids)
    except Exception as exc:
        log.warning("airlock.models_cache_write_failed", path=str(_cache_path()), error=str(exc))
    _apply_litellm_alias_map(model_ids)
    return model_ids
