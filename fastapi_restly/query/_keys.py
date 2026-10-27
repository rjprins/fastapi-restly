"""One key of list params of your own: a field of the response class, and the
column of the model that it names."""

from typing import Any

from sqlalchemy import ColumnElement
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import QueryableAttribute, RelationshipProperty

from ..exc import BadQueryParam
from ._impl import SchemaType, _resolve_field_name, _value_parser
from ._shared import SortNulls, _sort_term


def _find_column(
    model: type[Any], schema_response: SchemaType, key: str, kind: str
) -> tuple[str, QueryableAttribute[Any]]:
    """The Python name of the field that ``key`` names, and the attribute of
    ``model`` with that name.

    :param kind: ``"filter"`` or ``"sort"``, for the message.
    :raises BadQueryParam: ``Unknown <kind> key``.
    """
    unknown = BadQueryParam(f"Unknown {kind} key: {key!r}")
    field_name = _resolve_field_name(schema_response, key)
    if field_name is None:
        raise unknown
    column = getattr(model, field_name, None)
    # A Python property or a plain class attribute has no SQL, and a
    # relationship holds rows, not a value. A hybrid property has SQL, and
    # its proxy has no ``property``.
    if not isinstance(column, QueryableAttribute) or isinstance(
        getattr(column, "property", None), RelationshipProperty
    ):
        raise unknown
    # A hybrid property that is plain Python, such as
    # ``self.deleted_at is not None``, is a Python value on the class, and a
    # composite is several columns. Neither is one value in SQL.
    try:
        expression = column.expression
    except InvalidRequestError:
        raise unknown from None
    if not isinstance(expression, ColumnElement):
        raise unknown
    return field_name, column


class FilterKey:
    """One filter key of list params of your own: the field of the response
    class that the client's key names, and the column of the model.

    .. code-block:: python

        key = fr.query.FilterKey(self.model, self.schema_response, "status")
        stmt = stmt.where(key.column == key.parse("active"))

    The key is the name of a field, or its alias when the field has one, as
    in the generated filters. The model has an attribute with the name of
    the field that is one value in SQL: a column, a ``column_property``, or
    a hybrid property with an SQL expression. A hybrid property that is
    plain Python, such as ``self.deleted_at is not None``, is no filter key.
    Neither is a field that a response never shows: a ``WriteOnly`` field,
    one with ``exclude=True``, or one of Pydantic's secret types. A filter on
    it would let a client find out its value. Restly cannot see a value that
    a serializer hides; type such a field as a subclass of
    :class:`pydantic.Secret <pydantic.types.Secret>` instead. A key with a
    dot, such as ``vendor.name``, needs a join, so it is unknown here. The
    react-admin views use this class for their ``filter``.

    :param model: The model of the rows, such as the view's ``model``.
    :param schema_response: The response class. A view passes its
        ``schema_response``, so a client can filter only on what it sees.
    :param key: The key, as the client sends it.
    :raises fastapi_restly.exc.BadQueryParam: ``Unknown filter key``.
    """

    #: The key, as the client sent it.
    key: str
    #: The attribute of the model that the key filters, such as
    #: ``Item.status``.
    column: QueryableAttribute[Any]

    def __init__(self, model: type[Any], schema_response: SchemaType, key: str) -> None:
        self.key = key
        self._schema = schema_response
        self._model = model
        field_name, self.column = _find_column(model, schema_response, key, "filter")
        self._parse = _value_parser(schema_response, field_name, self.column, key)

    def parse(self, value: Any) -> Any:
        """``value`` as the column takes it, for a condition such as
        ``key.column == key.parse(value)``.

        The value goes through the validation of the field, as in the
        generated filters, so the validators of the field run too. They get
        only this field, so ``info.data`` is ``{}``; see
        :ref:`filter-value-validation`. The value can be text from a URL or a
        value from JSON, and a number becomes text for a text field. ``None``
        stays ``None`` when the field allows it, so ``key.column == None``
        gives ``IS NULL``. A reference, such as ``IDRef[User]``, gives its
        id. A column of SQLAlchemy's ``Enum`` type takes only its labels. A
        model for a ``JSON`` column becomes JSON data, as a write stores it.
        A time gets the form of its column; see :ref:`utc-times`.

        :raises fastapi_restly.exc.BadQueryParam: ``Invalid filter value``,
            with the reason.
        """
        return self._parse(value)

    def __repr__(self) -> str:
        return (
            f"FilterKey({self._model.__name__}, {self._schema.__name__}, {self.key!r})"
        )


class SortKey:
    """One sort key of list params of your own: the field of the response
    class that the client's key names, and the column of the model.

    .. code-block:: python

        key = fr.query.SortKey(self.model, self.schema_response, "name")
        stmt = stmt.order_by(key.term(descending=True, sort_nulls=self.sort_nulls))

    It finds the field and the column as :class:`FilterKey` does. A field
    that a response never shows is no sort key either: the order of the rows
    would let a client find out its value. The react-admin views use this
    class for their ``sort``.

    :param model: The model of the rows, such as the view's ``model``.
    :param schema_response: The response class. A view passes its
        ``schema_response``, so a client can sort only on what it sees.
    :param key: The key, as the client sends it.
    :raises fastapi_restly.exc.BadQueryParam: ``Unknown sort key``.
    """

    #: The key, as the client sent it.
    key: str
    #: The attribute of the model that the key sorts on, such as
    #: ``Item.name``.
    column: QueryableAttribute[Any]

    def __init__(self, model: type[Any], schema_response: SchemaType, key: str) -> None:
        self.key = key
        self._schema = schema_response
        self._model = model
        _, self.column = _find_column(model, schema_response, key, "sort")

    def term(
        self, *, descending: bool = False, sort_nulls: SortNulls = "last"
    ) -> ColumnElement[Any]:
        """The term for ``order_by()``. Order by a unique column after it,
        such as the primary key, so that a row never moves to another page.

        :param descending: Sort from the highest value to the lowest.
        :param sort_nulls: Where the NULLs go: ``"last"`` (the default) or
            ``"first"`` in both directions, or ``None`` for where the
            database puts them. A view passes its ``sort_nulls``.
        """
        return _sort_term(self.column, descending=descending, nulls=sort_nulls)

    def __repr__(self) -> str:
        return f"SortKey({self._model.__name__}, {self._schema.__name__}, {self.key!r})"
