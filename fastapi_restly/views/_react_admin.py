"""
React Admin compatible views for fastapi-restly.

Implements the ra-data-simple-rest HTTP contract for list:
- response body: plain JSON array
- sort:   sort=["field","ASC|DESC"]
- range:  range=[start, end]  (both inclusive, e.g. [0,24] = 25 items)
- filter: filter={"field":"value"} or filter={"id":[1,2,3]} for getMany
- Content-Range: items 0-24/315
"""

import functools
import json
from dataclasses import dataclass
from typing import Annotated, Any, ClassVar, Protocol, Sequence, cast

import fastapi
import pydantic
import sqlalchemy
from sqlalchemy.orm import RelationshipProperty
from starlette.datastructures import QueryParams

from .._pagination import _DEFAULT_PAGINATION, NoPagination, NumberedPagination
from ..exc import BadQueryParam, RestlyConfigurationError
from ..query._impl import _MAX_PAGINATION_OFFSET
from ..query._shared import _append_pk_tiebreak
from ._async import AsyncRestView
from ._base import (
    _LIST_SETTING_DEFAULTS,
    ListResult,
    ResponseShape,
    _annotate,
    _how_set,
    _list_filter_methods,
    _list_setting,
    _owner,
    _schema_response,
    _set_by,
    _typed_id_route,
    _view_id_type,
    get,
    put,
)
from ._sync import RestView

#: Default page size used when the react-admin client does not send a `range`
#: query parameter. Override per view with
#: ``pagination = fr.NumberedPagination(default_page_size=...)``.
DEFAULT_REACT_ADMIN_PAGE_SIZE = 25


@dataclass(frozen=True)
class _ReactAdminListParams:
    sort: tuple[str, str] | None
    start: int
    end: int
    filters: dict


class _ReactAdminQueryParams(pydantic.BaseModel):
    """The list params of a react-admin view: the raw strings of its three
    query keys."""

    sort: str | None = None
    range: str | None = None
    filter: str | None = None


# ---------------------------------------------------------------------------
# Query parsing helpers (standalone, analogous to query/_impl.py)
# ---------------------------------------------------------------------------


def parse_react_admin_sort(sort_raw: str | None) -> tuple[str, str] | None:
    """
    Parse a react-admin sort query parameter.

    Expected: '["field","ASC"]' or '["field","DESC"]'
    Returns (field, direction) or None if absent.
    Raises BadQueryParam (400) on malformed input.
    """
    if not sort_raw:
        return None
    try:
        parsed = json.loads(sort_raw)
    except json.JSONDecodeError:
        raise BadQueryParam("Invalid sort parameter: must be a JSON array")
    if not isinstance(parsed, list) or len(parsed) != 2:
        raise BadQueryParam("Invalid sort parameter: must be [field, direction]")
    field, direction = parsed
    if not isinstance(field, str) or direction not in ("ASC", "DESC"):
        raise BadQueryParam("Invalid sort parameter: direction must be 'ASC' or 'DESC'")
    return field, direction


def parse_react_admin_range(
    range_raw: str | None, default_page_size: int = DEFAULT_REACT_ADMIN_PAGE_SIZE
) -> tuple[int, int]:
    """
    Parse a react-admin range query parameter.

    Expected: '[0,24]' (both inclusive).
    Returns (start, end). Defaults to (0, default_page_size - 1) if absent.
    Raises BadQueryParam (400) on malformed input.
    """
    if not range_raw:
        return 0, default_page_size - 1
    try:
        parsed = json.loads(range_raw)
    except json.JSONDecodeError:
        raise BadQueryParam("Invalid range parameter: must be a JSON array")
    if not isinstance(parsed, list) or len(parsed) != 2:
        raise BadQueryParam("Invalid range parameter: must be [start, end]")
    start, end = parsed
    if not isinstance(start, int) or not isinstance(end, int):
        raise BadQueryParam("Invalid range parameter: values must be integers")
    return start, end


def parse_react_admin_filter(filter_raw: str | None) -> dict:
    """
    Parse a react-admin filter query parameter.

    Expected: '{"field":"value"}' or '{"id":[1,2,3]}' for getMany.
    Returns a dict. Defaults to {} if absent.
    Raises BadQueryParam (400) on malformed input.
    """
    if not filter_raw:
        return {}
    try:
        parsed = json.loads(filter_raw)
    except json.JSONDecodeError:
        raise BadQueryParam("Invalid filter parameter: must be a JSON object")
    if not isinstance(parsed, dict):
        raise BadQueryParam("Invalid filter parameter: must be a JSON object")
    return parsed


