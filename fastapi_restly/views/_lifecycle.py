"""Shared write lifecycle: the commit bracket.

Every write runs the same steps: ``authorize``, the reference check of the
data, the snapshot, the change, the save, ``before_action_commit``, the
commit and ``after_action_commit``. The change is the business method of a
CRUD handler, or the block of a ``write_action``. It changes objects in the
session and writes nothing: it runs under ``session.no_autoflush``, on sync
sessions too. The save after it writes them.

The subject is the one row that the action is about: the ``obj`` of a
``write_action``, the row that ``handle_update`` or ``handle_delete`` loads,
or the object that ``create`` or ``update`` returns. Its SQLAlchemy state
decides the rest:

- a row in the database: ``authorize`` gets it, and ``old`` is its snapshot;
- a new object: ``authorize`` gets ``obj=None``, ``old`` is ``None``, and the
  bracket adds it to the session;
- ``None``: no single row.

The save flushes the session. Then it refreshes the subject and every other
row of the view's model that the action wrote, and loads the relationships
of the response on them. ``new`` is the subject after the save, or ``None``
when its row is gone (a hard delete) or when there is no subject.

The outermost bracket on a session owns the commit and the after-hooks: a
bracket inside it joins it, as inside ``shared_write_action_commit``.
"""

import contextlib
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from functools import partial
from itertools import chain
from typing import Any, Protocol

from sqlalchemy import event
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import MANYTOONE, InstanceState, Session, SessionTransaction
from sqlalchemy.orm.collections import collection_adapter

from .._integrity import after_commit, translate_integrity_errors
from ..objects import _async_refresh_saved, _refresh_saved
from ..schemas._base import (
    _async_check_body_references,
    _check_body_references,
    _remember_checked_references,
)
from ._base import _check_action_name


class AsyncWriteHost(Protocol):
    """Async host interface for the write lifecycle.

    ``model`` is the model whose written rows the save refreshes.
    ``_get_reload_statements`` are the SELECTs that load what the response
    needs on the saved subject and on the other saved rows of that model.
    """

    session: Any
    model: Any

    def _get_reload_statements(self, subject: Any, others: list[Any]) -> list[Any]: ...
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
    """Sync host interface for the write lifecycle; see :class:`AsyncWriteHost`."""

    session: Any
    model: Any

    def _get_reload_statements(self, subject: Any, others: list[Any]) -> list[Any]: ...
    def authorize(self, action: str, obj: Any = None, data: Any = None) -> None: ...
    def snapshot(self, obj: Any) -> dict[str, Any]: ...
    def before_action_commit(self, action: str, new: Any, old: Any = None) -> None: ...
    def after_action_commit(self, action: str, new: Any, old: Any = None) -> None: ...


class _Bracket:
    """One write action, inside the bracket.

    Private. A CRUD handler sets ``subject`` to the object that its business
    method returns, because ``create`` builds the object only after
    ``authorize`` has run. A ``write_action`` block gets nothing: its subject
    is the ``obj`` that it was given.
    """

    __slots__ = ("subject",)

    def __init__(self, subject: Any) -> None:
        self.subject = subject


def _check_subject(session: Any, action: str, obj: Any) -> None:
    """Raise ``TypeError`` when ``obj`` is neither ``None`` nor an object of a
    model that this session can write."""
    if obj is None:
        return
    state = _mapped_state(obj)
    if state is None:
        raise TypeError(
            f"write_action({action!r}, obj=...) takes the row that the action is "
            "about, a new object that it creates, or None. "
            f"{type(obj).__name__} is not an object of a SQLAlchemy model. "
            "For an action on several rows, pass obj=None."
        )
    sync_session: Session = getattr(session, "sync_session", session)
    if state.session_id is not None and state.session_id != sync_session.hash_key:
        raise TypeError(
            f"write_action({action!r}, obj=...): the {type(obj).__name__} belongs "
            "to another session, so this session cannot write it. Load the row "
            "in this session, for example with get_one."
        )
    if state.detached and state.key in sync_session.identity_map:
        raise TypeError(
            f"write_action({action!r}, obj=...): this session already has the "
            f"{type(obj).__name__} with this key, as another object. Pass that "
            "object, for example the one from get_one."
        )


