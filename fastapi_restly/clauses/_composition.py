"""Boolean composition and bundling for query clauses."""

from __future__ import annotations

from typing import cast, overload

from sqlalchemy import ColumnElement, and_, false, not_, or_

from ._declarations import where_clause
from ._runtime import (
    UNSCOPED,
    Unscoped,
    WhereClause,
    _resolve_carried_where,
    _without_unscoped,
)

__all__ = ["all_of", "any_of", "none_of"]


def _require_wheres(
    name: str, clauses: tuple[WhereClause | Unscoped, ...]
) -> tuple[WhereClause, ...]:
    if not clauses:
        raise TypeError(f"{name}() requires at least one clause")
    given = _without_unscoped(name, clauses)
    # a ContextParam carries a value, not a predicate
    if any(not isinstance(c, WhereClause) for c in given):
        raise TypeError(f"{name} combines only clauses with a where")
    return cast("tuple[WhereClause, ...]", given)


@overload
def all_of(*clauses: WhereClause) -> WhereClause: ...


@overload
def all_of(first: WhereClause, /, *clauses: WhereClause | Unscoped) -> WhereClause: ...


@overload
def all_of(
    first: WhereClause | Unscoped,
    second: WhereClause,
    /,
    *clauses: WhereClause | Unscoped,
) -> WhereClause: ...


@overload
def all_of(*clauses: Unscoped) -> Unscoped: ...


@overload
def all_of(*clauses: WhereClause | Unscoped) -> WhereClause | Unscoped: ...


def all_of(*clauses: WhereClause | Unscoped) -> WhereClause | Unscoped:
    """AND the operands' predicates.

    UNSCOPED is a no-op. One remaining operand is returned unchanged, and
    none returns UNSCOPED. Calling with no arguments raises.
    """
    given = _require_wheres("all_of", clauses)
    if not given:
        return UNSCOPED
    if len(given) == 1:
        return given[0]

    def fn() -> ColumnElement[bool]:
        return and_(*(_resolve_carried_where(c) for c in given))

    fn.__name__ = fn.__qualname__ = "all_of"
    result = where_clause(fn)
    result._children = given
    return result


@overload
def any_of(*clauses: WhereClause) -> WhereClause: ...


@overload
def any_of(*clauses: Unscoped) -> Unscoped: ...


@overload
def any_of(*clauses: WhereClause | Unscoped) -> WhereClause | Unscoped: ...


def any_of(*clauses: WhereClause | Unscoped) -> WhereClause | Unscoped:
    """OR the operands' predicates.

    UNSCOPED accepts every row, so any UNSCOPED operand returns the sentinel.
    Other operands are validated before this simplification and are not
    resolved if the result is UNSCOPED. Calling with no arguments raises.
    """
    given = _require_wheres("any_of", clauses)
    if len(given) != len(clauses):
        return UNSCOPED
    fn = lambda: or_(*(_resolve_carried_where(c) for c in given))  # noqa: E731
    fn.__name__ = fn.__qualname__ = "any_of"
    result = where_clause(fn)
    result._children = given
    return result


def none_of(*clauses: WhereClause | Unscoped) -> WhereClause:
    """True when none of the operands' predicates hold.

    NOT over the OR of all operands. An UNSCOPED operand returns a
    WhereClause over SQL false(), matching no rows. Other operands are
    validated but not resolved in that case.
    """
    given = _require_wheres("none_of", clauses)
    if len(given) != len(clauses):
        return where_clause(false())
    fn = lambda: not_(or_(*(_resolve_carried_where(c) for c in given)))  # noqa: E731
    fn.__name__ = fn.__qualname__ = "none_of"
    result = where_clause(fn)
    result._children = given
    return result
