"""The list params of a view.

``list_filter`` marks a method as a filter of its own. This module reads the
list settings of a view and checks its list methods. It builds the FastAPI
dependency that validates the query string of a list route, refuses an
unknown query key, and writes the OpenAPI parameters.
"""

import inspect
import types
import weakref
from collections.abc import Iterator, Mapping, Set
from typing import (
    Annotated,
    Any,
    Callable,
    Iterable,
    Sequence,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

import fastapi
import pydantic
from fastapi.dependencies.models import Dependant
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.models import APIKeyIn
from fastapi.security.api_key import APIKeyBase
from pydantic.fields import FieldInfo
from sqlalchemy import ColumnElement, Select
from starlette.datastructures import QueryParams
from typing_extensions import TypeVar

from .._pagination import _SORT_QUERY_PARAM
from .._routes import matched_route
from ..clauses import UNSCOPED, WhereClause, where_clause
from ..clauses._runtime import _guard_statement_tables
from ..exc import RestlyConfigurationError
from ..query._impl import (
    _KEY_SETTING_DEFAULTS,
    _NO_DEFAULT,
    _check_default_sort,
    _check_key_setting,
    _inline_definitions,
    _none_in_mapping_message,
    _OwnFilter,
    _query_key,
    _record_of,
    _split_key,
)
from ._guards import _owner, _route_parameters, _set_by
from ._reads import _clause_element

_F = TypeVar("_F", bound=Callable[..., Any])


def list_filter(key: str) -> Callable[[_F], _F]:
    """Make a view method the filter for the query key ``key``.

    For a filter that the generated keys do not give::

        @fr.list_filter("popular")
        def filter_popular(self, value: bool) -> ColumnElement[bool]:
            popular = Item.view_count > 1000
            return popular if value else ~popular

    The method takes one value after ``self``. Its type becomes the type of
    the key in the list params, so OpenAPI shows it and a bad value gets
    ``422``. The docstring becomes the description of the key. Without a
    default, the view calls the method when the key has a value. With a
    default, it calls the method on every list request, with the default
    when the key is missing.

    The method returns a SQLAlchemy condition, a clause or ``None`` for no
    condition. The view adds it to the statement with AND, inside the scope.
    The condition may use only the tables that are in the statement; use a
    relationship (``.has()``/``.any()``) for another table. On an async view
    the method can be ``async def``.

    The key is a name of your own, such as ``popular``, or a field and an
    operator, such as ``name__length``. The operator may be one that Restly
    generates for other fields, or a new one. Before the last ``__`` comes a
    field with filters, as in the generated keys, such as ``vendor.name``.
    The key is the key in the URL, so a field goes by its alias when it has
    one. The key settings, such as ``exclude_filters``, use Python names.

    The key is always on: ``include_filters`` and ``exclude_filters`` choose
    among the generated keys only. A subclass drops the key by setting the
    method name to ``None``.

    :raises RestlyConfigurationError: ``key`` is not a non-empty string,
        starts with ``_``, is the name of a Pydantic model attribute, or has
        ``__`` without one field before it and one operator without ``.``
        after it. A key that is a page parameter, the
        sort parameter or a generated key that is on fails when the view
        registers, and so does a key with ``__`` or ``.`` that does not start
        with a field with filters.
    """
    _check_own_key_name(key)

    def mark(method: _F) -> _F:
        method._fr_list_filter = key  # type: ignore[attr-defined]
        return method

    return mark


def _check_own_key_name(key: Any) -> None:
    """Reject a key that cannot be a query parameter of the list params.

    The last ``__`` splits a field from an operator, as in the generated
    keys. The view checks the field when it registers.
    """
    if not isinstance(key, str) or not key:
        raise RestlyConfigurationError(
            f"@fr.list_filter needs a key as a non-empty string, got {key!r}."
        )
    field, separator, operator = key.rpartition("__")
    if separator and "." in operator:
        raise RestlyConfigurationError(
            f"@fr.list_filter({key!r}): an operator has no '.'. A '.' goes in "
            "the field, as in 'vendor.name__startswith'."
        )
    if separator and (not field or not operator or "__" in field):
        raise RestlyConfigurationError(
            f"@fr.list_filter({key!r}): '__' splits a field from an operator, "
            "as in 'name__startswith'. Write it once, with a field before it "
            "and an operator after it."
        )
    # the list params are a Pydantic model with one field per key
    if key.startswith("_") or hasattr(pydantic.BaseModel, key):
        raise RestlyConfigurationError(
            f"@fr.list_filter({key!r}): a key cannot start with '_' or be "
            "the name of a Pydantic model attribute."
        )


def _list_filter_methods(cls: type) -> dict[str, Any]:
    """The methods of ``cls`` with ``@fr.list_filter``, by name, in the
    order that the classes define them.

    A name set to ``None`` drops the filter of a base class.

    :raises RestlyConfigurationError: a method overrides a filter method
        without the decorator.
    """
    names: dict[str, None] = {}
    for klass in reversed(cls.__mro__):
        names.update(dict.fromkeys(vars(klass)))
    methods: dict[str, Any] = {}
    for name in names:
        marked = [
            klass
            for klass in cls.__mro__
            if hasattr(vars(klass).get(name), "_fr_list_filter")
        ]
        if not marked:
            continue
        value = inspect.getattr_static(cls, name)
        if value is None:
            continue
        if not hasattr(value, "_fr_list_filter"):
            key = getattr(vars(marked[0])[name], "_fr_list_filter")
            raise RestlyConfigurationError(
                f"{cls.__name__}.{name}{_set_by(cls, name)} overrides the "
                f"filter method of {marked[0].__name__} without "
                f"@fr.list_filter. Add @fr.list_filter({key!r}) to keep the "
                f"key, or set {name} = None to drop it."
            )
        methods[name] = value
    return methods


def _own_filters(cls: type) -> list[_OwnFilter]:
    """The own filter keys of a view, with the types of their values.

    :raises RestlyConfigurationError: two methods have one key, or a method
        does not take one value with a type.
    """
    filters: list[_OwnFilter] = []
    method_of: dict[str, str] = {}
    for name, method in _list_filter_methods(cls).items():
        label = f"{cls.__name__}.{name}"
        key = method._fr_list_filter
        if key in method_of:
            raise RestlyConfigurationError(
                f"{cls.__name__}: {method_of[key]} and {name} are both the "
                f"filter for the key {key!r}."
            )
        method_of[key] = name
        parameters = list(_route_parameters(method).values())[1:]
        if len(parameters) != 1 or parameters[0].kind not in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            raise RestlyConfigurationError(
                f"{label} must take one value after self, as in "
                f"'def {name}(self, value: bool)'."
            )
        parameter = parameters[0]
        if parameter.annotation is inspect.Parameter.empty:
            raise RestlyConfigurationError(
                f"{label} needs a type on {parameter.name!r}, such as "
                f"'{parameter.name}: bool'. The type checks the value from the "
                "URL, and OpenAPI shows it."
            )
        # Only the value's type is evaluated: the return type may name a
        # class that the module imports under TYPE_CHECKING.
        holder = types.SimpleNamespace(
            __annotations__={parameter.name: parameter.annotation}
        )
        try:
            annotation = get_type_hints(
                holder,
                globalns=getattr(inspect.unwrap(method), "__globals__", {}),
                include_extras=True,
            )[parameter.name]
        except NameError as exc:
            raise RestlyConfigurationError(
                f"{label}: the type of {parameter.name!r} cannot be resolved: {exc}."
            ) from exc
        filters.append(
            _OwnFilter(
                key=key,
                method=name,
                annotation=annotation,
                default=(
                    _NO_DEFAULT
                    if parameter.default is inspect.Parameter.empty
                    else parameter.default
                ),
                description=inspect.getdoc(method),
            )
        )
    return filters


def _apply_own_filters(
    stmt: Select[Any], results: Iterable[tuple[str, Any]]
) -> Select[Any]:
    """Add what the filter methods returned to ``stmt``, with AND.

    :param results: The label of each method, such as
        ``ItemView.filter_popular``, and what the method returned.

    :raises TypeError: a method returned anything other than a condition,
        a clause or ``None``, or a condition on a table that is not in the
        statement.
    """
    conditions: list[ColumnElement[bool]] = []
    for label, result in results:
        if result is None or result is UNSCOPED:
            continue
        result = _clause_element(result)
        if isinstance(result, ColumnElement):
            # applying it as a clause fills members such as Current.user_id
            result = where_clause(result)
        if type(result) is bool:
            raise TypeError(
                f"{label} returned a bool, not a SQL expression. A comparison "
                "on an instance attribute is a bool; compare the column "
                "instead (Item.name == value)."
            )
        if not isinstance(result, WhereClause):
            raise TypeError(
                f"{label} must return a SQLAlchemy boolean expression, a "
                f"clause or None, got {type(result).__name__}."
            )
        condition = result()
        # a table outside the statement would be a cross join that the scope
        # does not reach
        try:
            _guard_statement_tables(stmt, [condition])
        except TypeError as exc:
            raise TypeError(f"{label}: {exc}") from None
        conditions.append(condition)
    return stmt.where(*conditions) if conditions else stmt


#: The view settings that Restly generates the list params with, besides
#: the pagination, and their defaults.
_LIST_SETTING_DEFAULTS: dict[str, Any] = {
    **_KEY_SETTING_DEFAULTS,
    "sort_query_param": _SORT_QUERY_PARAM,
    "default_sort": None,
}


def _list_setting(cls: type, name: str) -> Any:
    """The value of a list setting of a view, as it is recorded.

    :raises RestlyConfigurationError: a key setting is not keys, or a
        ``default_sort`` is not sort keys, such as one that a mixin sets,
        which is not checked when it is defined.
    """
    value = getattr(cls, name)
    label = f"{cls.__name__}.{name}{_set_by(cls, name)}"
    if name == "sort_query_param":
        return value
    if name == "default_sort":
        _check_default_sort(label, value)
        return value
    return _check_key_setting(label, value)


#: The list methods that an async view awaits, and a sync view calls.
_LIST_METHODS = ("apply_list_params", "apply_filters")


def _check_list_methods(cls: type, *, is_async: bool) -> None:
    """Reject a list method of the wrong kind when the class is defined.

    An async view awaits :meth:`apply_list_params` and :meth:`apply_filters`,
    so a plain ``def`` fails there on the first request, and a sync view
    would get a coroutine instead of a statement. A sync view also calls its
    filter methods without await. A filter method that is overridden
    without ``@fr.list_filter`` fails here too.
    """
    filter_methods = _list_filter_methods(cls)
    if not is_async:
        for name, method in filter_methods.items():
            if inspect.iscoroutinefunction(method):
                raise RestlyConfigurationError(
                    f"{cls.__name__}.{name}{_set_by(cls, name)} is 'async "
                    "def', but a sync view calls it without await. Write "
                    f"'def {name}', or use fr.AsyncRestView."
                )
    for name in _LIST_METHODS:
        method = getattr(cls, name, None)
        if method is None or inspect.iscoroutinefunction(method) == is_async:
            continue
        origin = _set_by(cls, name)
        if is_async:
            raise RestlyConfigurationError(
                f"{cls.__name__}.{name}{origin} must be 'async def' on an "
                f"async view, which awaits it. Write 'async def {name}', and "
                f"'await super().{name}(...)'."
            )
        raise RestlyConfigurationError(
            f"{cls.__name__}.{name}{origin} is 'async def', but a sync view "
            f"calls it without await. Write 'def {name}', or use "
            "fr.AsyncRestView."
        )


def _how_set(cls: type, name: str) -> str:
    """How ``cls`` has the list setting ``name``, for a message, such as
    ``inherits include_filters = ('*', '*.*') from AppView``."""
    value = _list_setting(cls, name)
    setter = _owner(cls, name)
    if value == _LIST_SETTING_DEFAULTS[name]:
        return f"has {name} = {value!r}"
    if setter is cls:
        return f"sets {name} = {value!r}"
    return f"inherits {name} = {value!r} from {setter.__name__}"


def reject_unknown_query_keys(
    query: QueryParams, allowed: set[str], request: fastapi.Request | None
) -> None:
    """Raise a 422 naming every key of ``query`` that is not in ``allowed``.

    The list params decide what is allowed: their keys for the default list
    params, ``sort``/``range``/``filter`` for react-admin. A key that the
    route of ``request`` reads elsewhere is never unknown (see
    :func:`_route_query_keys`).

    :raises RequestValidationError: ``query`` has an unknown key.
    """
    unknown = set(query.keys()) - allowed
    if unknown and request is not None:
        unknown -= _route_query_keys(request)
    if unknown:
        raise _unknown_keys_error(query, unknown)


def _unknown_keys_error(
    query: Mapping[str, Any], keys: set[str]
) -> RequestValidationError:
    """The error for unknown query keys, in the shape of FastAPI's own
    validation errors."""
    return RequestValidationError(
        [
            {
                "type": "extra_forbidden",
                "loc": ("query", key),
                "msg": f"Unknown query parameter {key!r}",
                "input": query.get(key),
            }
            for key in sorted(keys)
        ]
    )


def _route_query_keys(request: fastapi.Request) -> set[str]:
    """The query keys that the matched route reads.

    Every query parameter its dependency tree declares, at any level: the
    app or a router, the view's ``dependencies``, a class attribute, the
    endpoint. Also the key of a query API key scheme such as
    ``APIKeyQuery``, which reads the query string without declaring a
    parameter. The guard walks the tree only for a key that the list
    params do not have.
    """
    dependant = getattr(matched_route(request.scope), "dependant", None)
    keys: set[str] = set()
    if dependant is None:
        return keys
    for current in _dependants(dependant):
        keys.update(_field_key(field) for field in current.query_params)
        call = current.call
        if isinstance(call, APIKeyBase) and call.model.in_ == APIKeyIn.query:
            keys.add(call.model.name)
    return keys


def _dependants(dependant: Dependant) -> Iterator[Dependant]:
    """``dependant`` and every dependency below it."""
    pending = [dependant]
    while pending:
        current = pending.pop()
        yield current
        pending.extend(current.dependencies)


def _field_key(field: Any) -> str:
    """The name that FastAPI reads the value of a query parameter from."""
    return getattr(field, "validation_alias", None) or field.alias


def _check_route_query_keys(
    view_name: str, route_name: str, dependant: Dependant, list_keys: Iterable[str]
) -> None:
    """Reject a route that reads a key of its list params itself.

    One value would fill both, and OpenAPI would show the key twice, which it
    does not allow. A parameter outside the schema does neither. A dependency
    on the app or on a router is not known yet when a view registers.

    :raises RestlyConfigurationError: the route reads such a key.
    """
    keys = set(list_keys)
    for current in _dependants(dependant):
        for field in current.query_params:
            key = _field_key(field)
            if key in keys and getattr(field.field_info, "include_in_schema", True):
                raise RestlyConfigurationError(
                    f"{view_name}.{route_name} reads the query key {key!r} itself, "
                    "and the list params of the view read it too. One value would "
                    "fill both, and OpenAPI would show the key twice. Read the value "
                    "from list_params, or give the parameter of the route another "
                    "name."
                )


def _list_params_dependency(
    params_model: type[pydantic.BaseModel],
) -> Callable[..., pydantic.BaseModel]:
    """A dependency that validates ``params_model`` from the query string.

    FastAPI splits a query model into its fields only when it is the sole
    query parameter: at runtime per function, in OpenAPI across the route.
    Any other query parameter therefore collapses it into one required
    ``list_params`` value. This reads the fields itself, the way FastAPI
    hands a query model its input: every value of a repeated key for a
    sequence field, the last value otherwise, a default that is not None,
    and any other key as sent. :func:`_list_openapi_parameters`
    documents the fields.
    """
    fields = _list_params_fields(params_model)

    def list_params(request: fastapi.Request, first: Any = None) -> pydantic.BaseModel:
        return _validate_list_params(
            params_model, fields, request.query_params, request
        )

    parameters = [
        inspect.Parameter(
            "request", inspect.Parameter.KEYWORD_ONLY, annotation=fastapi.Request
        )
    ]
    if fields:
        # FastAPI documents a route's 422 only when it reads a parameter of
        # the route itself. It reads the first key here, undocumented and
        # unchecked; the model validates the value with the rest.
        marker = fastapi.Query(alias=fields[0][0], include_in_schema=False)
        parameters.append(
            inspect.Parameter(
                "first",
                inspect.Parameter.KEYWORD_ONLY,
                default=None,
                annotation=Annotated[Any, marker],
            )
        )
    list_params.__signature__ = inspect.Signature(parameters)  # type: ignore[attr-defined]
    return list_params


#: The query key of each field of a list params model, the field, and
#: whether the field reads every value of a repeated key.
_ListParamsFields = tuple[tuple[str, FieldInfo, bool], ...]

_LIST_PARAMS_FIELDS: "weakref.WeakKeyDictionary[type, _ListParamsFields]" = (
    weakref.WeakKeyDictionary()
)


def _list_params_fields(params_model: type[pydantic.BaseModel]) -> _ListParamsFields:
    # once per model: a raw QueryParams or mapping reads them on every call
    fields = _LIST_PARAMS_FIELDS.get(params_model)
    if fields is None:
        fields = tuple(
            (_query_key(name, field), field, _reads_every_value(field))
            for name, field in params_model.model_fields.items()
        )
        _LIST_PARAMS_FIELDS[params_model] = fields
    return fields


def _validate_list_params(
    params_model: type[pydantic.BaseModel],
    fields: _ListParamsFields,
    query: QueryParams | Mapping[str, list[Any]],
    request: fastapi.Request | None = None,
) -> pydantic.BaseModel:
    """Validate ``params_model`` from ``query``, as FastAPI validates a
    query model. A key of another reader on the route of ``request`` does not
    reach the model, so ``extra="forbid"`` accepts it.

    :param query: The query, or the values of each key, as
        :func:`_values_by_key` gives them for a mapping.
    :raises RequestValidationError: a key that takes one value is repeated,
        or a value fails the validation of its field. The location of each
        error starts with ``"query"``.
    """
    values_of = (
        {key: query.getlist(key) for key in query.keys()}
        if isinstance(query, QueryParams)
        else query
    )
    field_keys = {key for key, _, _ in fields}
    repeated = [
        _repeated_key_error(key, values_of[key], field_keys)
        for key, _, every_value in fields
        if not every_value and len(values_of.get(key, ())) > 1
    ]
    if repeated:
        raise RequestValidationError(repeated)
    data: dict[str, Any] = {}
    for key, field, every_value in fields:
        if key in values_of:
            values = values_of[key]
            # an empty list comes only from code; a key that takes one
            # value refuses it
            data[key] = values if every_value or not values else values[0]
        elif not field.is_required():
            default = field.get_default()
            if default is not None:
                data[key] = default
    # generated list params hold no other key: the view has its own rule for one
    # (unknown_query_params)
    others = (
        []
        if _record_of(params_model) is not None
        else [key for key in values_of if key not in field_keys]
    )
    if others and request is not None:
        route_keys = _route_query_keys(request)
        others = [key for key in others if key not in route_keys]
    for key in others:
        values = values_of[key]
        data[key] = values[0] if len(values) == 1 else values
    try:
        return params_model.model_validate(data)
    except pydantic.ValidationError as exc:
        errors = [
            {**error, "loc": ("query", *error["loc"])}
            for error in exc.errors(include_url=False)
        ]
        raise RequestValidationError(errors) from None


def _repeated_key_error(
    key: str, values: list[str], field_keys: Set[str]
) -> dict[str, Any]:
    """The error for a key that takes one value and that a client repeated.
    It names the key that takes several values, when there is one."""
    name, operator = _split_key(key)
    several = {"": f"{name}__in", "ne": f"{name}__not_in"}.get(operator)
    message = f"Send {key!r} only once. It takes one value."
    if several in field_keys:
        message += f" To send several values, use {several!r}."
    return {
        "type": "repeated_key",
        "loc": ("query", key),
        "msg": message,
        "input": values,
    }


def _validate_raw_list_params(
    params_model: type[pydantic.BaseModel], raw: Any
) -> pydantic.BaseModel:
    """Validate query parameters that a caller passes instead of list params.

    The values are validated as on the list route; see :func:`_values_by_key`
    for a mapping.

    :raises RequestValidationError: a key is not a key of ``params_model``,
        or a value fails the validation of its field.
    """
    fields = _list_params_fields(params_model)
    query = raw if isinstance(raw, QueryParams) else _values_by_key(raw)
    unknown = set(raw.keys()) - {key for key, _, _ in fields}
    if unknown:
        raise _unknown_keys_error(raw, unknown)
    if not isinstance(raw, QueryParams) and (
        error := _none_values_error(raw, {key for key, _, _ in fields})
    ):
        raise error
    return _validate_list_params(params_model, fields, query)


def _none_values_error(
    raw: Mapping[str, Any], field_keys: Set[str]
) -> RequestValidationError | None:
    """The error for each key of a mapping from code whose value is
    ``None``, or ``None`` when there is no such key.

    :param field_keys: The keys of the list params, to name the ``__isnull``
        key of a field only when it has one.
    """
    errors = []
    for key, value in raw.items():
        if value is not None:
            continue
        isnull_key = f"{_split_key(key)[0]}__isnull"
        message = _none_in_mapping_message(
            key, isnull_key if isnull_key in field_keys else None
        )
        errors.append(
            {"type": "none_value", "loc": ("query", key), "msg": message, "input": None}
        )
    return RequestValidationError(errors) if errors else None


def _values_by_key(raw: Mapping[str, Any]) -> dict[str, list[Any]]:
    """The values of each key of a mapping from code, such as
    ``{"id__in": allowed_ids}``.

    Each value stays a value, and is not made text. A list, a tuple or a set
    holds the values of a repeated key. An empty one stays empty: ``__in``
    then matches no rows, ``__not_in`` every row, and a key that takes one
    value refuses it. :func:`_validate_raw_list_params` refuses ``None``
    before this.
    """
    return {
        key: list(value)
        if isinstance(value, (list, tuple, set, frozenset))
        else [value]
        for key, value in raw.items()
    }


def _reads_every_value(field: FieldInfo) -> bool:
    """Whether a list params field reads every value of a repeated query key.

    FastAPI's rule for a query model: a sequence or set type, alone or in a
    union, unless the value is JSON.
    """
    if any(isinstance(item, pydantic.Json) for item in field.metadata):
        return False
    return _is_sequence_annotation(field.annotation)


def _is_sequence_annotation(annotation: Any) -> bool:
    if get_origin(annotation) in (Union, types.UnionType):
        return any(_is_sequence_annotation(arg) for arg in get_args(annotation))
    return any(
        isinstance(candidate, type)
        and get_origin(candidate) is None
        and issubclass(candidate, (Sequence, Set))
        and not issubclass(candidate, (str, bytes))
        for candidate in (annotation, get_origin(annotation))
    )


class _ListRouteExtra(dict[str, Any]):
    """The ``openapi_extra`` of a list route, with one OpenAPI parameter for
    each key of its list params.

    FastAPI reads ``openapi_extra`` when it writes OpenAPI, and only then does
    this write the parameters. The type of a filter key needs the complete
    schema class, and a schema can name a class that is defined after the
    view registers, as a route of FastAPI can.
    """

    def __init__(
        self, extra: Mapping[str, Any], parameters: Callable[[], list[dict[str, Any]]]
    ) -> None:
        super().__init__(extra)
        self._own_parameters = list(self.get("parameters", []))
        self._parameters = parameters
        # FastAPI skips an empty openapi_extra
        dict.__setitem__(self, "parameters", self._own_parameters)

    def _fill(self) -> None:
        dict.__setitem__(
            self, "parameters", [*self._parameters(), *self._own_parameters]
        )

    def items(self) -> Any:
        self._fill()
        return super().items()

    def __getitem__(self, key: str) -> Any:
        if key == "parameters":
            self._fill()
        return super().__getitem__(key)


def _list_openapi_parameters(
    params_model: type[pydantic.BaseModel],
) -> list[dict[str, Any]]:
    """One OpenAPI query parameter per field of ``params_model``.

    The entries FastAPI writes for a query model it splits into fields, so
    the list route documents the same whatever else the route reads. Built from
    the model's JSON schema, with references inlined: the document's
    components do not hold the model's definitions.
    """
    schema = params_model.model_json_schema()
    definitions = schema.get("$defs", {})
    properties = schema.get("properties", {})
    required = set(schema.get("required", ()))
    parameters: list[dict[str, Any]] = []
    for name, field in params_model.model_fields.items():
        key = _query_key(name, field)
        field_schema = _inline_definitions(
            properties.get(key, properties.get(name, {})), definitions
        )
        field_schema["title"] = field.title or key.title().replace("_", " ")
        # the parameter has the description, so the schema needs no copy
        if field.description:
            field_schema.pop("description", None)
        parameter: dict[str, Any] = {
            "name": key,
            "in": "query",
            "required": key in required,
            "schema": field_schema,
        }
        if field.description:
            parameter["description"] = field.description
        if field.deprecated:
            parameter["deprecated"] = True
        parameters.append(parameter)
    return parameters
