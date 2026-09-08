"""Boolean composition and bundling for query clauses."""

from __future__ import annotations

from typing import overload

from sqlalchemy import ColumnElement, and_, false, not_, or_

from .._contextargs import contextual
from ._runtime import (
    UNSCOPED,
    Clause,
    CombinedClause,
    ContextParam,
    Unscoped,
    WhereClause,
    _has_transform,
    _require_no_transforms,
    _resolve_carried_where,
    _without_unscoped,
    where_clause,
)

__all__ = ["all_of", "any_of", "combine", "none_of"]


def _require_wheres(
    name: str, clauses: tuple[Clause | Unscoped, ...]
) -> tuple[Clause, ...]:
    if not clauses:
        raise TypeError(f"{name}() requires at least one clause")
    given = _without_unscoped(name, clauses)
    if any(c._where_fn is None for c in given):
        raise TypeError(f"{name} combines only clauses with a where")
    return given


@overload
def all_of(*clauses: WhereClause) -> WhereClause: ...


@overload
def all_of(*clauses: Clause) -> Clause: ...


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


@overload
def all_of(*clauses: Clause | Unscoped) -> Clause | Unscoped: ...


def all_of(*clauses: Clause | Unscoped) -> Clause | Unscoped:
    """AND the operands' predicates.

    UNSCOPED is a no-op. Every other operand must carry a where, and its
    transforms travel along. One remaining operand is returned unchanged,
    and none returns UNSCOPED. Otherwise returns a WhereClause when every
    remaining operand is one. Calling with no arguments raises.
    """
    given = _require_wheres("all_of", clauses)
    if not given:
        return UNSCOPED
    if len(given) == 1:
        return given[0]

    def fn() -> ColumnElement[bool]:
        return and_(*(_resolve_carried_where(c) for c in given))

    fn.__name__ = fn.__qualname__ = "all_of"
    if any(_has_transform(c) for c in given):
        result: Clause = CombinedClause()
        result._where_fn = contextual(fn)
    else:
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
    Other operands must be WhereClauses: OR over a predicate that depends on
    an inner join changes which rows exist at all. Express such a
    condition as EXISTS, via relationship .any()/.has(), in a where.
    """
    given = _require_wheres("any_of", clauses)
    _require_no_transforms(
        "any_of",
        given,
        "an inner join already removes rows, breaking OR semantics; "
        "express the joined condition as EXISTS via .any()/.has() in a where",
    )
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
    validated but not resolved in that case. Operand rules as for any_of.
    """
    given = _require_wheres("none_of", clauses)
    _require_no_transforms(
        "none_of",
        given,
        "an inner join already removes rows, breaking NOT semantics; "
        "express the joined condition as EXISTS via .any()/.has() in a where",
    )
    if len(given) != len(clauses):
        return where_clause(false())
    fn = lambda: not_(or_(*(_resolve_carried_where(c) for c in given)))  # noqa: E731
    fn.__name__ = fn.__qualname__ = "none_of"
    result = where_clause(fn)
    result._children = given
    return result


def combine(*clauses: Clause | Unscoped) -> CombinedClause:
    """Bundle the wheres and transforms of several clauses under one name.

    Not boolean logic: it gives a transform+where combination its own
    name instead of mutating an existing Clause (which may already be
    reused elsewhere). Wheres are AND-ed; transforms stay on the
    children, where apply_clauses finds them. At least one operand must
    carry a transform. UNSCOPED is ignored and does not supply a transform.
    A bundle of only wheres is all_of's job.
    """
    given = _without_unscoped("combine", clauses)
    for clause in given:
        if isinstance(clause, ContextParam):
            raise TypeError(
                "combine cannot bundle a ContextParam; embed the slot in an "
                "expression instead"
            )
    if not any(_has_transform(c) for c in given):
        raise TypeError(
            "combine requires at least one transform-carrying clause; "
            "a bundle of only wheres is all_of's job"
        )
    result = CombinedClause()
    wheres = [c for c in given if c._where_fn is not None]
    if wheres:
        fn = lambda: and_(*(_resolve_carried_where(c) for c in wheres))  # noqa: E731
        fn.__name__ = fn.__qualname__ = "combine"
        result._where_fn = contextual(fn)
    result._children = given
    return result
