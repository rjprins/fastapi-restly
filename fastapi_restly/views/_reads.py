"""The statements that a view reads with.

They load the relationships of a response, count the rows of a list, and
reload the rows after a write. This module also checks the read scope and
the columns that pick one row, and writes the message for a row that is not
found.
"""

import inspect
from typing import Any

import pydantic
from fastapi.exceptions import RequestValidationError
from sqlalchemy import ColumnElement, Select, func, tuple_
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select as sa_select
from sqlalchemy.orm import QueryableAttribute, selectinload

from .._columns import column_value
from .._times import value_error_reason
from ..clauses import UNSCOPED, WhereClause, where_clause
from ..clauses._runtime import (
    _display_table_name,
    _expression_tables,
    _statement_tables,
)
from ..clauses._scopes import _default_scope
from ..exc import RestlyConfigurationError
from ..schemas._base import _collection_item_type, _unwrap_type


def _get_nested_schema_annotation(
    annotation: Any, seen: set[Any] | None = None
) -> type[pydantic.BaseModel] | None:
    seen = set() if seen is None else set(seen)
    annotation, _ = _unwrap_type(annotation, seen)

    try:
        if inspect.isclass(annotation) and issubclass(annotation, pydantic.BaseModel):
            return annotation
    except TypeError:
        pass

    item = _collection_item_type(annotation, seen)
    if item is not None:
        return _get_nested_schema_annotation(item, seen)

    return None


def _build_relationship_loader_options(
    model_cls: type[Any],
    schema_cls: type[pydantic.BaseModel],
    seen: set[tuple[type[Any], type[pydantic.BaseModel]]] | None = None,
) -> list[Any]:
    if seen is None:
        seen = set()

    visit_key = (model_cls, schema_cls)
    if visit_key in seen:
        return []
    seen = seen | {visit_key}

    mapper = sa_inspect(model_cls)
    options: list[Any] = []
    for field_name, field_info in schema_cls.model_fields.items():
        if field_name not in mapper.relationships:
            continue

        relationship_prop = mapper.relationships[field_name]
        loader = selectinload(getattr(model_cls, field_name))
        nested_schema = _get_nested_schema_annotation(field_info.annotation)

        if nested_schema is not None:
            child_options = _build_relationship_loader_options(
                relationship_prop.mapper.class_, nested_schema, seen
            )
            if child_options:
                loader = loader.options(*child_options)

        options.append(loader)

    return options


#: The most rows that one reload SELECT names, below the parameter limits of
#: the databases (SQLite allows 32766 in all, older versions 999).
_RELOAD_CHUNK = 500


def _rows_reload_statements(
    model: Any, rows: list[Any], options: list[Any]
) -> list[Any]:
    """The SELECTs that reload saved ``rows`` of ``model``, in chunks.

    The save expires the relationships of the rows first, so a changed
    foreign key is never served from a stale relationship. The SELECT then
    loads the relationships and the expressions of the response through
    ``options``, and fills only what is unloaded: not ``populate_existing``,
    which would expire a deferred column that the route loaded, the columns
    of a subclass, and a change made after the flush.
    """
    mapper = sa_inspect(model)
    statements = []
    for start in range(0, len(rows), _RELOAD_CHUNK):
        identities = [
            sa_inspect(row).identity for row in rows[start : start + _RELOAD_CHUNK]
        ]
        if len(mapper.primary_key) == 1:
            where = mapper.primary_key[0].in_([identity[0] for identity in identities])
        else:
            where = tuple_(*mapper.primary_key).in_(identities)
        statements.append(sa_select(model).where(where).options(*options))
    return statements


def _count_statement(stmt: Select[Any]) -> Select[Any]:
    """The SELECT that counts the rows of the list statement ``stmt``.

    A list has one object for each row of the entity that ``stmt`` selects
    first. So the count counts the distinct values of the columns that name
    such a row (``_identity_columns``), and a join that repeats a row does
    not count it twice. The other columns are left out. PostgreSQL cannot
    compare ``json`` values, so ``DISTINCT`` over a ``json`` column fails.
    And the subquery of a ``column_property`` would run for every row of the
    list. A statement that does not select an entity first counts its
    distinct rows.
    """
    source = stmt.order_by(None).limit(None).offset(None)
    columns = _identity_columns(stmt)
    if columns:
        source = source.with_only_columns(*columns, maintain_column_froms=True)
    return sa_select(func.count()).select_from(source.distinct().subquery())


def _identity_columns(stmt: Select[Any]) -> list[Any]:
    """The columns that name a row of the entity that ``stmt`` selects first.

    These are the primary key columns, as attributes of the entity, so the
    ORM puts them on the selectable the entity loads from: an alias, or the
    union of a concrete inheritance. Each table of such a union numbers its
    own rows, so the discriminator is added too. Empty when the first column
    of ``stmt`` is not an entity.
    """
    descriptions = stmt.column_descriptions
    entity = descriptions[0]["entity"] if descriptions else None
    if entity is None or descriptions[0]["expr"] is not entity:
        return []
    info = sa_inspect(entity)
    mapper = info.mapper
    columns = [
        getattr(entity, mapper.get_property_by_column(column).key)
        for column in mapper.primary_key
    ]
    polymorphic = mapper.with_polymorphic
    if polymorphic and polymorphic[1] is not None and mapper.polymorphic_on is not None:
        # the discriminator as a column of what the entity selects from
        discriminator = info.selectable.corresponding_column(mapper.polymorphic_on)
        if discriminator is not None:
            columns.append(discriminator)
    return columns


