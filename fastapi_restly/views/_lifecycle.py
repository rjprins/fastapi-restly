"""The canonical write lifecycle.

Every write runs one invariant sequence -- ``authorize`` -> ``snapshot`` -> the
domain mutation -> ``before_commit`` -> commit -> ``after_commit``. This module
puts that sequence in exactly one place. Two entry shapes share it:

* ``write_action`` on the view -- an (async) context manager for custom write
  *actions* (publish, change-password, ...): you mutate inline, and for a
  create-shaped action you deposit the new object on the yielded handle's
  ``.obj``. This is the user-facing tool.
* ``run_write_action`` / ``async_run_write_action`` -- the free-function thunk
  form the built-in CRUD handlers delegate to, and usable off the HTTP path
  against any *host* that supplies the hooks. They wrap the context managers, so
  there is exactly one implementation of the sequence.
"""

import contextlib
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, TypeVar

T = TypeVar("T")


class _WriteHandle:
    """Yielded by the ``write_action`` context manager. ``obj`` defaults to the
    object passed in (mutate it in place); reassign it for a create-shaped action
    where the object does not exist until the body runs. It is what the commit
    hooks see as ``new`` and what you read after the block.
    """

    __slots__ = ("obj",)

    def __init__(self, obj: Any) -> None:
        self.obj = obj


class AsyncWriteHost(Protocol):
    """The hooks the async write lifecycle drives; async views satisfy it."""

    async def authorize(self, action: str, obj: Any = None, data: Any = None) -> None: ...
    def snapshot(self, obj: Any) -> dict[str, Any]: ...
    async def before_commit(self, action: str, new: Any, old: Any = None) -> None: ...
    async def after_commit(self, action: str, new: Any, old: Any = None) -> None: ...
    async def _commit(self) -> None: ...


class WriteHost(Protocol):
    """Sync counterpart of :class:`AsyncWriteHost`; sync views satisfy it."""

    def authorize(self, action: str, obj: Any = None, data: Any = None) -> None: ...
    def snapshot(self, obj: Any) -> dict[str, Any]: ...
    def before_commit(self, action: str, new: Any, old: Any = None) -> None: ...
    def after_commit(self, action: str, new: Any, old: Any = None) -> None: ...
    def _commit(self) -> None: ...


@contextlib.asynccontextmanager
async def async_write_action(
    host: AsyncWriteHost, action: str, *, obj: Any = None, data: Any = None
):
    """The async write bracket as a context manager.

    ``__aenter__`` runs ``authorize`` + ``snapshot`` and yields a handle; your
    body mutates (in place, or sets ``handle.obj`` for a create); on a clean exit
    it runs ``before_commit`` -> commit -> ``after_commit``. A raise in the body
    skips the commit and propagates (the session dependency rolls back).
    """
    await host.authorize(action, obj=obj, data=data)
    old = host.snapshot(obj) if obj is not None else None
    handle = _WriteHandle(obj)
    yield handle
    await host.before_commit(action, new=handle.obj, old=old)
    await host._commit()
    await host.after_commit(action, new=handle.obj, old=old)


@contextlib.contextmanager
def sync_write_action(
    host: WriteHost, action: str, *, obj: Any = None, data: Any = None
):
    """Sync variant of :func:`async_write_action`."""
    host.authorize(action, obj=obj, data=data)
    old = host.snapshot(obj) if obj is not None else None
    handle = _WriteHandle(obj)
    yield handle
    host.before_commit(action, new=handle.obj, old=old)
    host._commit()
    host.after_commit(action, new=handle.obj, old=old)


async def async_run_write_action(
    host: AsyncWriteHost,
    action: str,
    *,
    obj: Any = None,
    data: Any = None,
    mutate: Callable[[], Awaitable[T]],
) -> T:
    """Thunk form of the async write bracket: run ``mutate`` inside it and return
    its result. The built-in CRUD handlers use this; also usable off the HTTP
    path against any ``host`` that supplies the hooks.
    """
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
