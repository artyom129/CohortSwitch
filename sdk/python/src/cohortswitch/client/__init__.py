import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Self

import httpx

from cohortswitch.errors import CohortSwitchError
from cohortswitch.models import EvaluationResult


@dataclass(slots=True)
class _CacheEntry:
    expires_at: float
    result: EvaluationResult


class CohortSwitchClient:
    def __init__(
        self,
        *,
        base_url: str,
        sdk_key: str,
        timeout: float | httpx.Timeout = 2.0,
        cache_ttl_seconds: float = 0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not sdk_key.startswith("cs_sdk_"):
            raise ValueError("sdk_key must be a CohortSwitch SDK key")
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
        )
        self._client.headers["X-CohortSwitch-Key"] = sdk_key
        self._cache_ttl = max(0, cache_ttl_seconds)
        self._cache: dict[str, _CacheEntry] = {}

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def evaluate(
        self,
        flag_key: str,
        *,
        subject_key: str,
        attributes: dict[str, Any] | None = None,
    ) -> EvaluationResult:
        attributes = attributes or {}
        cache_key = self._cache_key(flag_key, subject_key, attributes)
        cached = self._cache.get(cache_key)
        if cached and cached.expires_at > time.monotonic():
            return cached.result
        payload = await self._post(
            "/api/v1/evaluate",
            {"flag_key": flag_key, "subject_key": subject_key, "attributes": attributes},
        )
        result = EvaluationResult.from_payload(payload)
        if self._cache_ttl:
            self._cache[cache_key] = _CacheEntry(
                expires_at=time.monotonic() + self._cache_ttl,
                result=result,
            )
            self._prune_cache()
        return result

    async def is_enabled(
        self,
        flag_key: str,
        *,
        subject_key: str,
        attributes: dict[str, Any] | None = None,
    ) -> bool:
        result = await self.evaluate(flag_key, subject_key=subject_key, attributes=attributes)
        if type(result.value) is not bool:
            raise CohortSwitchError(
                f"Flag {flag_key!r} did not return a boolean value",
                code="type_mismatch",
            )
        return result.value

    async def get_value(
        self,
        flag_key: str,
        *,
        subject_key: str,
        attributes: dict[str, Any] | None = None,
    ) -> Any:
        return (await self.evaluate(flag_key, subject_key=subject_key, attributes=attributes)).value

    async def evaluate_batch(
        self,
        flag_keys: list[str],
        *,
        subject_key: str,
        attributes: dict[str, Any] | None = None,
    ) -> dict[str, EvaluationResult]:
        payload = await self._post(
            "/api/v1/evaluate/batch",
            {
                "flags": flag_keys,
                "subject_key": subject_key,
                "attributes": attributes or {},
            },
        )
        return {
            key: EvaluationResult.from_payload(value) for key, value in payload["results"].items()
        }

    def clear_cache(self) -> None:
        self._cache.clear()

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(path, json=payload)
        except httpx.TimeoutException as exc:
            raise CohortSwitchError("CohortSwitch request timed out", code="timeout") from exc
        except httpx.HTTPError as exc:
            raise CohortSwitchError("CohortSwitch request failed", code="network_error") from exc
        if response.is_error:
            try:
                error = response.json()["error"]
                code = str(error.get("code", "api_error"))
                message = str(error.get("message", "CohortSwitch API returned an error"))
            except (ValueError, KeyError, TypeError):
                code = "api_error"
                message = "CohortSwitch API returned an error"
            raise CohortSwitchError(message, code=code, status_code=response.status_code)
        return response.json()

    @staticmethod
    def _cache_key(flag_key: str, subject_key: str, attributes: dict[str, Any]) -> str:
        canonical = json.dumps(attributes, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(f"{flag_key}\0{subject_key}\0{canonical}".encode()).hexdigest()

    def _prune_cache(self) -> None:
        if len(self._cache) <= 10_000:
            return
        now = time.monotonic()
        self._cache = {key: value for key, value in self._cache.items() if value.expires_at > now}
