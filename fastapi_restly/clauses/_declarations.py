"""Declaration-time construction of clauses."""

from __future__ import annotations

import inspect
from typing import Callable

from sqlalchemy import ColumnElement

from ._runtime import WhereClause

__all__ = ["where_clause"]


def where_clause(
    condition: ColumnElement[bool] | Callable[[], ColumnElement[bool]],
) -> WhereClause:
    """A WhereClause from a condition or a function returning one.

    A ready-made ColumnElement is reused as-is. A function takes no
    parameters and runs each time the clause is applied, so it can branch
    in Python. Both read request and local values from ContextNamespace
    members: embed the member in the condition
    (``Item.user_id == Current.user_id``), or call it in the function body
    (``Current.user_id()``). A value the caller already has needs no
    clause function: build the condition there and wrap it,
    ``where_clause(Item.collection_id == collection_id)``.

    In a ClauseNamespace body, stack this decorator over ``staticmethod``:
    the marker keeps a type checker from reading the def as a method, and
    is unwrapped here.
    """
    if isinstance(condition, staticmethod):
        condition = condition.__func__
    if callable(condition):
        _validate_clause_fn(condition)
        return WhereClause._of(condition, _function_label(condition))
    return WhereClause._of(lambda: condition, _condition_label(condition))


def _validate_clause_fn(fn: Callable) -> None:
    label = getattr(fn, "__name__", "clause function")
    if inspect.iscoroutinefunction(fn) or inspect.iscoroutinefunction(
        getattr(type(fn), "__call__", None)
    ):
        raise TypeError(
            f"{label} is async; clause functions build expressions and must be sync"
        )
    names = list(inspect.signature(fn).parameters)
    if names:
        raise TypeError(
            f"{label} takes parameters ({', '.join(names)}); a clause function "
            "takes none. Read a request or local value from a ContextNamespace "
            "member, in the body (Current.user_id()) or embedded in the "
            "condition (Item.user_id == Current.user_id). When the caller has "
            "the value, build the condition there: "
            "where_clause(Model.column == value)"
        )


def _function_label(fn: Callable) -> str:
    name = getattr(fn, "__name__", "")
    return name if name and not name.startswith("<") else "<function>"


def _condition_label(condition: object) -> str:
    try:
        text = str(condition).replace("\n", " ")
    except Exception:
        return "<condition>"
    return text if len(text) <= 50 else text[:47] + "..."