def _resolve_column(
    model: type[Any], schema_cls: Any, field_name: str, *, kind: str = "filter"
) -> Any:
    """
    Resolve a PUBLIC schema field name (or alias) to its SQLAlchemy column.

    Strict: only fields that the response class sends may be filtered or
    sorted. A column that exists on the model but is omitted from the response
    class (or never sent: WriteOnly or ``exclude=True``) is rejected, so the
    list endpoint cannot be used as an oracle to filter/sort on -- and thereby
    probe -- hidden data. Mirrors the standard REST dialect's schema-driven
    resolution.

    Raises BadQueryParam (400) if the field is not a public, filterable schema field.
    """
    resolved_name: str | None = None
    if schema_cls is not None:
        for name, field in schema_cls.model_fields.items():
            if field.exclude is True:
                continue
            if name == field_name or field.alias == field_name:
                resolved_name = name
                break

    if resolved_name is None:
        raise BadQueryParam(f"Unknown {kind} key: {field_name!r}")

    col = getattr(model, resolved_name, None)
    if col is None:
        raise BadQueryParam(f"Unknown {kind} key: {field_name!r}")
    if hasattr(col, "property") and isinstance(col.property, RelationshipProperty):
        raise BadQueryParam(
            f"Cannot sort or filter by relationship field: {field_name!r}"
        )
    return col


def _coerce_value(col: Any, value: Any, name: str) -> Any:
    """Validate a filter value as the column's Python type.

    The value comes from the JSON of ``filter``, so a date or a UUID arrives
    as a string. Pydantic turns it into the column's type, as the standard
    dialect does.

    :param name: The public name that the client used, for the message.
    :raises BadQueryParam: when the value is not valid for the column.
    """
    try:
        py_type = col.type.python_type
    except NotImplementedError:
        return value
    if isinstance(value, py_type):
        return value
    try:
        return _filter_value_adapter(py_type).validate_python(value)
    except pydantic.ValidationError as exc:
        reason = exc.errors()[0]["msg"]
        raise BadQueryParam(
            f"Invalid filter value for {name!r}: {value!r}. {reason}"
        ) from exc


@functools.cache
def _filter_value_adapter(py_type: type) -> pydantic.TypeAdapter[Any]:
    if py_type is str:
        # A number filters a text column, as ``str(5)`` did.
        config = pydantic.ConfigDict(coerce_numbers_to_str=True)
        return pydantic.TypeAdapter(str, config=config)
    try:
        return pydantic.TypeAdapter(py_type)
    except pydantic.PydanticSchemaGenerationError:
        # A type that Pydantic does not know: call it with the value.
        def construct(value: Any) -> Any:
            try:
                return py_type(value)
            except (TypeError, ArithmeticError) as exc:
                raise ValueError(str(exc)) from exc

        return pydantic.TypeAdapter(Annotated[Any, pydantic.AfterValidator(construct)])


def _apply_react_admin_filters(
    query: sqlalchemy.Select, model: type[Any], schema_cls: Any, filters: dict
) -> sqlalchemy.Select:
    """Apply a react-admin filter dict to a select query."""
    for key, value in filters.items():
        col = _resolve_column(model, schema_cls, key)
        if isinstance(value, list):
            coerced = [_coerce_value(col, v, key) for v in value]
            query = query.where(col.in_(coerced))
        else:
            query = query.where(col == _coerce_value(col, value, key))
    return query


def _apply_react_admin_sort_and_range(
    query: sqlalchemy.Select,
    model: type[Any],
    schema_cls: Any,
    sort: tuple[str, str] | None,
    start: int,
    end: int,
) -> sqlalchemy.Select:
    """The sort and the range (limit/offset) of a react-admin list."""
    sorted_on = []
    if sort:
        field, direction = sort
        col = _resolve_column(model, schema_cls, field, kind="sort")
        order_fn = sqlalchemy.desc if direction == "DESC" else sqlalchemy.asc
        query = query.order_by(order_fn(col))
        sorted_on.append(col)
    query = _append_pk_tiebreak(query, model, sorted_on)

    query = query.limit(end - start + 1).offset(start)
    return query


# ---------------------------------------------------------------------------
# Shared implementation mixin
# ---------------------------------------------------------------------------