def _require_result(host: Any, method: str, result: Any) -> Any:
    """``result`` of a business method, or ``TypeError`` when it is ``None``.

    The handler saves what the method returns. Without the check, a forgotten
    ``return`` would commit the change and give the hooks ``new=None``, the
    shape of a delete.
    """
    if result is None or _mapped_state(result) is None:
        shown = "None" if result is None else f"a {type(result).__name__}"
        raise TypeError(
            f"{type(host).__name__}.{method}() returned {shown}. Return the "
            f"object that the action writes, as super().{method}() does."
        )
    return result


def _mapped_state(obj: Any) -> InstanceState[Any] | None:
    """The SQLAlchemy state of ``obj``, or ``None`` for ``None`` and for a
    value that is not an object of a model (a list, a Pydantic model)."""
    if obj is None:
        return None
    state = sa_inspect(obj, raiseerr=False)
    return state if isinstance(state, InstanceState) else None


def _row_in_database(obj: Any) -> Any:
    """``obj`` when it is a row that the database has, else ``None``.

    A new object (transient or pending) has no identity yet. ``authorize``
    gets ``obj=None`` for it, as on ``POST /``, and it has no ``old``.
    """
    state = _mapped_state(obj)
    return obj if state is not None and state.has_identity else None


def _add_to_session(session: Any, subject: Any) -> None:
    """Add a new subject to the session, so the save inserts it, and a
    detached one, so the save writes its change."""
    state = _mapped_state(subject)
    if state is not None and (state.transient or state.detached):
        session.add(subject)


def _new(subject: Any) -> Any:
    """The subject after the save: ``None`` when the flush removed its row."""
    state = _mapped_state(subject)
    if state is None:
        return subject  # None, or a value that is not an ORM object
    return subject if state.persistent else None


_WRITTEN_ROWS_KEY = "_fr_written_rows"


class _WrittenRows:
    """The rows of the view's model that the flushes of one action write.

    The save refreshes them, so a route that changes several rows of its
    model in one action returns each of them as the database has it. Rows of
    other models are flushed, but not refreshed. Only their relationships
    whose foreign key changed are unloaded, so a response that nests them
    never shows the old related row.
    """

    def __init__(self, model: Any) -> None:
        self.model = model if isinstance(model, type) else None
        self.rows: dict[int, Any] = {}
        #: A flush ran while the action was open, so it wrote something.
        self.flushed = False
        #: A statement that is not a SELECT ran while the action was open,
        #: such as ``session.execute(update(...))``.
        self.executed = False
        #: What was pending in the session when the change began.
        self.before: _Pending | None = None
        #: The many-to-one relationships, by row, that a changed foreign key
        #: left holding the old related row.
        self.stale: dict[int, tuple[Any, set[str]]] = {}

    def others(self, subject: Any) -> list[Any]:
        """The written rows, other than the subject, that are still rows."""
        return [
            row
            for row in self.rows.values()
            if row is not subject and sa_inspect(row).persistent
        ]


@contextlib.contextmanager
def _record_written_rows(session: Session, model: Any) -> Iterator[_WrittenRows]:
    """Record the rows of ``model`` that the flushes write until the block
    ends. The session's ``after_flush`` listener fills the record."""
    written = _WrittenRows(model)
    recorders: list[_WrittenRows] = session.info.setdefault(_WRITTEN_ROWS_KEY, [])
    recorders.append(written)
    try:
        yield written
    finally:
        recorders.remove(written)
        if not recorders:
            session.info.pop(_WRITTEN_ROWS_KEY, None)


def _remember_written_rows(session: Session, flush_context: Any) -> None:
    """Give each running write action the rows of its model that a flush writes."""
    recorders: list[_WrittenRows] | None = session.info.get(_WRITTEN_ROWS_KEY)
    if not recorders:
        return
    # after_flush still lists the objects of the flush as new and dirty.
    # session.new builds a new set on each access, so read it once.
    for written in recorders:
        written.flushed = True
    new = session.new
    for obj in chain(new, session.dirty):
        if obj not in new and not session.is_modified(obj):
            continue
        stale = [] if obj in new else _stale_relationships(obj)
        for written in recorders:
            if written.model is not None and isinstance(obj, written.model):
                written.rows.setdefault(id(obj), obj)
            if stale:
                written.stale.setdefault(id(obj), (obj, set()))[1].update(stale)


