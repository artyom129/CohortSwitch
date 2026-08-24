import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from cohortswitch import CohortSwitchClient
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import text

from backend.app.core.redis import redis_client
from backend.app.database.session import SessionFactory
from backend.app.main import app

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def clean_state() -> AsyncIterator[None]:
    async with SessionFactory() as session:
        await session.execute(
            text(
                "TRUNCATE evaluation_events, audit_events, api_keys, scheduled_changes, "
                "flag_versions, progressive_stages, rollout_allocations, rule_conditions, "
                "targeting_rules, flag_variations, flag_configs, feature_flags, environments, "
                "projects, memberships, organizations, refresh_tokens, users CASCADE"
            )
        )
        await session.commit()
    await redis_client.flushdb()
    yield
    await redis_client.flushdb()


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as value:
        yield value


async def bootstrap(client: httpx.AsyncClient) -> dict[str, Any]:
    register = await client.post(
        "/api/v1/auth/register",
        json={"email": "owner@example.com", "password": "correct-horse-battery-staple"},
    )
    assert register.status_code == 201, register.text
    access_token = register.json()["access_token"]
    auth = {"Authorization": f"Bearer {access_token}"}

    organization = await client.post(
        "/api/v1/organizations",
        headers=auth,
        json={"name": "Example Inc", "slug": "example-inc"},
    )
    assert organization.status_code == 201, organization.text
    organization_id = organization.json()["id"]

    project = await client.post(
        "/api/v1/projects",
        headers=auth,
        json={"organization_id": organization_id, "name": "Storefront", "slug": "storefront"},
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]

    environment = await client.post(
        f"/api/v1/projects/{project_id}/environments",
        headers=auth,
        json={"key": "production", "name": "Production"},
    )
    assert environment.status_code == 201, environment.text
    environment_id = environment.json()["id"]

    flag = await client.post(
        f"/api/v1/projects/{project_id}/flags",
        headers=auth,
        json={
            "environment_id": environment_id,
            "key": "new_checkout",
            "name": "New checkout",
            "description": "Redesigned checkout",
            "flag_type": "BOOLEAN",
            "enabled": True,
            "default_variation": "disabled",
            "variations": [
                {"key": "enabled", "value": True},
                {"key": "disabled", "value": False},
            ],
            "rules": [
                {
                    "name": "pro-kz",
                    "priority": 0,
                    "conditions": [
                        {"attribute": "country", "operator": "equals", "value": "KZ"},
                        {"attribute": "plan", "operator": "equals", "value": "PRO"},
                    ],
                    "variation": "enabled",
                }
            ],
            "rollout": [
                {"variation": "enabled", "percentage_basis_points": 500},
                {"variation": "disabled", "percentage_basis_points": 9500},
            ],
        },
    )
    assert flag.status_code == 201, flag.text

    sdk_key = await client.post(
        "/api/v1/sdk-keys",
        headers=auth,
        json={
            "name": "production-sdk",
            "organization_id": organization_id,
            "project_id": project_id,
            "environment_id": environment_id,
        },
    )
    assert sdk_key.status_code == 201, sdk_key.text
    return {
        "auth": auth,
        "organization_id": organization_id,
        "project_id": project_id,
        "environment_id": environment_id,
        "flag": flag.json(),
        "sdk_key": sdk_key.json()["key"],
    }


