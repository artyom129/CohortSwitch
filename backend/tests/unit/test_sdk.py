from datetime import UTC, datetime

import httpx
import pytest
from cohortswitch import CohortSwitchClient, CohortSwitchError


def evaluation_payload(value: object = True) -> dict[str, object]:
    return {
        "flag_key": "new_checkout",
        "value": value,
        "variation": "enabled",
        "reason": "percentage_rollout",
        "matched_rule": None,
        "config_version": 17,
        "evaluated_at": datetime.now(UTC).isoformat(),
    }


@pytest.mark.asyncio
async def test_sdk_evaluates_and_sends_authentication() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-CohortSwitch-Key"] == "cs_sdk_live_test"
        assert request.url.path == "/api/v1/evaluate"
        return httpx.Response(200, json=evaluation_payload())

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url="http://test", transport=transport)
    client = CohortSwitchClient(
        base_url="http://test", sdk_key="cs_sdk_live_test", http_client=http_client
    )
    assert await client.is_enabled("new_checkout", subject_key="user_42") is True
    await http_client.aclose()


@pytest.mark.asyncio
async def test_sdk_local_cache_avoids_duplicate_request() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=evaluation_payload())

    http_client = httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(handler))
    client = CohortSwitchClient(
        base_url="http://test",
        sdk_key="cs_sdk_live_test",
        cache_ttl_seconds=30,
        http_client=http_client,
    )
    await client.evaluate("new_checkout", subject_key="user_42")
    await client.evaluate("new_checkout", subject_key="user_42")
    assert calls == 1
    await http_client.aclose()


@pytest.mark.asyncio
async def test_sdk_surfaces_typed_api_error() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            404, json={"error": {"code": "flag_not_found", "message": "Missing"}}
        )
    )
    http_client = httpx.AsyncClient(base_url="http://test", transport=transport)
    client = CohortSwitchClient(
        base_url="http://test", sdk_key="cs_sdk_live_test", http_client=http_client
    )
    with pytest.raises(CohortSwitchError) as error:
        await client.evaluate("missing", subject_key="user_42")
    assert error.value.code == "flag_not_found"
    assert error.value.status_code == 404
    await http_client.aclose()