def _stale_relationships(obj: Any) -> list[str]:
    """The many-to-one relationships of ``obj`` that still hold the old
    related row: the flush changes their foreign key, but the code set the
    column, not the relationship. Read in ``after_flush``, while the history
    of the flush is still there."""
    state = sa_inspect(obj)
    stale = []
    for rel in state.mapper.relationships:
        if rel.direction is not MANYTOONE or rel.key in state.unloaded:
            continue
        if state.attrs[rel.key].history.has_changes():
            continue
        columns = [
            attr.key
            for attr in state.mapper.column_attrs
            if set(attr.columns) & rel.local_columns
        ]
        if any(state.attrs[key].history.has_changes() for key in columns):
            stale.append(rel.key)
    return stale


def _expire_stale(session: Session, written: _WrittenRows, saved: list[Any]) -> None:
    """Unload the stale relationships of the written rows of other models.
    The rows in ``saved`` are refreshed already."""
    skip = {id(row) for row in saved}
    for key, (obj, names) in written.stale.items():
        if key not in skip and sa_inspect(obj).persistent:
            session.expire(obj, list(names))


def _remember_written_statements(orm_execute_state: Any) -> None:
    """Tell each running write action that a statement may have written.

    A statement such as ``session.execute(update(...))`` writes at once,
    without a flush, so the refused-write rule must see it too.
    """
    if orm_execute_state.is_select:
        return
    recorders: list[_WrittenRows] | None = orm_execute_state.session.info.get(
        _WRITTEN_ROWS_KEY
    )
    for written in recorders or ():
        written.executed = True


@dataclass(frozen=True)
class _Pending:
    """The ids of the objects with a change that a flush would write."""

    new: frozenset[int]
    deleted: frozenset[int]
    modified: frozenset[int]

    @classmethod
    def of(cls, session: Session) -> "_Pending":
        return cls(
            frozenset(map(id, session.new)),
            frozenset(map(id, session.deleted)),
            frozenset(id(obj) for obj in session.dirty if session.is_modified(obj)),
        )

    def grew(self, now: "_Pending") -> bool:
        """True when ``now`` has a change that this did not have.

        A second change to an object that was already changed is not seen.
        """
        return not (
            now.new <= self.new
            and now.deleted <= self.deleted
            and now.modified <= self.modified
        )


async def _async_save(host: AsyncWriteHost, subject: Any, written: _WrittenRows) -> Any:
    """The save step: flush, then make the subject and the view's other
    written rows ready for the hooks and the response. Returns ``new``.

    The subject gets the refresh of ``fr.objects.save_object``. Then one
    reload, with the view's loader options, covers the subject and the other
    rows.
    """
    session = host.session
    _add_to_session(session, subject)
    await session.flush()
    state = _mapped_state(subject)
    if state is not None and state.persistent:
        await _async_refresh_saved(session, subject)
    others = written.others(subject)
    for row in others:
        _expire_relationships(row)
    _expire_stale(session.sync_session, written, [subject, *others])
    for stmt in host._get_reload_statements(subject, others):
        # unique(): the loader-options seam is public and may return a
        # joinedload against a collection, which fans the row set out.
        (await session.scalars(stmt)).unique().all()
    return _new(subject)


def _save(host: WriteHost, subject: Any, written: _WrittenRows) -> Any:
    """Sync variant of :func:`_async_save`."""
    session = host.session
    _add_to_session(session, subject)
    session.flush()
    state = _mapped_state(subject)
    if state is not None and state.persistent:
        _refresh_saved(session, subject)
    others = written.others(subject)
    for row in others:
        _expire_relationships(row)
    _expire_stale(session, written, [subject, *others])
    for stmt in host._get_reload_statements(subject, others):
        session.scalars(stmt).unique().all()
    return _new(subject)


def _expire_relationships(row: Any) -> None:
    """Expire the relationships of a saved row, so the reload reads them
    again: a changed foreign key then never shows the old related row."""
    state = sa_inspect(row)
    keys = [rel.key for rel in state.mapper.relationships]
    # expire(row, []) would expire every attribute
    if keys:
        state.session.expire(row, keys)


