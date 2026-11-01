import functools
import types
from typing import Any, get_origin

import pydantic
from sqlalchemy import JSON, TypeDecorator, type_coerce
from sqlalchemy.engine import Dialect
from sqlalchemy.sql.operators import OperatorType
from sqlalchemy.types import TypeEngine

from ..schemas._base import _json_data, _reject_excluded_json_type, _type_label


class JSONDocument(TypeDecorator[Any]):
    """A JSON column that holds a Pydantic type, such as a model::

        class Address(pydantic.BaseModel):
            street: str
            city: str


        class Shop(fr.IDMixin, Base):
            address: Mapped[Address] = mapped_column(fr.JSONDocument(Address))

    The column validates the document when it loads a row, so
    ``shop.address`` is an ``Address``. A write validates the value too, so a
    dict works as well. Then it dumps the value as Pydantic dumps it in JSON
    mode: a nested model becomes a dict, and a ``datetime`` becomes text.
    Restly dumps a value for a plain ``JSON`` column in the same way. The type
    can be any type that Pydantic validates, such as ``dict[str, Address]``.

    The column refuses a type with a secret, such as ``SecretStr``, or with a
    field that has ``exclude=True``, such as a ``WriteOnly`` field: the dump
    would mask the secret or leave out the field. It also refuses a dataclass
    and an iterator type, such as ``Iterable``. It raises
    :class:`~fastapi_restly.exc.RestlyConfigurationError` when you define the
    column.

    On a base with :class:`ModelDefaults`, ``Mapped[Address]`` alone gives
    the column ``JSONDocument(Address)``, for each Pydantic model. The type
    map has ``pydantic.BaseModel: JSONDocument(pydantic.BaseModel)`` for it,
    as SQLAlchemy has ``enum.Enum: Enum(enum.Enum)`` for an enum. Another
    type, such as ``dict[str, Address]``, needs ``mapped_column``. Your own
    entry for ``pydantic.BaseModel``, such as
    ``JSONDocument(pydantic.BaseModel, impl=JSONB)``, wins.

    In all else, the column works as SQLAlchemy's ``JSON``. ``None`` is stored
    as JSON ``null``, and ``none_as_null=True`` stores SQL ``NULL``. A change
    inside the document, such as ``shop.address.city = "Utrecht"``, is not
    saved: assign a new value. A value in a query is dumped but not
    validated, because it can be a part of a document, as in
    ``Shop.address["city"].as_string() == "Utrecht"``.

    :param type_: The type of the document. It must be hashable, as the types
        above are.
    :param none_as_null: Store ``None`` as SQL ``NULL``, as in ``JSON``.
    :param impl: The JSON type of the column, such as ``JSONB`` on
        PostgreSQL. The default is ``JSON``.
    :raises RestlyConfigurationError: ``type_`` has a part that the dump
        would mask or leave out.
    """

    impl = JSON
    cache_ok = True

    def __init__(
        self, type_: Any, *, none_as_null: bool = False, impl: type[JSON] | None = None
    ) -> None:
        if impl is not None and not (isinstance(impl, type) and issubclass(impl, JSON)):
            raise TypeError(
                f"impl must be a JSON type class, such as JSONB, not {impl!r}. "
                "Pass none_as_null to JSONDocument."
            )
        super().__init__(none_as_null=none_as_null)
        if impl is not None:
            self.impl = impl(none_as_null=none_as_null)
        self.type_ = type_
        self.none_as_null = none_as_null
        self._label = f"{type(self).__name__}({_type_label(type_)})"
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
        ``JSONDocument(pydantic.BaseModel)`` makes a type for each model, with
        the same options. Another type accepts only its own class, as any
        SQLAlchemy type does. SQLAlchemy has no public way to do this yet.
        """
        if (
            self.type_ is pydantic.BaseModel
            and isinstance(python_type, type)
            and issubclass(python_type, pydantic.BaseModel)
        ):
            return type(self)(
                python_type,
                none_as_null=self.none_as_null,
                impl=type(self.impl_instance),
            )
        return super()._resolve_for_python_type(
            python_type, matched_on, matched_on_flattened
        )

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        """Validate ``value`` and dump it as JSON data."""
        if value is None or value is JSON.NULL:
            return value
        return _json_data(self._adapter.validate_python(value), self._label)

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        """Validate the stored document."""
        if value is None:
            return None
        return self._adapter.validate_python(value)

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

        :raises NotImplementedError: The type has no one class, as a union
            has not. SQLAlchemy raises it for such a type too.
        """
        origin = get_origin(self.type_) or self.type_
        if not isinstance(origin, type) or origin is types.UnionType:
            raise NotImplementedError
        return origin

    def __repr__(self) -> str:
        return self._label


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
    """The comparator of a :class:`JSONDocument` over a JSON type with
    ``impl_comparator``."""

    class Comparator(TypeDecorator.Comparator, impl_comparator):  # type: ignore[misc,valid-type]
        def __getitem__(self, index: Any) -> Any:
            # A part of a document is JSON, not a document. SQLAlchemy gives
            # it the type of the column, which would validate it.
            return type_coerce(self.expr, self.type.impl_instance)[index]

        def __reduce__(self) -> Any:
            return (_comparator_of, (self.expr,))

    return Comparator


def _comparator_of(expr: Any) -> Any:
    return expr.type.comparator_factory(expr)
