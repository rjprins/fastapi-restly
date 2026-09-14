"""Shared write lifecycle.

Every write follows the same sequence: authorize, snapshot, mutate,
before_action_commit, commit, after_action_commit. ``write_action`` exposes
the commit bracket as a context manager for custom actions. ``run_write_action``
and ``async_run_write_action`` are the thunk form used by CRUD handlers.
``shared_write_action_commit`` moves the commit and after-hooks to an outer block.
"""

import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Protocol, TypeVar

from sqlalchemy import event
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, SessionTransaction

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
    async def before_action_commit(
        self, action: str, new: Any, old: Any = None
    ) -> None: ...
    async def after_action_commit(
        self, action: str, new: Any, old: Any = None
    ) -> None: ...


class WriteHost(Protocol):
    """Sync host interface for the write lifecycle."""

    session: Any

    def authorize(self, action: str, obj: Any = None, data: Any = None) -> None: ...
    def snapshot(self, obj: Any) -> dict[str, Any]: ...
    def before_action_commit(self, action: str, new: Any, old: Any = None) -> None: ...
    def after_action_commit(self, action: str, new: Any, old: Any = None) -> None: ...


@contextlib.asynccontextmanager
async def async_write_action(
    host: AsyncWriteHost, action: str, *, obj: Any = _UNSET, data: Any = None
):
    """Async write bracket.

    ``obj=<row>`` means an in-place write. ``obj=None`` means a no-object write.
    Omitting ``obj`` means create-shaped; the block must set ``handle.obj``.
    """
    owner = _shared_write_action_commit_owner(host.session)
    if owner is not None:
        owner.require_active(asynchronous=True)
    passed = obj is not _UNSET
    await host.authorize(action, obj=obj if passed else None, data=data)
    old = host.snapshot(obj) if (passed and obj is not None) else None
    handle = _WriteHandle(obj)
    yield handle
    _require_deposited_obj(action, handle)
    await host.before_action_commit(action, new=handle.obj, old=old)
    owner = _shared_write_action_commit_owner(host.session)
    if owner is None:
        await host.session.commit()
        await host.after_action_commit(action, new=handle.obj, old=old)
    else:
        await host.session.flush()
        owner.enqueue(
            host.session.sync_session, host, action, handle.obj, old, asynchronous=True
        )


@contextlib.contextmanager
def sync_write_action(
    host: WriteHost, action: str, *, obj: Any = _UNSET, data: Any = None
):
    """Sync variant of :func:`async_write_action`."""
    owner = _shared_write_action_commit_owner(host.session)
    if owner is not None:
        owner.require_active()
    passed = obj is not _UNSET
    host.authorize(action, obj=obj if passed else None, data=data)
    old = host.snapshot(obj) if (passed and obj is not None) else None
    handle = _WriteHandle(obj)
    yield handle
    _require_deposited_obj(action, handle)
    host.before_action_commit(action, new=handle.obj, old=old)
    owner = _shared_write_action_commit_owner(host.session)
    if owner is None:
        host.session.commit()
        host.after_action_commit(action, new=handle.obj, old=old)
    else:
        host.session.flush()
        owner.enqueue(host.session, host, action, handle.obj, old)


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


@contextlib.asynccontextmanager
async def _async_shared_write_action_commit(
    session: AsyncSession,
) -> AsyncIterator[None]:
    """Commit once after the outermost block, then await its surviving hooks."""
    pending: tuple[_QueuedAfterHook, ...] = ()
    with _join_shared_write_action_commit(
        session.sync_session, asynchronous=True
    ) as owner:
        yield
        if owner.depth == 1:
            owner.require_active()
            await _async_commit_shared_write_actions(session, owner)
            pending = tuple(owner.pending)

    # Drop the owner before hooks run, so no hook can join a finished commit.
    for entry in pending:
        if entry.asynchronous:
            await entry.callback()
        else:
            # A sync action may have joined through AsyncSession.run_sync().
            # Its hook needs that same bridge for any SQLAlchemy reads.
            await session.run_sync(lambda _: entry.callback())


@contextlib.contextmanager
def _shared_write_action_commit(session: Session) -> Iterator[None]:
    """Sync counterpart of :func:`_async_shared_write_action_commit`."""
    pending: tuple[_QueuedAfterHook, ...] = ()
    with _join_shared_write_action_commit(session) as owner:
        yield
        if owner.depth == 1:
            owner.require_active()
            _commit_shared_write_actions(session, owner)
            pending = tuple(owner.pending)

    for entry in pending:
        entry.callback()


_SHARED_WRITE_ACTION_COMMIT_KEY = "_fr_shared_write_action_commit"


@dataclass
class _QueuedAfterHook:
    callback: Callable[[], Any]
    transaction: SessionTransaction | None
    asynchronous: bool
    new: Any
    loaded_attributes: frozenset[str]


