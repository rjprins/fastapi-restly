"""Boolean composition of query clauses."""

from __future__ import annotations

from sqlalchemy import and_, false, not_, or_

from ._runtime import UNSCOPED, WhereClause, _without_unscoped

__all__ = ["all_of", "any_of", "none_of"]


def _operands(name: str, clauses: tuple[WhereClause, ...]) -> tuple[WhereClause, ...]:
    if not clauses:
        raise TypeError(f"{name}() requires at least one clause")
    return _without_unscoped(name, clauses)


def _fixed(clauses: tuple[WhereClause, ...]) -> bool:
    # a composite has a fixed condition when none of its operands runs a
    # clause function
    return all(clause._fixed for clause in clauses)


def all_of(*clauses: WhereClause) -> WhereClause:
    """AND the operands' predicates.

    UNSCOPED is a no-op. One remaining operand is returned unchanged, and
    none returns UNSCOPED. Calling with no arguments raises.
    """
    given = _operands("all_of", clauses)
    if not given:
        return UNSCOPED
    if len(given) == 1:
        return given[0]

    # operands resolve unfilled: the composite fills every placeholder once
    return WhereClause._of(
        lambda: and_(*(c._build() for c in given)), "all_of", fixed=_fixed(given)
    )


def any_of(*clauses: WhereClause) -> WhereClause:
    """OR the operands' predicates.

    UNSCOPED accepts every row, so any UNSCOPED operand returns the sentinel.
    Other operands are validated before this simplification and are not
    resolved if the result is UNSCOPED. Calling with no arguments raises.
    """
    given = _operands("any_of", clauses)
    if len(given) != len(clauses):
        return UNSCOPED
    return WhereClause._of(
        lambda: or_(*(c._build() for c in given)), "any_of", fixed=_fixed(given)
    )


def none_of(*clauses: WhereClause) -> WhereClause:
    """A clause that matches the rows where none of the operands match.

    It is NOT over the OR of all operands: ``NOT (a OR b)``, not
    ``NOT (a AND b)``. An UNSCOPED operand returns a
    WhereClause over SQL false(), matching no rows. Other operands are
    validated but not resolved in that case.
    """
    given = _operands("none_of", clauses)
    if len(given) != len(clauses):
        return WhereClause._of(false, "none_of", fixed=True)
    return WhereClause._of(
        lambda: not_(or_(*(c._build() for c in given))), "none_of", fixed=_fixed(given)
    )
