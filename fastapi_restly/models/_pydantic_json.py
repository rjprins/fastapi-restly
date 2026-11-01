import functools
import types
from typing import Annotated, Any, get_args, get_origin

import pydantic
from sqlalchemy import JSON, TypeDecorator, type_coerce
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.sql.operators import OperatorType
from sqlalchemy.types import TypeEngine

from ..exc import RestlyConfigurationError
from ..schemas._base import (
    _json_data,
    _reject_excluded_json_fields,
    _reject_excluded_json_type,
    _type_label,
)


class PydanticJSON(TypeDecorator[Any]):
    """A JSON column that holds a Pydantic type, such as a model::

        class Address(pydantic.BaseModel):
            street: str
            city: str


        class Shop(fr.IDMixin, Base):
            address: Mapped[Address]
            branches: Mapped[dict[str, Address]] = mapped_column(
                fr.PydanticJSON(dict[str, Address])
            )

    On a base with :class:`ModelDefaults`, ``Mapped[Address]`` alone gives
    the column ``PydanticJSON(Address)``, for each Pydantic model. Its type
    map has ``pydantic.BaseModel: PydanticJSON(pydantic.BaseModel)``, as
    SQLAlchemy uses ``enum.Enum: Enum(enum.Enum)`` for an enum. Your own
    entry for ``pydantic.BaseModel`` wins. Another type, such as
    ``dict[str, Address]``, needs ``mapped_column``.

    The column validates the document when it loads a row, so
    ``shop.address`` is an ``Address``. A write validates the value too, so a
    dict works as well, and so does another model with the same fields. Then
    it dumps the value as ``TypeAdapter(type_)`` dumps it in JSON mode: a
    nested model becomes a dict, and a ``datetime`` becomes text. The keys
    are the names of the fields, not their aliases. A load reads the stored
    JSON data as Pydantic reads JSON, so a strict model reads it back too.

    The column refuses a type with a secret, such as ``SecretStr``, or with a
    field that a dump leaves out, such as a ``WriteOnly`` field. The column
    would not store the value of such a field. It also refuses a dataclass
    and an iterator type, such as ``Iterable``. It raises
    :class:`~fastapi_restly.exc.RestlyConfigurationError` when you define the
    column.

    In all else, the column works as SQLAlchemy's ``JSON``. ``None`` is stored
    as JSON ``null``, and ``none_as_null=True`` stores SQL ``NULL``. A change
    inside the document, such as ``shop.address.city = "Utrecht"``, is not
    saved: assign a new value. A query reads a part of the document as with
    ``JSON``, as in ``Shop.address["city"].as_string() == "Utrecht"``. A dict
    or a model in a query is dumped, but not validated, because it can be a
    part of a document.

    :class:`PydanticJSONB` is the same column with PostgreSQL's ``JSONB``.
    For another JSON type, write a subclass that sets ``impl`` and
    ``cache_ok = True``, as for any SQLAlchemy ``TypeDecorator``.

    :param type\\_: The type of the document. It must be hashable, as the
        types above are.
    :param none_as_null: Store ``None`` as SQL ``NULL``, as in ``JSON``.
    :raises RestlyConfigurationError: ``type_`` is not hashable, it has a
        part that the dump would mask or leave out, or it has a ``ReadOnly``
        field, which a write would replace.
    """

    impl = JSON
    cache_ok = True
    #: As for ``JSON``: a document can be a dict, which has no hash.
    hashable = False

    def __init__(self, type_: Any, none_as_null: bool = False) -> None:
        # none_as_null is not keyword-only, as in JSON: SQLAlchemy builds the
        # cache key of a type from the arguments that it can pass by position.
        super().__init__(none_as_null=none_as_null)
        self.type_ = type_
        self.none_as_null = none_as_null
        self._label = f"{type(self).__name__}({_type_label(type_)})"
        try:
            hash(type_)
        except TypeError:
            raise RestlyConfigurationError(
                f"{self._label}: the type is not hashable. SQLAlchemy caches a "
                "statement by the types in it. Use a type without a dict or a "
                "list in it, as in Annotated[str, Field(max_length=10)]."
            ) from None
        self._adapter: pydantic.TypeAdapter[Any] = pydantic.TypeAdapter(type_)
        _reject_excluded_json_type(type_, self._label)
        # A TypeDecorator does not take this from its impl. JSON puts a None
        # into the INSERT, as JSON null, unless none_as_null is set.
        self.should_evaluate_none = self.impl_instance.should_evaluate_none

    def _resolve_for_python_type(
        self, python_type: Any, matched_on: Any, matched_on_flattened: Any
    ) -> Any:
        """The column type for the model in ``Mapped[...]``.

        SQLAlchemy calls this for a type in a type map. As with
        ``sqlalchemy.Enum(enum.Enum)``, the generic form
        ``PydanticJSON(pydantic.BaseModel)`` makes a type for each model, of
        the same class and with the same options. Another type accepts only
        its own class, as any SQLAlchemy type does. SQLAlchemy has no public
        way to do this yet.
        """
        if (
            self.type_ is pydantic.BaseModel
            and isinstance(python_type, type)
            and issubclass(python_type, pydantic.BaseModel)
        ):
            if python_type is pydantic.BaseModel:
                raise RestlyConfigurationError(
                    f"Mapped[pydantic.BaseModel] names no model, so "
                    f"{type(self).__name__} could not load the document. Name "
                    "the model, as in Mapped[Address]."
                )
            return type(self)(python_type, none_as_null=self.none_as_null)
        return super()._resolve_for_python_type(
            python_type, matched_on, matched_on_flattened
        )

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        """Validate ``value`` and dump it as JSON data, by the type of the
        document.

        Another model with the same fields validates by its attributes, as a
        schema of a view does. A subclass keeps only the fields of the type,
        as a field of that type does in ``model_dump()``.
        """
        if value is None or value is JSON.NULL:
            return value
        document = self._adapter.validate_python(
            value, by_name=True, from_attributes=True
        )
        # The type was checked when the column was defined. A part of type
        # Any can still hold a secret.
        _reject_excluded_json_fields(document, self._label)
        return self._adapter.dump_python(
            document, mode="json", round_trip=True, by_alias=False
        )

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        """Validate the stored document.

        The document is JSON data, so it is read as Pydantic reads JSON: a
        strict field takes text for a ``datetime``, and a list for a tuple.
        A field can have its name as the key, as the dump writes it, or its
        alias, as Pydantic reads it by default.
        """
        if value is None:
            return None
        return self._adapter.validate_python(value, by_name=True, strict=False)

    def coerce_compared_value(self, op: OperatorType | None, value: Any) -> Any:
        """The type of a value in a query, as the JSON type gives it.

        SQLAlchemy's docs advise this for a ``TypeDecorator`` over ``JSON``. A
        dict, a list or a model is dumped but not validated: it can be a part
        of a document, as in ``contains()`` of ``JSONB``.
        """
        coerced = self.impl_instance.coerce_compared_value(op, value)
        if coerced is self.impl_instance:
            return _QueryValue(self.impl_instance, self._label)
        return coerced

    @property
    def comparator_factory(self) -> Any:  # type: ignore[override]
        return _document_comparator(self.impl_instance.comparator_factory)

    @property
    def python_type(self) -> type:
        """The class of the document, such as ``dict`` for
        ``dict[str, Address]``.

        :raises NotImplementedError: The type has no one class, as a union or
            ``Any`` has not. SQLAlchemy raises it for such a type too.
        """
        type_ = self.type_
        if get_origin(type_) is Annotated:
            type_ = get_args(type_)[0]
        origin = get_origin(type_) or type_
        if origin is Any or not isinstance(origin, type) or origin is types.UnionType:
            raise NotImplementedError
        return origin

    def __repr__(self) -> str:
        return self._label