@contextlib.contextmanager
def _change(session: Any, subject: Any) -> Iterator[None]:
    """The window of the change: a business method or a ``write_action`` block.

    Nothing is written until the save after it: the window runs under
    ``session.no_autoflush``, so a query in it does not flush a half-built
    object, on a sync session too. A new subject is added to the session
    when the window opens, so a flush in the block inserts it.
    """
    with session.no_autoflush:
        _add_to_session(session, subject)
        yield


@contextlib.asynccontextmanager
async def _async_bracket(
    host: AsyncWriteHost, action: str, obj: Any, data: Any
) -> AsyncIterator[_Bracket]:
    """The async commit bracket around one write action.

    The outermost bracket on a session opens the shared commit, so it owns
    the commit and the after-hooks. A bracket inside it joins it.
    """
    commit = (
        _async_shared_write_action_commit(host.session)
        if _shared_write_action_commit_owner(host.session) is None
        else contextlib.nullcontext()
    )
    async with commit, _async_joined_bracket(host, action, obj, data) as bracket:
        yield bracket


@contextlib.asynccontextmanager
async def _async_joined_bracket(
    host: AsyncWriteHost, action: str, obj: Any, data: Any
) -> AsyncIterator[_Bracket]:
    """One action inside a shared commit: everything up to the flush."""
    session = host.session
    owner = _require_shared_write_action_commit(session)
    owner.require_active(asynchronous=True)
    row = _row_in_database(obj)
    await host.authorize(action, obj=row, data=data)
    checked = await _async_check_body_references(session, data)
    old = host.snapshot(row) if row is not None else None
    bracket = _Bracket(obj)
    with _record_written_rows(session.sync_session, host.model) as written:
        written.before = _Pending.of(session.sync_session)
        try:
            with _remember_checked_references(session, checked), _change(session, obj):
                yield bracket
            new = await _async_save(host, bracket.subject, written)
            await host.before_action_commit(action, new=new, old=old)
            await session.flush()
        except BaseException:
            owner.refuse(session.sync_session, written)
            raise
    owner.enqueue(session.sync_session, host, action, new, old, asynchronous=True)


@contextlib.contextmanager
def _bracket(host: WriteHost, action: str, obj: Any, data: Any) -> Iterator[_Bracket]:
    """Sync variant of :func:`_async_bracket`."""
    commit = (
        _shared_write_action_commit(host.session)
        if _shared_write_action_commit_owner(host.session) is None
        else contextlib.nullcontext()
    )
    with commit, _joined_bracket(host, action, obj, data) as bracket:
        yield bracket


@contextlib.contextmanager
def _joined_bracket(
    host: WriteHost, action: str, obj: Any, data: Any
) -> Iterator[_Bracket]:
    """Sync variant of :func:`_async_joined_bracket`."""
    session = host.session
    owner = _require_shared_write_action_commit(session)
    owner.require_active()
    row = _row_in_database(obj)
    host.authorize(action, obj=row, data=data)
    checked = _check_body_references(session, data)
    old = host.snapshot(row) if row is not None else None
    bracket = _Bracket(obj)
    with _record_written_rows(session, host.model) as written:
        written.before = _Pending.of(session)
        try:
            with _remember_checked_references(session, checked), _change(session, obj):
                yield bracket
            new = _save(host, bracket.subject, written)
            host.before_action_commit(action, new=new, old=old)
            session.flush()
        except BaseException:
            owner.refuse(session, written)
            raise
    owner.enqueue(session, host, action, new, old)


@contextlib.asynccontextmanager
async def async_write_action(
    host: AsyncWriteHost, action: str, *, obj: Any, data: Any = None
) -> AsyncIterator[None]:
    """The commit bracket of ``AsyncRestView.write_action``. The block gets
    nothing from ``as``."""
    _check_action_name(host, action)
    _check_subject(host.session, action, obj)
    async with _async_bracket(host, action, obj, data):
        yield


