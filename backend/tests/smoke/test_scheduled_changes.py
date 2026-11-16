from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from backend.app import scheduler
from backend.app.core.redis import redis_client
from backend.app.database import Base
from backend.app.database.models import FlagConfig, ScheduledChange
from backend.app.database.session import get_session
from backend.app.main import app
from backend.tests.smoke.test_api_sqlite import FakeRedis


@pytest.fixture
async def stack(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[httpx.AsyncClient, Any]]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def override_session() -> AsyncIterator[Any]:
        async with factory() as session:
            yield session

    fake = FakeRedis()
    for method in ("incr", "expire", "get", "set", "mget", "pipeline", "delete", "publish", "ping"):
        monkeypatch.setattr(redis_client, method, getattr(fake, method))
    monkeypatch.setattr(scheduler, "SessionFactory", factory)
    app.dependency_overrides[get_session] = override_session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        yield client, factory
    app.dependency_overrides.clear()
    await engine.dispose()


async def _flag(client: httpx.AsyncClient) -> tuple[dict[str, str], str, str]:
    registration = await client.post(
        "/api/v1/auth/register",
        json={"email": "planner@example.com", "password": "correct-horse-battery-staple"},
    )
    auth = {"Authorization": f"Bearer {registration.json()['access_token']}"}
    organization = await client.post(
        "/api/v1/organizations", headers=auth, json={"name": "Plan", "slug": "plan-org"}
    )
    project = await client.post(
        "/api/v1/projects",
        headers=auth,
        json={"organization_id": organization.json()["id"], "name": "Api", "slug": "api"},
    )
    project_id = project.json()["id"]
    environment = await client.post(
        f"/api/v1/projects/{project_id}/environments",
        headers=auth,
        json={"key": "production", "name": "Production"},
    )
    environment_id = environment.json()["id"]
    flag = await client.post(
        f"/api/v1/projects/{project_id}/flags",
        headers=auth,
        json={
            "environment_id": environment_id,
            "key": "dark_mode",
            "name": "Dark mode",
            "flag_type": "BOOLEAN",
            "enabled": False,
            "default_variation": "off",
            "variations": [{"key": "on", "value": True}, {"key": "off", "value": False}],
        },
    )
    assert flag.status_code == 201, flag.text
    return auth, flag.json()["id"], environment_id


def _when(minutes: int = -1) -> str:
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()


@pytest.mark.asyncio
async def test_scheduled_change_payload_is_checked_when_created(stack: Any) -> None:
    client, _ = stack
    auth, flag_id, environment_id = await _flag(client)
    url = f"/api/v1/flags/{flag_id}/scheduled-changes"
    base = {"environment_id": environment_id, "expected_version": 1, "activate_at": _when(60)}

    missing = await client.post(
        url, headers=auth, json={**base, "change_type": "rollout_percentage", "payload": {}}
    )
    assert missing.status_code == 422

    unknown = await client.post(
        url,
        headers=auth,
        json={**base, "change_type": "variation", "payload": {"variation": "purple"}},
    )
    assert unknown.status_code == 422
    assert unknown.json()["error"]["variations"] == ["purple"]

    naive = await client.post(
        url,
        headers=auth,
        json={**base, "change_type": "enable", "activate_at": "2030-01-01T10:00:00"},
    )
    assert naive.status_code == 422

    ok = await client.post(url, headers=auth, json={**base, "change_type": "enable"})
    assert ok.status_code == 201, ok.text


@pytest.mark.asyncio
async def test_one_broken_change_does_not_block_the_others(stack: Any) -> None:
    client, factory = stack
    auth, flag_id, environment_id = await _flag(client)

    # A row written before validation existed: its payload cannot be applied.
    async with factory() as session, session.begin():
        config = await session.scalar(select(FlagConfig).where(FlagConfig.flag_id == UUID(flag_id)))
        session.add(
            ScheduledChange(
                config_id=config.id,
                change_type="rollout_percentage",
                payload={},
                activate_at=datetime.now(UTC) - timedelta(minutes=5),
            )
        )

    enable = await client.post(
        f"/api/v1/flags/{flag_id}/scheduled-changes",
        headers=auth,
        json={
            "environment_id": environment_id,
            "expected_version": 1,
            "change_type": "enable",
            "activate_at": _when(-1),
        },
    )
    assert enable.status_code == 201, enable.text

    assert await scheduler.apply_due_changes() == 1
    assert await scheduler.apply_due_changes() == 0  # the broken one is not retried forever

    listed = await client.get(
        f"/api/v1/flags/{flag_id}/scheduled-changes",
        headers=auth,
        params={"environment_id": environment_id},
    )
    assert listed.status_code == 200, listed.text
    by_type = {item["change_type"]: item for item in listed.json()}
    assert by_type["enable"]["applied_at"] is not None
    assert by_type["rollout_percentage"]["failed_at"] is not None
    assert "percentage_basis_points" in by_type["rollout_percentage"]["last_error"]

    flag = await client.get(
        f"/api/v1/flags/{flag_id}", headers=auth, params={"environment_id": environment_id}
    )
    assert flag.json()["enabled"] is True
