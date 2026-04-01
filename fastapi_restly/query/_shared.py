import types
from typing import Any, Union, get_args, get_origin


def _escape_like_value(value: str) -> str:
    """Escape SQL LIKE wildcard characters for literal substring matching."""
    escaped = value.replace("\\", "\\\\")
    escaped = escaped.replace("%", "\\%")
    escaped = escaped.replace("_", "\\_")
    return escaped


def _unwrap_optional_annotation(annotation: Any) -> Any:
    """Unwrap Optional[X] or X | None to X. Returns annotation unchanged otherwise."""
    origin = get_origin(annotation)
    if origin not in (types.UnionType, Union):
        return annotation

    non_none_args = [arg for arg in get_args(annotation) if arg is not type(None)]
    if len(non_none_args) == 1:
        return non_none_args[0]
    return annotation