@contextlib.contextmanager
def sync_write_action(
    host: WriteHost, action: str, *, obj: Any, data: Any = None
) -> Iterator[None]:
    """The commit bracket of ``RestView.write_action``."""
    _check_action_name(host, action)
    _check_subject(host.session, action, obj)
    with _bracket(host, action, obj, data):
        yield


@contextlib.asynccontextmanager
async def _async_shared_write_action_commit(
    session: AsyncSession,
) -> AsyncIterator[None]:
    """Commit once after the outermost block, then await its surviving hooks.

    The outermost block turns an ``IntegrityError`` that escapes it, or that
    its commit raises, into :class:`~fastapi_restly.exc.Conflict`.
    """
    pending: tuple[_QueuedAfterHook, ...] = ()
    outermost = _shared_write_action_commit_owner(session.sync_session) is None
    with (
        translate_integrity_errors(outermost=outermost),
        _join_shared_write_action_commit(
            session.sync_session, asynchronous=True
        ) as owner,
    ):
        yield
        if owner.depth == 1:
            owner.require_active()
            await _async_commit_shared_write_actions(session, owner)
            pending = tuple(owner.pending)

    # Drop the owner before hooks run, so no hook can join a finished commit.
    with after_commit():
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
    outermost = _shared_write_action_commit_owner(session) is None
    with (
        translate_integrity_errors(outermost=outermost),
        _join_shared_write_action_commit(session) as owner,
    ):
        yield
        if owner.depth == 1:
            owner.require_active()
            _commit_shared_write_actions(session, owner)
            pending = tuple(owner.pending)

    with after_commit():
        for entry in pending:
            entry.callback()


_SHARED_WRITE_ACTION_COMMIT_KEY = "_fr_shared_write_action_commit"


@dataclass
class _QueuedAfterHook:
    callback: Callable[[], Any]
    transaction: SessionTransaction | None
    asynchronous: bool
    loaded_objects: list[tuple[Any, frozenset[str]]]


@dataclass
class _SharedWriteActionCommit:
    asynchronous: bool = False
    depth: int = 0
    aborted: bool = False
    committing: bool = False
    pending: list[_QueuedAfterHook] = field(default_factory=list)
    #: The transaction of each action that raised and left a change behind.
    refused: list[SessionTransaction | None] = field(default_factory=list)

    def require_active(self, *, asynchronous: bool = False) -> None:
        if self.aborted:
            raise RuntimeError(
                "The write was aborted: an exception left a write_action() or "
                "shared_write_action_commit() block, or a rollback ended its "
                "transaction. The session owner must roll back before retrying "
                "the operation."
            )
        if asynchronous and not self.asynchronous:
            raise RuntimeError(
                "An async write action cannot join the commit of a sync "
                "write_action() or shared_write_action_commit() block."
            )

    def abort(self) -> None:
        self.aborted = True
        self.pending.clear()

    def refuse(self, session: Session, written: _WrittenRows) -> None:
        """Remember an action that raised after its change began.

        The error may be caught inside the shared commit, as a bulk route
        catches the error of one row. The change of the action may then be
        half done, so the commit refuses to run, unless the rollback of a
        savepoint around the action undid it. An action that raised before
        it changed anything is not remembered. The changes that were pending
        before it, such as those of an outer block, do not count.
        """
        transaction = session.get_nested_transaction() or session.get_transaction()
        if transaction is not None and not transaction.is_active:
            return  # a failed flush already rolled the change back
        changed = written.before is not None and written.before.grew(
            _Pending.of(session)
        )
        if written.flushed or written.executed or changed:
            self.refused.append(transaction)

    def require_unrefused(self) -> None:
        if self.refused:
            self.abort()
            raise RuntimeError(
                "The write was not committed: a write action raised after it "
                "changed objects, and the error was caught inside the "
                "write_action() or shared_write_action_commit() block, so the "
                "change may be half done. Run each write that may raise in "
                "session.begin_nested(): the rollback of the savepoint then "
                "undoes its change."
            )

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
                loaded_objects=_loaded_orm_graph(new),
            )
        )


async def _async_commit_shared_write_actions(
    session: AsyncSession, owner: _SharedWriteActionCommit
) -> None:
    owner.require_unrefused()
    for obj, attributes in _objects_to_refresh(owner):
        await session.refresh(obj, attribute_names=attributes)
    owner.committing = True
    try:
        await session.commit()
    finally:
        owner.committing = False


