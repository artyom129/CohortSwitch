import pytest

from backend.app.evaluation.operators import MISSING, condition_matches


@pytest.mark.parametrize(
    ("operator", "actual", "expected", "result"),
    [
        ("equals", "PRO", "PRO", True),
        ("not_equals", "FREE", "PRO", True),
        ("in", "KZ", ["KZ", "US"], True),
        ("not_in", "FR", ["KZ", "US"], True),
        ("contains", ["beta", "staff"], "staff", True),
        ("contains", "hello world", "world", True),
        ("starts_with", "user@example.com", "user", True),
        ("ends_with", "user@example.com", "example.com", True),
        ("exists", 0, None, True),
        ("not_exists", MISSING, None, True),
        ("greater_than", 11, 10, True),
        ("greater_or_equal", "10", 10, True),
        ("less_than", 9, 10, True),
        ("less_or_equal", 10, 10, True),
        ("semver_equal", "2.4.1", "2.4.1", True),
        ("semver_greater", "2.10.0", "2.9.9", True),
        ("semver_less", "1.9.0", "2.0.0", True),
        ("semver_greater", "not-a-version", "2.0.0", False),
        ("equals", MISSING, None, False),
    ],
)
def test_operators(operator: str, actual: object, expected: object, result: bool) -> None:
    assert condition_matches(operator, actual, expected) is result


def test_unknown_operator_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported operator"):
        condition_matches("unknown", "a", "a")


def test_in_means_list_membership_not_substring() -> None:
    assert condition_matches("in", "us", ["us", "ca"]) is True
    assert condition_matches("in", "us", "russia") is False
    assert condition_matches("not_in", "us", "russia") is False
    assert condition_matches("in", ["a"], [["a"]]) is True
    assert condition_matches("in", {"a": 1}, ["x"]) is False
