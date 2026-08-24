from types import SimpleNamespace

import pytest

from backend.app.scheduler import _apply_payload


@pytest.mark.parametrize(
    ("percentage", "expected"),
    [
        (0, [("disabled", 10_000)]),
        (500, [("enabled", 500), ("disabled", 9500)]),
        (10_000, [("enabled", 10_000)]),
    ],
)
def test_scheduled_rollout_compiles_complete_allocations(
    percentage: int, expected: list[tuple[str, int]]
) -> None:
    change = SimpleNamespace(
        change_type="rollout_percentage",
        payload={
            "percentage_basis_points": percentage,
            "variation": "enabled",
            "fallback_variation": "disabled",
        },
    )
    payload = SimpleNamespace(rollout=[], progressive_stages=[])
    audit_action, event = _apply_payload(change, payload)
    assert [(item.variation, item.percentage_basis_points) for item in payload.rollout] == expected
    assert audit_action == event == "rollout.changed"


def test_scheduled_percentage_rejects_invalid_range() -> None:
    change = SimpleNamespace(
        change_type="rollout_percentage",
        payload={
            "percentage_basis_points": 10_001,
            "variation": "enabled",
            "fallback_variation": "disabled",
        },
    )
    with pytest.raises(ValueError, match="between 0 and 10000"):
        _apply_payload(change, SimpleNamespace(rollout=[], progressive_stages=[]))