def _commit_shared_write_actions(
    session: Session, owner: _SharedWriteActionCommit
) -> None:
    owner.require_unrefused()
    for obj, attributes in _objects_to_refresh(owner):
        session.refresh(obj, attribute_names=attributes)
    owner.committing = True
    try:
        session.commit()
    finally:
        owner.committing = False


def _objects_to_refresh(
    owner: _SharedWriteActionCommit,
) -> Iterator[tuple[Any, set[str]]]:
    loaded: dict[int, tuple[Any, set[str]]] = {}
    for entry in owner.pending:
        for obj, attributes in entry.loaded_objects:
            loaded.setdefault(id(obj), (obj, set()))[1].update(attributes)
    for obj, attributes in loaded.values():
        state = sa_inspect(obj)
        if state.persistent:
            # A column refresh can clear expired_attributes while leaving
            # relationships unloaded. Recover those recorded relationships too.
            missing = attributes.intersection(state.unloaded)
            if missing:
                yield obj, missing


def _loaded_orm_graph(obj: Any) -> list[tuple[Any, frozenset[str]]]:
    """Record loaded attributes without invoking relationship loaders.

    Keep related objects alive: rollback can remove their last relationship
    reference. Each action owns its record so a rolled-back action drops it.
    """
    loaded = []
    pending = [obj]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        state = sa_inspect(current, raiseerr=False)
        if state is None:
            continue
        attributes = set(state.dict).intersection(state.mapper.attrs.keys())
        for relation in state.mapper.relationships:
            if relation.key not in attributes:
                continue
            if relation.lazy in ("dynamic", "write_only"):
                attributes.remove(relation.key)
                continue
            value = state.dict[relation.key]
            if relation.uselist:
                pending.extend(collection_adapter(value))
            elif value is not None:
                pending.append(value)
        loaded.append((current, frozenset(attributes)))
    return loaded


def _shared_write_action_commit_owner(
    session: AsyncSession | Session,
) -> _SharedWriteActionCommit | None:
    return session.info.get(_SHARED_WRITE_ACTION_COMMIT_KEY)


def _require_shared_write_action_commit(
    session: AsyncSession | Session,
) -> _SharedWriteActionCommit:
    owner = _shared_write_action_commit_owner(session)
    if owner is None:
        raise RuntimeError("a write action runs only inside a shared commit")
    return owner


@contextlib.contextmanager
def _join_shared_write_action_commit(
    session: Session, *, asynchronous: bool = False
) -> Iterator[_SharedWriteActionCommit]:
    owner = _shared_write_action_commit_owner(session)
    if owner is None:
        owner = _SharedWriteActionCommit(asynchronous=asynchronous)
        _listen_once(session)
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
            owner.pending.clear()
            owner.refused.clear()


def _listen_once(session: Session) -> None:
    # every write opens a shared commit, so the listeners stay on the
    # session; they do nothing while no block is open
    if event.contains(session, "before_commit", _reject_direct_commit):
        return
    event.listen(session, "after_soft_rollback", _discard_rolled_back_hooks)
    event.listen(session, "after_transaction_end", _abort_ended_transaction)
    event.listen(session, "before_commit", _reject_direct_commit)
    event.listen(session, "after_flush", _remember_written_rows)
    event.listen(session, "do_orm_execute", _remember_written_statements)


def _reject_direct_commit(session: Session) -> None:
    owner = _shared_write_action_commit_owner(session)
    if owner is None or owner.committing or session.in_nested_transaction():
        return
    owner.abort()
    raise RuntimeError(
        "session.commit() cannot be called inside a write_action() or "
        "shared_write_action_commit() block. The outermost block owns the commit."
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
    owner.refused[:] = [
        refused
        for refused in owner.refused
        if not _belongs_to_transaction(refused, transaction)
    ]


def _belongs_to_transaction(
    transaction: SessionTransaction | None, ancestor: SessionTransaction
) -> bool:
    while transaction is not None:
        if transaction is ancestor:
            return True
        transaction = transaction.parent
    return False
