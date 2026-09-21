"""Composable, context-bound query clauses for SQLAlchemy.

A WhereClause is a named, reusable predicate. where_clause() builds one
from a SQLAlchemy condition or from a function that returns one, and
all_of()/any_of()/none_of() compose them. A clause takes no arguments and
holds no values.

A value that changes per request or per call is a ContextParam: a member
of a ContextNamespace (`name: ContextParam[T]`), bound through the
namespace (`Current.bind(tenant_id=tid)`, or `Current.depends(...)` per
request). Embedded in a condition (`Item.tenant_id == Current.tenant_id`)
the member is a placeholder; called in a clause function
(`Current.tenant_id()`) it returns the value. Both are read when the
clause is applied, so the statement carries the values with it, and an
unbound member raises LookupError instead of running unfiltered.

A WhereClause is also callable. Calling it returns the raw
ColumnElement for use inside plain SQLAlchemy: join conditions, CASE
expressions, or a hand-built .where(). This bypasses apply_clauses'
table validation, so raw SQLAlchemy rules apply.

Statement construction stays plain SQLAlchemy. Build select()/update()/
delete() as usual and pass the result through apply_clauses(), the
bridge between the two worlds. A clause is a predicate and never joins:
express a condition on a related table as EXISTS (relationship
.any()/.has()). apply_clauses rejects a predicate that references a
table the statement does not select from.
"""

from ._composition import all_of, any_of, none_of
from ._context import ContextNamespace, ContextParam
from ._declarations import where_clause
from ._runtime import UNSCOPED, Unscoped, WhereClause, apply_clauses
from ._scopes import ClauseNamespace

__all__ = [
    "UNSCOPED",
    "Unscoped",
    "ClauseNamespace",
    "ContextNamespace",
    "ContextParam",
    "WhereClause",
    "all_of",
    "any_of",
    "apply_clauses",
    "none_of",
    "where_clause",
]
