"""JSON path utilities for navigating and modifying UMM metadata."""

from typing import Any

from jsonpath_ng import parse


def get_value_at_path(metadata: dict[str, Any], path: str) -> Any:
    """Get the value at a JSON path in the metadata, or None if not found."""
    matches = parse(path).find(metadata)
    return matches[0].value if matches else None


def set_value_at_path(metadata: dict[str, Any], path: str, value: Any) -> bool:
    """
    Set a value at a JSON path in the metadata (in place).

    Creates intermediate keys if they don't exist.
    """
    parse(path).update_or_create(metadata, value)
    return True


def remove_value_at_path(metadata: dict[str, Any], path: str) -> tuple[bool, Any]:
    """
    Remove a value at a JSON path (in place).

    Returns (success, removed_value).
    """
    expr = parse(path)
    matches = expr.find(metadata)
    if not matches:
        return False, None

    removed = matches[0].value
    # jsonpath_ng's filter() deletes matched nodes when the predicate returns True.
    # lambda _: True means "delete every node matched by this path expression."
    expr.filter(lambda _: True, metadata)
    return True, removed