class PydanticJSONB(PydanticJSON):
    """:class:`PydanticJSON` with PostgreSQL's ``JSONB``::

        class Shop(fr.IDMixin, Base):
            address: Mapped[Address] = mapped_column(fr.PydanticJSONB(Address))

    PostgreSQL can compare two ``JSONB`` documents, and ``JSONB`` has more
    operators, such as ``contains`` and ``has_key``. For ``Mapped[Address]``
    alone, put ``pydantic.BaseModel: fr.PydanticJSONB(pydantic.BaseModel)``
    in the ``type_annotation_map`` of your base. SQLite has no ``JSONB``.
    """

    impl = JSONB
    cache_ok = True


class _QueryValue(TypeDecorator[Any]):
    """A value that a query compares with a document: dumped as JSON data,
    and not validated."""

    impl = JSON
    cache_ok = True

    def __init__(self, impl: TypeEngine[Any], label: str) -> None:
        super().__init__()
        self.impl = impl
        self.label = label

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        return _json_data(value, self.label)


@functools.cache
def _document_comparator(impl_comparator: type) -> type:
    """The comparator of a :class:`PydanticJSON` over a JSON type with
    ``impl_comparator``."""

    # As in TypeDecorator.comparator_factory: an impl that is a TypeDecorator
    # has TypeDecorator.Comparator in its comparator already.
    bases = (
        (impl_comparator,)
        if TypeDecorator.Comparator in impl_comparator.__mro__
        else (TypeDecorator.Comparator, impl_comparator)
    )

    class Comparator(*bases):  # type: ignore[misc]
        def __getitem__(self, index: Any) -> Any:
            # A part of a document is JSON, not a document. SQLAlchemy gives
            # it the type of the column, which would validate it.
            return type_coerce(self.expr, self.type.impl_instance)[index]

        def __reduce__(self) -> Any:
            return (_comparator_of, (self.expr,))

    return Comparator


def _comparator_of(expr: Any) -> Any:
    return expr.type.comparator_factory(expr)