class _ReactAdminViewProtocol(Protocol):
    request: fastapi.Request
    model: ClassVar[type[Any]]
    schema_response: ClassVar[type[pydantic.BaseModel]]
    schema_update: ClassVar[type[pydantic.BaseModel]]
    schema_list_params: ClassVar[type[pydantic.BaseModel]]
    pagination: ClassVar[NumberedPagination | NoPagination | None]
    get_many_endpoint: ClassVar[Any]
    put: ClassVar[Any]

    def get_react_admin_range_unit(self) -> str: ...
    def get_relationship_loader_options(self) -> list[Any]: ...
    def _single_response(self, obj: Any) -> pydantic.BaseModel: ...
    def _reject_unknown_keys(self, query: QueryParams, allowed: set[str]) -> None: ...

    @classmethod
    def before_include_view(cls) -> None: ...


class _ReactAdminMixin:
    """
    Shared transport helpers for react-admin views.

    This is an implementation detail shared by ReactAdminView and
    AsyncReactAdminView. User-facing customization should happen by
    subclassing one of those concrete view classes.

    Set ``pagination = fr.NumberedPagination(default_page_size=...)`` on a
    subclass to change the implicit page size used when the client does not
    send a ``range`` query parameter. Its ``max_page_size`` caps the rows one
    ``range`` asks for. The other pagination settings do not apply:
    react-admin pages with ``range`` and reports the total in the
    ``Content-Range`` header.

    Type annotations on the mixin methods use ``_ReactAdminViewProtocol`` to
    make the expected ``RestView`` surface explicit to static checkers.
    """

    #: Only ``default_page_size`` and ``max_page_size`` apply: the implicit
    #: page size when no ``range`` parameter is sent, and the most rows one
    #: ``range`` may ask for. Override per view.
    pagination: ClassVar[NumberedPagination | NoPagination | None] = NumberedPagination(
        default_page_size=DEFAULT_REACT_ADMIN_PAGE_SIZE
    )

    def get_react_admin_range_unit(self) -> str:
        """Return the unit string used in the Content-Range header."""
        return "items"

    @classmethod
    def _check_declared_schema_list_params(cls) -> None:
        # react-admin reads its own sort/range/filter grammar, not the
        # pagination's page parameters
        return None

    #: The whole react-admin query contract. The keys of the default list
    #: params (``page``, ``page_size``, per-field filters) are not part of
    #: this dialect, so they are unknown keys here like any other typo.
    react_admin_query_params: ClassVar[frozenset[str]] = frozenset(
        {"sort", "range", "filter"}
    )

    def _reject_unknown_query_params(self) -> None:
        """Reject any query key outside the react-admin contract.

        Overrides the default list params guard: this dialect takes three
        JSON-encoded keys, not the schema-derived grammar, so the inherited
        allow-list would wave through ``?page=2`` while still missing a typo.
        ``extra_query_params`` and ``unknown_query_params`` work as on the
        default list routes.
        """
        view = cast(_ReactAdminViewProtocol, self)
        request = getattr(view, "request", None)
        if request is None:
            return
        view._reject_unknown_keys(
            request.query_params, set(self.react_admin_query_params)
        )

    def _parse_react_admin_params(self) -> _ReactAdminListParams:
        """Parse sort, range, and filter from the current request query string.

        The unknown-key guard runs in :meth:`_coerce_react_admin_params`,
        not through the registration wrapper: the react-admin list endpoint
        declares no ``list_params`` argument.
        """
        view = cast(_ReactAdminViewProtocol, self)
        return self._coerce_react_admin_params(view.request.query_params)

    def _coerce_react_admin_params(self, params: Any) -> _ReactAdminListParams:
        if isinstance(params, _ReactAdminListParams):
            return params
        if isinstance(params, pydantic.BaseModel):
            params = params.model_dump()

        view = cast(_ReactAdminViewProtocol, self)
        if isinstance(params, QueryParams):
            # the query of a request: a key the route reads is not unknown
            view._reject_unknown_keys(params, set(self.react_admin_query_params))
        # ``pagination`` is required on react-admin (see before_include_view),
        # and NumberedPagination checks default_page_size when it is created.
        pagination = cast(NumberedPagination, view.pagination)
        default_page_size = pagination.default_page_size
        sort_raw = params.get("sort") if hasattr(params, "get") else None
        range_raw = params.get("range") if hasattr(params, "get") else None
        filter_raw = params.get("filter") if hasattr(params, "get") else None

        sort = (
            tuple(sort_raw)
            if isinstance(sort_raw, list | tuple)
            else parse_react_admin_sort(sort_raw)
        )
        if isinstance(range_raw, list | tuple):
            if len(range_raw) != 2:
                raise BadQueryParam("Invalid range parameter: must be [start, end]")
            start, end = range_raw
            if not isinstance(start, int) or not isinstance(end, int):
                raise BadQueryParam("Invalid range parameter: values must be integers")
        else:
            start, end = parse_react_admin_range(
                range_raw, default_page_size=default_page_size
            )
        filters = (
            filter_raw
            if isinstance(filter_raw, dict)
            else parse_react_admin_filter(filter_raw)
        )
        if start < 0 or end < start:
            raise BadQueryParam(
                "Invalid range parameter: must be [start, end] with 0 <= start <= end"
            )
        if end > _MAX_PAGINATION_OFFSET:
            raise BadQueryParam(
                f"Invalid range parameter: end must be at most {_MAX_PAGINATION_OFFSET}"
            )
        if end - start + 1 > pagination.max_page_size:
            raise BadQueryParam(
                "Invalid range parameter: a range can ask for at most "
                f"{pagination.max_page_size} rows"
            )
        return _ReactAdminListParams(sort=sort, start=start, end=end, filters=filters)

    def _react_admin_filters(
        self, stmt: sqlalchemy.Select, params: _ReactAdminListParams
    ) -> sqlalchemy.Select:
        view = cast(_ReactAdminViewProtocol, self)
        return _apply_react_admin_filters(
            stmt, view.model, _schema_response(type(self)), params.filters
        )

    def _react_admin_sort_and_range(
        self, stmt: sqlalchemy.Select, params: _ReactAdminListParams
    ) -> sqlalchemy.Select:
        view = cast(_ReactAdminViewProtocol, self)
        return _apply_react_admin_sort_and_range(
            stmt,
            view.model,
            _schema_response(type(self)),
            params.sort,
            params.start,
            params.end,
        )

    def _serialize_items(self, items: Sequence[Any]) -> list[dict]:
        """Serialize ORM objects to JSON-compatible dicts via the view's
        response class, as the other routes do: an instance of the view's
        schema from a ``to_single_response`` override goes out without its
        WriteOnly fields."""
        view = cast(_ReactAdminViewProtocol, self)
        return [
            view._single_response(obj).model_dump(mode="json", by_alias=True)
            for obj in items
        ]

    def _build_react_admin_list_response(
        self, serialized_items: list[dict], total: int, start: int, end: int
    ) -> fastapi.Response:
        """Build a JSON array response with a Content-Range header."""
        view = cast(_ReactAdminViewProtocol, self)
        unit = view.get_react_admin_range_unit()
        last = start + len(serialized_items) - 1 if serialized_items else start
        return fastapi.Response(
            content=json.dumps(serialized_items),
            media_type="application/json",
            headers={
                "Content-Range": f"{unit} {start}-{last}/{total}",
                "Access-Control-Expose-Headers": "Content-Range",
            },
        )

    def to_list_response(self, list_result: ListResult[Any]) -> fastapi.Response:
        """Build the react-admin list response: a JSON array with a
        ``Content-Range`` header."""
        params = self._coerce_react_admin_params(list_result.list_params)
        return self._build_react_admin_list_response(
            self._serialize_items(list_result.objects),
            list_result.total_count or 0,
            params.start,
            params.end,
        )

    @classmethod
    def before_include_view(cls) -> None:
        view_cls = cast(type[_ReactAdminViewProtocol], cls)
        # React-admin reports its total through the Content-Range header, which
        # needs the count query -- gated on ``pagination``. Disabling it would
        # report a total of 0 and silently break the client's paging.
        pagination = view_cls.pagination
        if not isinstance(pagination, NumberedPagination):
            raise RestlyConfigurationError(
                f"{cls.__name__}: a react-admin view cannot disable pagination; "
                "'pagination' must stay a fr.NumberedPagination so the "
                "Content-Range total is counted."
            )
        # react-admin reads the two page sizes; any other setting would be
        # silently ignored
        ignored = [
            name
            for name in (
                "max_page",
                "page_query_param",
                "page_size_query_param",
                "envelope",
            )
            if getattr(pagination, name) != getattr(_DEFAULT_PAGINATION, name)
        ]
        if ignored:
            raise RestlyConfigurationError(
                f"{cls.__name__}: a react-admin view uses only "
                "pagination.default_page_size and max_page_size. It pages with "
                "the 'range' parameter and reports the total in the "
                "Content-Range header, so these settings do not apply: "
                f"{', '.join(ignored)}."
            )
        # react-admin sends its own filter and sort parameters, so a list
        # setting other than the default would be silently ignored. A view
        # that inherits one sets the default again, to show it knows.
        for name, default in _LIST_SETTING_DEFAULTS.items():
            if _list_setting(cls, name) == default:
                continue
            if _owner(cls, name) is cls:
                fix = "Remove the setting."
            else:
                fix = f"Set {name} = {default!r} on {cls.__name__}."
            raise RestlyConfigurationError(
                f"{cls.__name__} {_how_set(cls, name)}, which a react-admin "
                "view does not use. React-admin sends its filters and sort "
                f"order in the 'filter' and 'sort' parameters, as JSON. {fix}"
            )
        for name in _list_filter_methods(cls):
            raise RestlyConfigurationError(
                f"{cls.__name__}.{name}{_set_by(cls, name)} has "
                "@fr.list_filter, which a react-admin view does not use. "
                "React-admin sends its filters in the 'filter' parameter, as "
                "JSON. Override apply_filters to read another filter."
            )
        if "schema_list_params" not in cls.__dict__:
            view_cls.schema_list_params = _ReactAdminQueryParams
        cast(Any, super()).before_include_view()
        # Override the list return annotation set by BaseRestView to Response,
        # since we return a raw Response with Content-Range header.
        if hasattr(view_cls, "get_many_endpoint"):
            _annotate(view_cls.get_many_endpoint, return_annotation=fastapi.Response)
        # Annotate the PUT handler with the same schema/types as PATCH.
        if hasattr(view_cls, "put"):
            _annotate(
                view_cls.put,
                return_annotation=view_cls.schema_response,
                schema_obj=view_cls.schema_update,
                id=_view_id_type(view_cls),
            )


