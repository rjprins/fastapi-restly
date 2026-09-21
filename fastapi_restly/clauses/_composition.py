"""Boolean composition and bundling for query clauses."""

from __future__ import annotations

from typing import overload

from sqlalchemy import and_, false, not_, or_

from ._runtime import UNSCOPED, Unscoped, WhereClause, _without_unscoped

__all__ = ["all_of", "any_of", "none_of"]


def _require_wheres(
    name: str, clauses: tuple[WhereClause | Unscoped, ...]
) -> tuple[WhereClause, ...]:
    if not clauses:
        raise TypeError(f"{name}() requires at least one clause")
    return _without_unscoped(name, clauses)


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

    # operands resolve unfilled: the composite fills every placeholder once
    return WhereClause._of(lambda: and_(*(c._build() for c in given)), "all_of")


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
    return WhereClause._of(lambda: or_(*(c._build() for c in given)), "any_of")


def none_of(*clauses: WhereClause | Unscoped) -> WhereClause:
    """True when none of the operands' predicates hold.

    NOT over the OR of all operands. An UNSCOPED operand returns a
    WhereClause over SQL false(), matching no rows. Other operands are
    validated but not resolved in that case.
    """
    given = _require_wheres("none_of", clauses)
    if len(given) != len(clauses):
        return WhereClause._of(false, "none_of")
    return WhereClause._of(lambda: not_(or_(*(c._build() for c in given))), "none_of")