@pytest.mark.asyncio
async def test_full_management_evaluation_version_and_audit_flow(
    client: httpx.AsyncClient,
) -> None:
    state = await bootstrap(client)
    sdk_headers = {"X-CohortSwitch-Key": state["sdk_key"]}
    body = {
        "flag_key": "new_checkout",
        "subject_key": "user_42",
        "attributes": {"country": "KZ", "plan": "PRO"},
    }
    decisions = [
        (await client.post("/api/v1/evaluate", headers=sdk_headers, json=body)).json()
        for _ in range(5)
    ]
    assert {item["variation"] for item in decisions} == {"enabled"}
    assert {item["config_version"] for item in decisions} == {1}

    config_url = (
        f"/api/v1/projects/{state['project_id']}/environments/{state['environment_id']}/config"
    )
    snapshot = await client.get(config_url, headers=sdk_headers)
    assert snapshot.status_code == 200
    assert snapshot.headers["etag"] == '"1"'
    unchanged = await client.get(
        config_url,
        headers={**sdk_headers, "If-None-Match": snapshot.headers["etag"]},
    )
    assert unchanged.status_code == 304

    flag_id = state["flag"]["id"]
    update_payload = {
        "environment_id": state["environment_id"],
        "expected_version": 1,
        "description": "Kill switch exercised",
        "enabled": False,
        "default_variation": "disabled",
        "variations": state["flag"]["variations"],
        "rules": state["flag"]["rules"],
        "rollout": state["flag"]["rollout"],
        "progressive_stages": [],
    }
    updated = await client.put(
        f"/api/v1/flags/{flag_id}", headers=state["auth"], json=update_payload
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["current_version"] == 2
    disabled = await client.post("/api/v1/evaluate", headers=sdk_headers, json=body)
    assert disabled.json()["reason"] == "flag_disabled"
    assert disabled.json()["value"] is False

    stale = await client.put(f"/api/v1/flags/{flag_id}", headers=state["auth"], json=update_payload)
    assert stale.status_code == 409
    assert stale.json()["error"]["current_version"] == 2

    rollback = await client.post(
        f"/api/v1/flags/{flag_id}/rollback/1",
        headers=state["auth"],
        json={"environment_id": state["environment_id"], "expected_version": 2},
    )
    assert rollback.status_code == 200, rollback.text
    assert rollback.json()["current_version"] == 3
    assert rollback.json()["enabled"] is True

    versions = await client.get(
        f"/api/v1/flags/{flag_id}/versions",
        headers=state["auth"],
        params={"environment_id": state["environment_id"]},
    )
    assert [item["version"] for item in versions.json()] == [3, 2, 1]
    assert versions.json()[0]["source_version"] == 1

    audit = await client.get(
        f"/api/v1/organizations/{state['organization_id']}/audit",
        headers=state["auth"],
    )
    actions = {item["action"] for item in audit.json()}
    assert {"flag.created", "flag.updated", "flag.rollback", "api_key.created"} <= actions

    forbidden = await client.get(
        f"/api/v1/organizations/{state['organization_id']}/audit", headers=sdk_headers
    )
    assert forbidden.status_code == 403


@pytest.mark.asyncio
async def test_only_one_concurrent_writer_wins(client: httpx.AsyncClient) -> None:
    state = await bootstrap(client)
    flag = state["flag"]
    payload = {
        "environment_id": state["environment_id"],
        "expected_version": 1,
        "enabled": False,
        "default_variation": "disabled",
        "variations": flag["variations"],
        "rules": flag["rules"],
        "rollout": flag["rollout"],
        "progressive_stages": [],
    }

    async def write() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as writer:
            return await writer.put(
                f"/api/v1/flags/{flag['id']}", headers=state["auth"], json=payload
            )

    first, second = await asyncio.gather(write(), write())
    assert sorted([first.status_code, second.status_code]) == [200, 409]


@pytest.mark.asyncio
async def test_redis_read_failure_falls_back_to_postgres(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = await bootstrap(client)
    await redis_client.flushdb()

    async def unavailable(*args: object, **kwargs: object) -> None:
        raise RedisConnectionError("test outage")

    monkeypatch.setattr(redis_client, "get", unavailable)
    response = await client.post(
        "/api/v1/evaluate",
        headers={"X-CohortSwitch-Key": state["sdk_key"]},
        json={"flag_key": "new_checkout", "subject_key": "user_7", "attributes": {}},
    )
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_python_sdk_works_against_asgi_api(client: httpx.AsyncClient) -> None:
    state = await bootstrap(client)
    sdk_http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers={"X-CohortSwitch-Key": state["sdk_key"]},
    )
    sdk = CohortSwitchClient(
        base_url="http://testserver",
        sdk_key=state["sdk_key"],
        http_client=sdk_http,
    )
    result = await sdk.evaluate(
        "new_checkout",
        subject_key="sdk-user",
        attributes={"country": "KZ", "plan": "PRO"},
    )
    assert result.value is True
    assert result.matched_rule == "pro-kz"
    await sdk_http.aclose()
