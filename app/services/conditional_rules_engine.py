from collections.abc import Mapping
from typing import Any


class ConditionalRulesEngine:
    def evaluate(self, condition: Mapping[str, Any] | None, context: Mapping[str, Any]) -> bool:
        if not condition:
            return True
        if "all" in condition:
            return all(self.evaluate(item, context) for item in condition["all"])
        if "any" in condition:
            return any(self.evaluate(item, context) for item in condition["any"])
        if "not" in condition:
            return not self.evaluate(condition["not"], context)

        field = condition.get("field")
        operator = condition.get("operator")
        expected = condition.get("value")
        actual = _resolve_field(context, str(field)) if field else None

        if operator == "equals":
            return actual == expected
        if operator == "not_equals":
            return actual != expected
        if operator == "in":
            return actual in (expected or [])
        if operator == "not_in":
            return actual not in (expected or [])
        if operator == "exists":
            return _exists(actual)
        if operator == "not_exists":
            return not _exists(actual)
        if operator == "contains":
            return _contains(actual, expected)
        if operator in {"gt", "gte", "lt", "lte"}:
            return _compare(actual, expected, operator)
        return False


def _resolve_field(context: Mapping[str, Any], field: str) -> Any:
    current: Any = context
    for part in field.split("."):
        if isinstance(current, Mapping):
            current = current.get(part)
            continue
        return None
    return current


def _exists(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) > 0
    return True


def _contains(actual: Any, expected: Any) -> bool:
    if actual is None:
        return False
    if isinstance(actual, str):
        return str(expected).lower() in actual.lower()
    if isinstance(actual, (list, tuple, set)):
        return expected in actual
    return False


def _compare(actual: Any, expected: Any, operator: str) -> bool:
    try:
        actual_value = float(actual)
        expected_value = float(expected)
    except (TypeError, ValueError):
        return False
    if operator == "gt":
        return actual_value > expected_value
    if operator == "gte":
        return actual_value >= expected_value
    if operator == "lt":
        return actual_value < expected_value
    return actual_value <= expected_value
