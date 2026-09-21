"""Runtime representation and statement application for query clauses."""

from __future__ import annotations

from typing import Any, Callable, NoReturn, Sequence, TypeVar, final, overload

from sqlalchemy import ColumnElement, Delete, Select, Update
from sqlalchemy.sql.expression import (
    BindParameter,
    ColumnClause,
    Join,
    ScalarSelect,
    SelectBase,
    Subquery,
)
from sqlalchemy.sql.visitors import ExternallyTraversible, iterate

from ._context import ContextParam


class WhereClause:
    """A named, reusable predicate.

    Built by where_clause() and by all_of()/any_of()/none_of(), never
    constructed directly. It takes no arguments and holds no values: a
    value that changes per request or per call is a ContextNamespace
    member, embedded in the condition or read in the clause function. It
    applies to SELECT, UPDATE and DELETE statements through
    apply_clauses(), and calling it returns the raw ColumnElement for use
    inside plain SQLAlchemy expressions.
    """

    _build: Callable[[], ColumnElement[bool]]
    _label: str

    def __init__(self) -> None:
        raise TypeError(
            "build a clause with where_clause() or all_of()/any_of()/none_of()"
        )

    @classmethod
    def _of(cls, build: Callable[[], ColumnElement[bool]], label: str) -> WhereClause:
        # the construction path around the teaching __init__
        self = cls.__new__(cls)
        self._build = build
        self._label = label
        return self

    def __call__(self) -> ColumnElement[bool]:
        """Resolve to the raw ColumnElement, for plain SQLAlchemy use.

        Context members are read now: an unbound one raises LookupError.
        The result drops into any expression position: .where(), a join
        condition, a CASE. This path skips apply_clauses' table validation.
        """
        return self._resolve(None)

    def _resolve(self, owners: dict[str, object] | None) -> ColumnElement[bool]:
        return _fill_slots(self._build(), owners)

    def __bool__(self) -> NoReturn:
        # `if item.is_deleted:` would otherwise always be True: a clause
        # is a query fragment, not an answer
        raise TypeError("a clause is not a boolean; apply it to a query instead")

    def __repr__(self) -> str:
        return f"<WhereClause {self._label}>"


@final
class Unscoped:
    """Sentinel scope: explicitly no scope, everywhere a scope can appear.

    `UNSCOPED` is the one instance; the class is public so a `scope`
    declaration or a `get_one` / `get_many` override can name the type
    (`WhereClause | Unscoped`).
    The explicit spelling for a view's `scope` (where `None` means
    "fall back to the model's default"), a namespace's `default_scope`
    (where undeclared defers to
    a base model's namespace, or none), and a `RefExists` scope (where
    `None` is rejected, so a variable that happens to be None can never
    silently unscope). `apply_clauses()` accepts it and applies nothing
    for it, preserving existing filters and other supplied clauses.
    Boolean composition treats it as SQL TRUE: all_of ignores it and
    returns the sole remaining clause unchanged, or UNSCOPED when none
    remain. any_of propagates it, and none_of returns a WhereClause over
    SQL false(). Composition validates other operands before simplifying.
    Searching for UNSCOPED finds explicit uses. Variables and composition
    can pass the sentinel to other scope declarations and read calls.
    Deliberately not re-exported at the top level: the escape is spelled
    in full.
    """

    def __repr__(self) -> str:
        return "fr.clauses.UNSCOPED"


UNSCOPED = Unscoped()


_HANDWRITTEN = object()  # owners-entry for a user-written bindparam


def _fill_slots(
    expr: ColumnElement[bool], owners: dict[str, object] | None = None
) -> ColumnElement[bool]:
    """Replace embedded member placeholders with their bound values.

    `owners` maps bindparam key -> owning member and is shared across all
    clauses of one apply_clauses call: SQLAlchemy compiles binds by key,
    so two owners under one key would silently let the last value win.
    One name, one owner.
    """
    if owners is None:
        owners = {}
    values: dict[str, object] = {}

    def claim(key: str, owner: object) -> None:
        existing = owners.setdefault(key, owner)
        if existing is owner:
            return
        # by identity: == on a member raises
        if existing is _HANDWRITTEN or owner is _HANDWRITTEN:
            raise TypeError(
                f"the ContextParam named {key!r} collides with a hand-written "
                f"bindparam({key!r}) in the same statement; rename one"
            )
        raise TypeError(
            f"two different ContextParams named {key!r} appear in one "
            "statement; share one member or rename one"
        )

    stack: list = [expr]
    while stack:
        el = stack.pop()
        member: ContextParam[Any] | None = getattr(el, "_fr_param", None)
        if member is not None:
            key = member._placeholder.key
            claim(key, member)
            if key not in values:
                values[key] = member()
            continue
        if isinstance(el, BindParameter) and not el.unique:
            claim(el.key, _HANDWRITTEN)
        stack.extend(el.get_children())
    return expr.params(values) if values else expr