@dataclass
class _SharedWriteActionCommit:
    asynchronous: bool = False
    depth: int = 0
    aborted: bool = False
    committing: bool = False
    pending: list[_QueuedAfterHook] = field(default_factory=list)

    def require_active(self, *, asynchronous: bool = False) -> None:
        if self.aborted:
            raise RuntimeError(
                "shared_write_action_commit() was aborted. "
                "The session owner must roll back before retrying the operation."
            )
        if asynchronous and not self.asynchronous:
            raise RuntimeError(
                "Async write actions require an async outermost "
                "shared_write_action_commit() block."
            )

    def abort(self) -> None:
        self.aborted = True
        self.pending.clear()

    def enqueue(
        self,
        session: Session,
        host: AsyncWriteHost | WriteHost,
        action: str,
        new: Any,
        old: Any,
        *,
        asynchronous: bool = False,
    ) -> None:
        self.require_active(asynchronous=asynchronous)
        self.pending.append(
            _QueuedAfterHook(
                callback=partial(host.after_action_commit, action, new=new, old=old),
                transaction=(
                    session.get_nested_transaction() or session.get_transaction()
                ),
                asynchronous=asynchronous,
                new=new,
                loaded_attributes=_loaded_orm_attributes(new),
            )
        )


def _loaded_orm_attributes(obj: Any) -> frozenset[str]:
    state = sa_inspect(obj, raiseerr=False)
    if state is None:
        return frozenset()
    return frozenset(state.dict).intersection(state.mapper.attrs.keys())


def _expired_hook_attributes(entry: _QueuedAfterHook) -> frozenset[str]:
    state = sa_inspect(entry.new, raiseerr=False)
    if state is None or not state.persistent:
        return frozenset()
    return entry.loaded_attributes.intersection(state.expired_attributes)


async def _async_commit_shared_write_actions(
    session: AsyncSession, owner: _SharedWriteActionCommit
) -> None:
    for entry in owner.pending:
        expired = _expired_hook_attributes(entry)
        if expired:
            await session.refresh(entry.new, attribute_names=expired)
    owner.committing = True
    try:
        await session.commit()
    finally:
        owner.committing = False


def _commit_shared_write_actions(
    session: Session, owner: _SharedWriteActionCommit
) -> None:
    for entry in owner.pending:
        expired = _expired_hook_attributes(entry)
        if expired:
            session.refresh(entry.new, attribute_names=expired)
    owner.committing = True
    try:
        session.commit()
    finally:
        owner.committing = False


def _shared_write_action_commit_owner(
    session: AsyncSession | Session,
) -> _SharedWriteActionCommit | None:
    return session.info.get(_SHARED_WRITE_ACTION_COMMIT_KEY)


@contextlib.contextmanager
def _join_shared_write_action_commit(
    session: Session, *, asynchronous: bool = False
) -> Iterator[_SharedWriteActionCommit]:
    owner = _shared_write_action_commit_owner(session)
    if owner is None:
        owner = _SharedWriteActionCommit(asynchronous=asynchronous)
        event.listen(session, "after_soft_rollback", _discard_rolled_back_hooks)
        event.listen(session, "after_transaction_end", _abort_ended_transaction)
        event.listen(session, "before_commit", _reject_direct_commit)
        session.info[_SHARED_WRITE_ACTION_COMMIT_KEY] = owner
    owner.depth += 1
    try:
        owner.require_active(asynchronous=asynchronous)
        yield owner
    except BaseException:
        owner.abort()
        raise
    finally:
        owner.depth -= 1
        if owner.depth == 0:
            session.info.pop(_SHARED_WRITE_ACTION_COMMIT_KEY, None)
            event.remove(session, "after_soft_rollback", _discard_rolled_back_hooks)
            event.remove(session, "after_transaction_end", _abort_ended_transaction)
            event.remove(session, "before_commit", _reject_direct_commit)
            owner.pending.clear()


def _reject_direct_commit(session: Session) -> None:
    owner = _shared_write_action_commit_owner(session)
    if owner is None or owner.committing or session.in_nested_transaction():
        return
    owner.abort()
    raise RuntimeError(
        "session.commit() cannot be called inside shared_write_action_commit(). "
        "The outermost block owns the commit."
    )


def _abort_ended_transaction(session: Session, transaction: SessionTransaction) -> None:
    owner = _shared_write_action_commit_owner(session)
    if owner is not None and not owner.committing and transaction.parent is None:
        owner.abort()


def _discard_rolled_back_hooks(
    session: Session, transaction: SessionTransaction
) -> None:
    """Observe caller-owned rollbacks, without running any hooks or SQL.

    SQLAlchemy reports both nested and root rollbacks through this event:
    https://docs.sqlalchemy.org/en/20/orm/events.html#sqlalchemy.orm.SessionEvents.after_soft_rollback
    """
    owner = _shared_write_action_commit_owner(session)
    if owner is None:
        return
    # A failed flush reports an internal marker under the actual rollback.
    while transaction.parent is not None and not transaction.nested:
        transaction = transaction.parent
    if transaction.parent is None:
        owner.abort()
        return
    owner.pending[:] = [
        entry
        for entry in owner.pending
        if not _belongs_to_transaction(entry.transaction, transaction)
    ]


def _belongs_to_transaction(
    transaction: SessionTransaction | None, ancestor: SessionTransaction
) -> bool:
    while transaction is not None:
        if transaction is ancestor:
            return True
        transaction = transaction.parent
    return False