# ---------------------------------------------------------------------------
# Concrete view classes
# ---------------------------------------------------------------------------


class AsyncReactAdminView(_ReactAdminMixin, AsyncRestView):
    """
    AsyncRestView that follows the ra-data-simple-rest HTTP contract.

    Use this instead of AsyncRestView when your frontend is react-admin
    with ra-data-simple-rest.
    """

    @get("/")
    async def get_many_endpoint(self) -> Any:
        result = await self.handle_get_many(self._parse_react_admin_params())
        return self.to_response(result, ResponseShape.LIST)

    async def apply_list_params(
        self, query: sqlalchemy.Select, list_params: Any
    ) -> sqlalchemy.Select:
        """Apply the react-admin list grammar to the scoped base query:
        :meth:`apply_filters`, then ``sort`` and ``range``."""
        params = self._coerce_react_admin_params(list_params)
        query = await self.apply_filters(query, params)
        return self._react_admin_sort_and_range(query, params)

    async def apply_filters(
        self, stmt: sqlalchemy.Select, list_params: Any
    ) -> sqlalchemy.Select:
        """Apply the react-admin ``filter`` to ``stmt``, without the sort and
        the range."""
        return self._react_admin_filters(
            stmt, self._coerce_react_admin_params(list_params)
        )

    @_typed_id_route
    @put("/{id}")
    async def put(self, id: Any, schema_obj: Any) -> Any:
        obj = await self.handle_update(id, schema_obj)
        return self.to_response(obj)


