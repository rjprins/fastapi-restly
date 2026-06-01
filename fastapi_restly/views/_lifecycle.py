"""Shared write lifecycle.

Every write follows the same sequence: authorize, snapshot, mutate,
before_commit, commit, after_commit. ``write_action`` exposes the sequence as a
context manager for custom actions. ``run_write_action`` and
``async_run_write_action`` are the thunk form used by CRUD handlers.
"""

import contextlib
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, TypeVar

T = TypeVar("T")

#: Sentinel for "no ``obj`` was passed"; that marks create-shaped actions.
_UNSET: Any = object()


class _WriteHandle:
    """Handle yielded by ``write_action``.

    For in-place writes, ``obj`` is the row passed to the context manager. For
    create-shaped writes, callers must assign the created object to ``w.obj``.
    Commit hooks receive this value as ``new``.
    """

    __slots__ = ("obj",)

    def __init__(self, obj: Any) -> None:
        self.obj = obj


def _require_deposited_obj(action: str, handle: _WriteHandle) -> None:
    """Raise when a create-shaped block exits without setting ``handle.obj``."""
    if handle.obj is _UNSET:
        raise RuntimeError(
            f"write_action({action!r}) is create-shaped (no obj= was passed) but "
            "the block never set handle.obj. Assign the new object "
            "(`w.obj = <object>`) inside the block; pass obj=<row> for an in-place "
            "write, or obj=None for an explicit no-object write."
        )


class AsyncWriteHost(Protocol):
    """Async host interface for the write lifecycle."""

    session: Any

    async def authorize(
        self, action: str, obj: Any = None, data: Any = None
    ) -> None: ...
    def snapshot(self, obj: Any) -> dict[str, Any]: ...
    async def before_commit(self, action: str, new: Any, old: Any = None) -> None: ...
    async def after_commit(self, action: str, new: Any, old: Any = None) -> None: ...


class WriteHost(Protocol):
    """Sync host interface for the write lifecycle."""

    session: Any

    def authorize(self, action: str, obj: Any = None, data: Any = None) -> None: ...
    def snapshot(self, obj: Any) -> dict[str, Any]: ...
    def before_commit(self, action: str, new: Any, old: Any = None) -> None: ...
    def after_commit(self, action: str, new: Any, old: Any = None) -> None: ...


@contextlib.asynccontextmanager
async def async_write_action(
    host: AsyncWriteHost, action: str, *, obj: Any = _UNSET, data: Any = None
):
    """Async write bracket.

    ``obj=<row>`` means an in-place write. ``obj=None`` means a no-object write.
    Omitting ``obj`` means create-shaped; the block must set ``handle.obj``.
    """
    passed = obj is not _UNSET
    await host.authorize(action, obj=obj if passed else None, data=data)
    old = host.snapshot(obj) if (passed and obj is not None) else None
    handle = _WriteHandle(obj)
    yield handle
    _require_deposited_obj(action, handle)
    await host.before_commit(action, new=handle.obj, old=old)
    await host.session.commit()
    await host.after_commit(action, new=handle.obj, old=old)


@contextlib.contextmanager
def sync_write_action(
    host: WriteHost, action: str, *, obj: Any = _UNSET, data: Any = None
):
    """Sync variant of :func:`async_write_action`."""
    passed = obj is not _UNSET
    host.authorize(action, obj=obj if passed else None, data=data)
    old = host.snapshot(obj) if (passed and obj is not None) else None
    handle = _WriteHandle(obj)
    yield handle
    _require_deposited_obj(action, handle)
    host.before_commit(action, new=handle.obj, old=old)
    host.session.commit()
    host.after_commit(action, new=handle.obj, old=old)


async def async_run_write_action(
    host: AsyncWriteHost,
    action: str,
    *,
    obj: Any = None,
    data: Any = None,
    mutate: Callable[[], Awaitable[T]],
) -> T:
    """Run ``mutate`` inside the async write bracket and return its result."""
    async with async_write_action(host, action, obj=obj, data=data) as w:
        w.obj = await mutate()
    return w.obj


def run_write_action(
    host: WriteHost,
    action: str,
    *,
    obj: Any = None,
    data: Any = None,
    mutate: Callable[[], T],
) -> T:
    """Sync variant of :func:`async_run_write_action`."""
    with sync_write_action(host, action, obj=obj, data=data) as w:
        w.obj = mutate()
    return w.obj
