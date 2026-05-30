"""The canonical write lifecycle, reified as a self-free function.

Every write request runs one invariant sequence -- ``authorize`` -> ``snapshot``
-> the domain mutation -> ``before_commit`` -> commit -> ``after_commit``. This
module puts that sequence in exactly one place: the built-in write handlers and
any custom action go through it, so they cannot drift apart, and it runs against
any *host* that supplies the hooks (normally the view, but anything implementing
them -- e.g. a background-job context -- so the lifecycle is not bound to an HTTP
request).

The view exposes the bound, overridable entry point ``handle_write``; these
free functions are the underlying mechanism, mirroring how ``save_object`` on
the view sits over ``async_save_object`` in :mod:`fastapi_restly.objects`.
"""

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, TypeVar

T = TypeVar("T")


class AsyncWriteHost(Protocol):
    """The hooks :func:`async_run_write_action` drives; async views satisfy it."""

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


async def async_run_write_action(
    host: AsyncWriteHost,
    action: str,
    *,
    obj: Any = None,
    data: Any = None,
    mutate: Callable[[], Awaitable[T]],
) -> T:
    """Run ``mutate`` inside the full write bracket and return its result.

    The invariant sequence: ``authorize`` -> ``snapshot`` (only when ``obj`` is
    given) -> ``mutate`` -> ``before_commit`` -> commit -> ``after_commit``.
    ``host`` supplies the hooks -- normally the view (``self``).
    """
    await host.authorize(action, obj=obj, data=data)
    old = host.snapshot(obj) if obj is not None else None
    new = await mutate()
    await host.before_commit(action, new=new, old=old)
    await host._commit()
    await host.after_commit(action, new=new, old=old)
    return new


def run_write_action(
    host: WriteHost,
    action: str,
    *,
    obj: Any = None,
    data: Any = None,
    mutate: Callable[[], T],
) -> T:
    """Sync variant of :func:`async_run_write_action`."""
    host.authorize(action, obj=obj, data=data)
    old = host.snapshot(obj) if obj is not None else None
    new = mutate()
    host.before_commit(action, new=new, old=old)
    host._commit()
    host.after_commit(action, new=new, old=old)
    return new
