"""Composable, context-bound query clauses for SQLAlchemy.

A Clause carries a predicate ("where") and/or a statement transform
("transform"). Clause itself is abstract. Each constructor names the
kind it builds: where_clause() -> WhereClause, transform_clause() ->
TransformClause, combine() -> CombinedClause. The fourth kind, a
ContextParam value slot, is declared in a ContextNamespace
(`name: ContextParam[T]`), not constructed. A CombinedClause always
carries at least one transform. A bundle of only wheres is all_of's
job. Functions passed to the constructors are wrapped so that
parameters the caller does not supply are injected from values bound
via Clause.bind().

A WhereClause is also callable. Calling it returns the raw
ColumnElement for use inside plain SQLAlchemy: join conditions, CASE
expressions, or a hand-built .where(). This bypasses apply_clauses'
table validation, so raw SQLAlchemy rules apply.

Composites built with all_of/any_of/none_of/combine keep their operands
as children, and Clause.bind() routes each value down the tree to the
leaf that accepts it. Binding on a composite is therefore equivalent to
binding on the leaf itself:

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
bridge between the two worlds. The Clause.select/.update/.delete
methods are shorthand for the common single-clause path. select()
takes the same entities SQLAlchemy's select() takes. apply_clauses
collects transforms from the whole clause tree, each distinct transform
applied once, so a join carried inside an all_of or combine is never
lost. any_of and none_of reject operands that carry a transform. OR/NOT
over an inner-join-dependent predicate silently changes which rows
exist at all. Express such conditions as EXISTS (relationship
.any()/.has()) in a plain where.
"""

from ._composition import all_of, any_of, combine, none_of
from ._declarations import ContextNamespace, transform_clause, where_clause
from ._runtime import (
    UNSCOPED,
    Clause,
    CombinedClause,
    ContextParam,
    TransformClause,
    Unscoped,
    WhereClause,
    apply_clauses,
)
from ._scopes import ClauseNamespace

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