class ReactAdminView(_ReactAdminMixin, RestView):
    """
    RestView that follows the ra-data-simple-rest HTTP contract.

    Use this instead of RestView when your frontend is react-admin
    with ra-data-simple-rest.
    """

    @get("/")
    def get_many_endpoint(self) -> Any:
        result = self.handle_get_many(self._parse_react_admin_params())
        return self.to_response(result, ResponseShape.LIST)

    def apply_list_params(
        self, query: sqlalchemy.Select, list_params: Any
    ) -> sqlalchemy.Select:
        """Apply the react-admin list grammar to the scoped base query:
        :meth:`apply_filters`, then ``sort`` and ``range``."""
        params = self._coerce_react_admin_params(list_params)
        query = self.apply_filters(query, params)
        return self._react_admin_sort_and_range(query, params)

    def apply_filters(
        self, stmt: sqlalchemy.Select, list_params: Any
    ) -> sqlalchemy.Select:
        """Apply the react-admin ``filter`` to ``stmt``, without the sort and
        the range."""
        return self._react_admin_filters(
            stmt, self._coerce_react_admin_params(list_params)
        )

    @_typed_id_route
    @put("/{id}")
    def put(self, id: Any, schema_obj: Any) -> Any:
        obj = self.handle_update(id, schema_obj)
        return self.to_response(obj)