def _without_unscoped(
    name: str, clauses: tuple[WhereClause | Unscoped, ...]
) -> tuple[WhereClause, ...]:
    given: list[WhereClause] = []
    for clause in clauses:
        if clause is UNSCOPED:
            continue
        if not isinstance(clause, WhereClause):
            hint = (
                "; a ContextParam carries a value: embed it in an expression"
                if isinstance(clause, ContextParam)
                else ""
            )
            raise TypeError(f"{name}() accepts only clauses or UNSCOPED{hint}")
        given.append(clause)
    return tuple(given)


def _seed_owners(stmt: ExternallyTraversible) -> dict[str, object]:
    # SQLAlchemy's bind-key space is per statement, so ownership must be
    # too: binds the statement already carries (earlier apply_clauses
    # layers, hand-written bindparams) claim their keys before any
    # clause of this call fills a placeholder
    owners: dict[str, object] = {}
    for el in iterate(stmt):
        if isinstance(el, BindParameter) and not el.unique:
            member = getattr(el, "_fr_param", None)
            owners.setdefault(el.key, member if member is not None else _HANDWRITTEN)
    return owners


def _display_table_name(key: str) -> str:
    # strip the metadata-identity prefix _table_name adds for membership
    head, sep, tail = key.partition(":")
    return tail if sep and head.isdigit() else key


def _table_name(table) -> str:
    # include metadata identity where available: two same-named tables from
    # different registries must not satisfy each other in the validation
    name = str(getattr(table, "key", None) or getattr(table, "name", table))
    metadata = getattr(table, "metadata", None)
    return f"{id(metadata)}:{name}" if metadata is not None else name


def _expression_tables(expr: ColumnElement) -> set[str]:
    # tables the expression references at its outer level; self-contained
    # subqueries (EXISTS, IN (SELECT ...)) bring their own FROMs and are
    # skipped
    names: set[str] = set()
    stack: list = [expr]
    while stack:
        el = stack.pop()
        if isinstance(el, (SelectBase, ScalarSelect, Subquery)):
            continue
        if isinstance(el, ColumnClause):
            if el.table is not None:
                names.add(_table_name(el.table))
            continue
        stack.extend(el.get_children())
    return names


def _statement_tables(stmt: Select[Any] | Update | Delete) -> set[str]:
    froms: list = (
        list(stmt.get_final_froms()) if isinstance(stmt, Select) else [stmt.table]
    )
    names: set[str] = set()
    while froms:
        el = froms.pop()
        if isinstance(el, Join):
            froms.extend([el.left, el.right])
        else:
            names.add(_table_name(el))
    return names


_SelectT = TypeVar("_SelectT", bound=Select[Any])


@overload
def apply_clauses(stmt: _SelectT, /, *clauses: WhereClause | Unscoped) -> _SelectT: ...
@overload
def apply_clauses(stmt: Update, /, *clauses: WhereClause | Unscoped) -> Update: ...
@overload
def apply_clauses(stmt: Delete, /, *clauses: WhereClause | Unscoped) -> Delete: ...
def apply_clauses(stmt, /, *clauses: WhereClause | Unscoped):
    """Apply clauses to a statement built with plain SQLAlchemy.

    The bridge between the two worlds: build select()/update()/delete()
    as usual, then let this add the clauses' predicates to its WHERE. A
    predicate that references a table the statement does not select from
    is rejected: the silent alternative is a cartesian product. Express a
    condition on a related table as EXISTS (.any()/.has()). ``UNSCOPED``
    among the clauses applies nothing: a scope seam passes on what it was
    given.
    """
    given = _without_unscoped("apply_clauses", clauses)
    owners = _seed_owners(stmt)
    wheres = [clause._resolve(owners) for clause in given]
    _guard_statement_tables(stmt, wheres)
    return stmt.where(*wheres)


def _guard_statement_tables(
    stmt: Select[Any] | Update | Delete, wheres: Sequence[ColumnElement[bool]]
) -> None:
    available = _statement_tables(stmt)
    for where in wheres:
        missing = _expression_tables(where) - available
        if missing:
            shown = sorted(_display_table_name(key) for key in missing)
            raise TypeError(
                "clause references table(s) not in the statement: "
                + ", ".join(shown)
                + "; express the condition as EXISTS (.any()/.has())"
            )
