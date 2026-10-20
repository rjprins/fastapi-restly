from typing import Any, Literal, cast

import sqlalchemy
from sqlalchemy import ColumnElement, Select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.sql.compiler import SQLCompiler
from sqlalchemy.sql.elements import UnaryExpression
from sqlalchemy.sql.operators import nulls_first_op, nulls_last_op

#: Where a sort puts NULLs: first or last in both directions, or where the
#: database puts them.
SortNulls = Literal["first", "last"] | None


def _escape_like_value(value: str) -> str:
    """Escape SQL LIKE wildcard characters for literal substring matching."""
    escaped = value.replace("\\", "\\\\")
    escaped = escaped.replace("%", "\\%")
    escaped = escaped.replace("_", "\\_")
    return escaped


def _primary_key_attrs(
    model: type[DeclarativeBase],
) -> list[InstrumentedAttribute[Any]]:
    """The mapped attributes of ``model``'s primary key, in mapper order."""
    mapper = sqlalchemy.inspect(model)
    return [
        getattr(model, mapper.get_property_by_column(column).key)
        for column in mapper.primary_key
    ]


def _append_pk_tiebreak(
    query: Select[Any],
    model: type[DeclarativeBase],
    sorted_on: list[InstrumentedAttribute[Any]],
) -> Select[Any]:
    """Append the primary key as the final ``ORDER BY`` terms.

    Without it, equal-valued rows on a non-unique sort can be skipped or
    repeated across pages. Key columns the caller already sorted on are not
    appended again.
    """
    for pk in _primary_key_attrs(model):
        if not any(pk is column for column in sorted_on):
            query = query.order_by(pk)
    return query


class _SortTerm(UnaryExpression[Any]):
    """``nulls_first()`` or ``nulls_last()`` that works on every database.

    PostgreSQL, Oracle and SQLite 3.30 or later get ``NULLS FIRST`` or
    ``NULLS LAST``. Other databases, such as MySQL and SQL Server, do not
    have it, and get ``CASE WHEN x IS NULL ...`` before the term. It is a
    ``UnaryExpression`` as ``nulls_last()`` is, so the ORM finds the column
    in it, as it does for a joined eager load with a page.
    """

    __visit_name__ = "restly_sort_term"
    inherit_cache = True


@compiles(_SortTerm)
def _compile_with_case(term: _SortTerm, compiler: SQLCompiler, **kw: Any) -> str:
    ordered = cast(UnaryExpression[Any], term.element)
    first, other = ("0", "1") if term.modifier is nulls_first_op else ("1", "0")
    is_null = sqlalchemy.case(
        (ordered.element.is_(None), sqlalchemy.literal_column(first)),
        else_=sqlalchemy.literal_column(other),
    )
    return f"{compiler.process(is_null, **kw)}, {compiler.process(ordered, **kw)}"


@compiles(_SortTerm, "postgresql")
@compiles(_SortTerm, "oracle")
def _compile_native(term: _SortTerm, compiler: SQLCompiler, **kw: Any) -> str:
    return compiler.visit_unary(term, **kw)


@compiles(_SortTerm, "sqlite")
def _compile_sqlite(term: _SortTerm, compiler: SQLCompiler, **kw: Any) -> str:
    # The version is known once the engine has connected.
    if (compiler.dialect.server_version_info or ()) >= (3, 30):
        return _compile_native(term, compiler, **kw)
    return _compile_with_case(term, compiler, **kw)


def _sort_term(
    column: Any, *, descending: bool, nulls: SortNulls, joined: bool = False
) -> ColumnElement[Any]:
    """The ``ORDER BY`` term for ``column``, with NULLs where ``nulls`` says.

    A column of the model that cannot be NULL gets a plain term, which keeps
    the SQL simple.

    :param joined: ``column`` is on a row that a join added. A sort joins
        with a LEFT OUTER JOIN, so the value can be NULL.
    """
    ordered = sqlalchemy.desc(column) if descending else sqlalchemy.asc(column)
    if nulls is None or (not joined and not _can_be_null(column)):
        return ordered
    modifier = nulls_first_op if nulls == "first" else nulls_last_op
    return _SortTerm(ordered, modifier=modifier)


def _can_be_null(column: Any) -> bool:
    """Whether the value of ``column`` can be NULL. An expression, such as a
    ``column_property`` with a subquery, can."""
    columns = getattr(getattr(column, "property", None), "columns", ())
    return not columns or any(
        not isinstance(c, sqlalchemy.Column) or c.nullable for c in columns
    )
