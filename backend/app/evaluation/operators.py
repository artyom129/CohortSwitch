from collections.abc import Callable, Container
from typing import Any

from packaging.version import InvalidVersion, Version

MISSING = object()


def _equals(actual: Any, expected: Any) -> bool:
    return actual is not MISSING and actual == expected


def _not_equals(actual: Any, expected: Any) -> bool:
    return actual is not MISSING and actual != expected


def _in(actual: Any, expected: Any) -> bool:
    return actual is not MISSING and isinstance(expected, Container) and actual in expected


def _not_in(actual: Any, expected: Any) -> bool:
    return actual is not MISSING and isinstance(expected, Container) and actual not in expected


def _contains(actual: Any, expected: Any) -> bool:
    return actual is not MISSING and isinstance(actual, Container) and expected in actual


def _starts_with(actual: Any, expected: Any) -> bool:
    return isinstance(actual, str) and isinstance(expected, str) and actual.startswith(expected)


def _ends_with(actual: Any, expected: Any) -> bool:
    return isinstance(actual, str) and isinstance(expected, str) and actual.endswith(expected)


def _exists(actual: Any, expected: Any) -> bool:
    return actual is not MISSING


def _not_exists(actual: Any, expected: Any) -> bool:
    return actual is MISSING


def _numeric_compare(actual: Any, expected: Any, operation: Callable[[float, float], bool]) -> bool:
    if actual is MISSING or isinstance(actual, bool) or isinstance(expected, bool):
        return False
    try:
        return operation(float(actual), float(expected))
    except (TypeError, ValueError):
        return False


def _semver_compare(
    actual: Any, expected: Any, operation: Callable[[Version, Version], bool]
) -> bool:
    if not isinstance(actual, str) or not isinstance(expected, str):
        return False
    try:
        return operation(Version(actual), Version(expected))
    except InvalidVersion:
        return False


OPERATORS: dict[str, Callable[[Any, Any], bool]] = {
    "equals": _equals,
    "not_equals": _not_equals,
    "in": _in,
    "not_in": _not_in,
    "contains": _contains,
    "starts_with": _starts_with,
    "ends_with": _ends_with,
    "exists": _exists,
    "not_exists": _not_exists,
    "greater_than": lambda actual, expected: _numeric_compare(
        actual, expected, lambda left, right: left > right
    ),
    "greater_or_equal": lambda actual, expected: _numeric_compare(
        actual, expected, lambda left, right: left >= right
    ),
    "less_than": lambda actual, expected: _numeric_compare(
        actual, expected, lambda left, right: left < right
    ),
    "less_or_equal": lambda actual, expected: _numeric_compare(
        actual, expected, lambda left, right: left <= right
    ),
    "semver_equal": lambda actual, expected: _semver_compare(
        actual, expected, lambda left, right: left == right
    ),
    "semver_greater": lambda actual, expected: _semver_compare(
        actual, expected, lambda left, right: left > right
    ),
    "semver_less": lambda actual, expected: _semver_compare(
        actual, expected, lambda left, right: left < right
    ),
}


def condition_matches(operator: str, actual: Any, expected: Any) -> bool:
    try:
        operation = OPERATORS[operator]
    except KeyError as exc:
        raise ValueError(f"Unsupported operator: {operator}") from exc
    return operation(actual, expected)
