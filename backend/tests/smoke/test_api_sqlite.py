from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from backend.app.core.redis import redis_client
from backend.app.database import Base
from backend.app.database.session import get_session
from backend.app.main import app


class FakePipeline:
    def __init__(self, redis: "FakeRedis") -> None:
        self.redis = redis
        self.operations: list[tuple[str, str, Any]] = []

    def set(self, key: str, value: Any, **kwargs: Any) -> "FakePipeline":
        self.operations.append(("set", key, value))
        return self

    async def execute(self) -> list[bool]:
        for _, key, value in self.operations:
            self.redis.values[key] = value
        return [True] * len(self.operations)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    async def incr(self, key: str) -> int:
        value = int(self.values.get(key, 0)) + 1
        self.values[key] = value
        return value

    async def expire(self, key: str, seconds: int) -> bool:
        return True

    async def get(self, key: str) -> Any:
        return self.values.get(key)

    async def set(self, key: str, value: Any, **kwargs: Any) -> bool:
        self.values[key] = value
        return True

    async def mget(self, keys: list[str]) -> list[Any]:
        return [self.values.get(key) for key in keys]

    def pipeline(self, transaction: bool = False) -> FakePipeline:
        return FakePipeline(self)

    async def delete(self, key: str) -> int:
        return int(self.values.pop(key, None) is not None)

    async def publish(self, channel: str, message: str) -> int:
        return 0

    async def ping(self) -> bool:
        return True


@pytest.fixture
async def smoke_client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
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
    app.dependency_overrides[get_session] = override_session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        yield client
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_end_to_end_control_and_evaluation_path(smoke_client: httpx.AsyncClient) -> None:
    registration = await smoke_client.post(
        "/api/v1/auth/register",
        json={"email": "owner@example.com", "password": "correct-horse-battery-staple"},
    )
    assert registration.status_code == 201, registration.text
    auth = {"Authorization": f"Bearer {registration.json()['access_token']}"}

    organization = await smoke_client.post(
        "/api/v1/organizations",
        headers=auth,
        json={"name": "Example", "slug": "example-org"},
    )
    assert organization.status_code == 201, organization.text
    organization_id = organization.json()["id"]

    project = await smoke_client.post(
        "/api/v1/projects",
        headers=auth,
        json={"organization_id": organization_id, "name": "Backend", "slug": "backend"},
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]

    environment = await smoke_client.post(
        f"/api/v1/projects/{project_id}/environments",
        headers=auth,
        json={"key": "production", "name": "Production"},
    )
    assert environment.status_code == 201, environment.text
    environment_id = environment.json()["id"]

    flag = await smoke_client.post(
        f"/api/v1/projects/{project_id}/flags",
        headers=auth,
        json={
            "environment_id": environment_id,
            "key": "new_checkout",
            "name": "New checkout",
            "flag_type": "BOOLEAN",
            "enabled": True,
            "default_variation": "disabled",
            "variations": [
                {"key": "enabled", "value": True},
                {"key": "disabled", "value": False},
            ],
            "rules": [
                {
                    "name": "pro-users",
                    "priority": 0,
                    "conditions": [{"attribute": "plan", "operator": "equals", "value": "PRO"}],
                    "variation": "enabled",
                }
            ],
        },
    )
    assert flag.status_code == 201, flag.text

    key = await smoke_client.post(
        "/api/v1/sdk-keys",
        headers=auth,
        json={
            "name": "production",
            "organization_id": organization_id,
            "project_id": project_id,
            "environment_id": environment_id,
        },
    )
    assert key.status_code == 201, key.text
    sdk_headers = {"X-CohortSwitch-Key": key.json()["key"]}

    evaluation = await smoke_client.post(
        "/api/v1/evaluate",
        headers=sdk_headers,
        json={
            "flag_key": "new_checkout",
            "subject_key": "user_42",
            "attributes": {"plan": "PRO"},
        },
    )
    assert evaluation.status_code == 200, evaluation.text
    assert evaluation.json()["value"] is True
    assert evaluation.json()["matched_rule"] == "pro-users"

    snapshot_url = f"/api/v1/projects/{project_id}/environments/{environment_id}/config"
    snapshot = await smoke_client.get(snapshot_url, headers=sdk_headers)
    assert snapshot.status_code == 200
    assert snapshot.headers["etag"] == '"1"'
    unchanged = await smoke_client.get(
        snapshot_url,
        headers={**sdk_headers, "If-None-Match": snapshot.headers["etag"]},
    )
    assert unchanged.status_code == 304
