import typing as _typing

from ._base import (
    CASCADE_ALL_ASYNC,
    CASCADE_ALL_DELETE_ORPHAN_ASYNC,
    IDMixin,
    ModelDefaults,
    TimestampsMixin,
)
from ._documents import JSONDocument

# Public API for ``fastapi_restly.models``.
__all__ = [
    "CASCADE_ALL_ASYNC",
    "CASCADE_ALL_DELETE_ORPHAN_ASYNC",
    "IDMixin",
    "JSONDocument",
    "ModelDefaults",
    "TimestampsMixin",
]

if not _typing.TYPE_CHECKING:
    # Hidden from type checkers, which report an unknown name themselves.

    def __getattr__(name: str):
        from ._base import _REMOVED_BASES

        if name in _REMOVED_BASES:
            raise AttributeError(_REMOVED_BASES[name])
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