def _model_scope(model: type[Any]) -> WhereClause:
    declared = _default_scope(model)
    return UNSCOPED if declared is None else declared


def _checked_scope(scope: Any, label: str, what: str) -> WhereClause:
    if isinstance(scope, WhereClause):
        return scope
    raise RestlyConfigurationError(
        f"{label}: {what} must be a WhereClause or fr.clauses.UNSCOPED, got "
        f"{type(scope).__name__}; wrap a raw expression with where_clause()"
    )


def _clause_element(value: Any) -> Any:
    """The SQL expression behind a mapped column or a hybrid property, such
    as ``Item.archived``, which ``.where()`` accepts too. Other values are
    returned as they are."""
    if isinstance(value, QueryableAttribute):
        return value.__clause_element__()
    return value


def _checked_where(where: Any, label: str) -> WhereClause:
    if isinstance(where, WhereClause):
        return where
    where = _clause_element(where)
    if isinstance(where, ColumnElement):
        return where_clause(where)
    if type(where) is bool:
        # ``obj.slug == slug`` on a loaded object is a Python bool, and
        # SQLAlchemy renders it as WHERE true, which narrows nothing
        raise TypeError(
            f"{label}: where= got a bool, not a SQL expression. A comparison "
            "on an instance attribute is a bool; compare the column instead "
            "(Model.slug == slug)."
        )
    raise TypeError(
        f"{label}: where= must be a SQLAlchemy boolean expression or a clause, "
        f"got {type(where).__name__}"
    )


def _check_identity_tables(
    model_cls: type[Any], id: ColumnElement[bool], label: str
) -> None:
    """Raise when the expression that picks the row names another table.

    SQLAlchemy adds such a table to the FROM of the query, which joins every
    row of it, so ``Shelf.id == 2`` in a view of books picks a book that has
    nothing to do with shelf 2.
    """
    missing = _expression_tables(id) - _statement_tables(sa_select(model_cls))
    if missing:
        name = model_cls.__name__
        shown = ", ".join(sorted(_display_table_name(key) for key in missing))
        raise TypeError(
            f"{label}: the expression that picks the row names a table that is "
            f"not in the query: {shown}. It would join every row of that "
            f"table, so a wrong row could be loaded. Compare a column of "
            f"{name}, such as {name}.slug == slug, or use .has() or .any() "
            "for a related row."
        )


def _reject_bad_row_identity(label: str, id: Any) -> None:
    """Raise for a value that looks like an id but cannot pick a row."""
    if isinstance(id, WhereClause):
        raise TypeError(
            f"{label}: a clause is a scope, not a row identity. "
            "Call it for its expression (ItemClauses.published()), or "
            "pass it as scope= to replace the view scope for this read."
        )
    if type(id) is bool:
        # ``obj.slug == slug`` on a loaded object is a Python bool, and
        # SQLAlchemy renders it as WHERE true, which matches the whole scope
        raise TypeError(
            f"{label}: got a bool, not an id or a SQL expression. "
            "A comparison on an instance attribute is a bool; compare the "
            "column instead (Model.slug == slug)."
        )


def _primary_key_criterion(
    model_cls: type[Any], id: Any, path_parameter: str
) -> ColumnElement[bool]:
    """The default :meth:`BaseRestView.where_id`: the primary key is ``id``.
    ``path_parameter`` is the name of ``id`` in the path, for the error.

    :raises RequestValidationError: ``id`` does not fit the key column, such
        as a number out of the range of an ``Integer``. The database would
        refuse it with an error.
    """
    pk_cols = sa_inspect(model_cls).primary_key
    if len(pk_cols) != 1:
        raise NotImplementedError(
            f"{model_cls.__name__} has a composite primary key, so an id does "
            "not name one row. Override where_id(id): split the id from the "
            "path, and return sqlalchemy.and_(Model.a == a, Model.b == b)."
        )
    try:
        id = column_value(pk_cols[0].type, id)
    except ValueError as exc:
        raise RequestValidationError(
            [
                {
                    "type": "value_error",
                    "loc": ("path", path_parameter),
                    "msg": value_error_reason(exc),
                    "input": id,
                    "ctx": {"error": exc},
                }
            ]
        ) from None
    return pk_cols[0] == id


def _not_found_message(resource: str, id: Any) -> str:
    # ``id`` is None for a route that loaded by an expression: the 404 body
    # is no place for SQL
    if id is None:
        return f"{resource} was not found"
    return f"{resource} with id {id} was not found"
