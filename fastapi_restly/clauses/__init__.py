"""Composable, context-bound query clauses for SQLAlchemy.

A WhereClause is a named, reusable predicate. where_clause() builds one
from a SQLAlchemy condition or from a function that returns one, and
all_of()/any_of()/none_of() compose them. A ContextParam is a value
slot, declared in a ContextNamespace (`name: ContextParam[T]`), not
constructed. Functions passed to where_clause() are wrapped so that
parameters the caller does not supply are injected from values bound
via bind().

A WhereClause is also callable. Calling it returns the raw
ColumnElement for use inside plain SQLAlchemy: join conditions, CASE
expressions, or a hand-built .where(). This bypasses apply_clauses'
table validation, so raw SQLAlchemy rules apply.

Composites built with all_of/any_of/none_of keep their operands as
children, and bind() routes each value down the tree to the leaf that
accepts it. Binding on a composite is therefore equivalent to binding on
the leaf itself:

    visible = all_of(owned_by_tenant, none_of(is_deleted))

    with visible.bind(tenant_id=tid):          # same as
    with owned_by_tenant.bind(tenant_id=tid):  # this

Routing is strict. A value nobody accepts raises, and a value accepted
by more than one distinct contextual instance raises too. That is an
accidental name collision, and binding on the leaf directly is the
unambiguous fix. The same leaf reached through several branches is fine
and binds once.

Statement construction stays plain SQLAlchemy. Build select()/update()/
delete() as usual and pass the result through apply_clauses(), the
bridge between the two worlds. The WhereClause.select/.update/.delete
methods are shorthand for the common single-clause path. select()
takes the same entities SQLAlchemy's select() takes. A clause is a
predicate and never joins: express a condition on a related table as
EXISTS (relationship .any()/.has()). apply_clauses rejects a predicate
that references a table the statement does not select from.
"""

from ._composition import all_of, any_of, none_of
from ._declarations import ContextNamespace, where_clause
from ._runtime import UNSCOPED, ContextParam, Unscoped, WhereClause, apply_clauses
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
