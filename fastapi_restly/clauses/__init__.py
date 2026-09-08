"""Composable, context-bound query clauses for SQLAlchemy."""

from ._runtime import (
    UNSCOPED,
    Clause,
    CombinedClause,
    ContextNamespace,
    ContextParam,
    TransformClause,
    Unscoped,
    WhereClause,
    all_of,
    any_of,
    apply_clauses,
    combine,
    none_of,
    transform_clause,
    where_clause,
)
from ._runtime import _apply_where_half as _apply_where_half
from ._runtime import _context_param as _context_param
from ._scopes import _NAMESPACES as _NAMESPACES
from ._scopes import ClauseNamespace
from ._scopes import _default_scope as _default_scope

__all__ = [
    "UNSCOPED",
    "Unscoped",
    "Clause",
    "ClauseNamespace",
    "CombinedClause",
    "ContextNamespace",
    "ContextParam",
    "TransformClause",
    "WhereClause",
    "all_of",
    "any_of",
    "apply_clauses",
    "combine",
    "none_of",
    "transform_clause",
    "where_clause",
]
