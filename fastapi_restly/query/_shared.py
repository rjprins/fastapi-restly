def _escape_like_value(value: str) -> str:
    """Escape SQL LIKE wildcard characters for literal substring matching."""
    escaped = value.replace("\\", "\\\\")
    escaped = escaped.replace("%", "\\%")
    escaped = escaped.replace("_", "\\_")
    return escaped
