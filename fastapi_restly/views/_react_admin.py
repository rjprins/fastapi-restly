"""
React Admin compatible views for fastapi-restly.

Implements the ra-data-simple-rest HTTP contract for list:
- response body: plain JSON array
- sort:   sort=["field","ASC|DESC"]
- range:  range=[start, end]  (both inclusive, e.g. [0,24] = 25 items)
- filter: filter={"field":"value"} or filter={"id":[1,2,3]} for getMany
- Content-Range: items 0-24/315

The views are a user of Restly. They have list params of their own, and use
the public API, as a view of yours would. A comment names the ticket of each
private name that has no public form yet.
"""

import json
from typing import Any, ClassVar, Literal, Protocol, cast

import fastapi
import pydantic
import sqlalchemy
from fastapi.exceptions import RequestValidationError

from fastapi_restly.exc import RestlyConfigurationError
from fastapi_restly.query import FilterKey, SortKey, SortNulls
from fastapi_restly.views import (
    AsyncRestView,
    BaseRestView,
    ListResult,
    NoPagination,
    NumberedPagination,
    ResponseShape,
    RestView,
    ViewID,
    ViewResponse,
    ViewUpdate,
    get,
    put,
)

# Private names without a public form yet.
from ._base import _how_set, _list_filter_methods, _list_setting, _owner, _set_by

#: Default page size used when the react-admin client does not send a `range`
#: query parameter. Override per view with
#: ``pagination = fr.NumberedPagination(default_page_size=...)``.
DEFAULT_REACT_ADMIN_PAGE_SIZE = 25

#: The largest SQL offset: a database takes a signed 64-bit integer.
_MAX_OFFSET = 2**63 - 1

#: The settings of a ``NumberedPagination`` that react-admin does not use.
_UNUSED_PAGINATION_SETTINGS = (
    "max_page",
    "page_query_param",
    "page_size_query_param",
    "envelope",
)

#: The view settings that choose the keys of generated list params.
_LIST_SETTINGS = (
    "include_filters",
    "exclude_filters",
    "include_sort_keys",
    "exclude_sort_keys",
    "sort_query_param",
    "default_sort",
)


class ReactAdminListParams(pydantic.BaseModel):
    """The list params of a react-admin view: ``sort``, ``range`` and
    ``filter``.

    React-admin sends each key as JSON in the query string. The fields are
    :class:`pydantic.Json <pydantic.types.Json>`, so the view and your
    overrides get the parsed
    values: ``sort`` as ``("name", "DESC")``, ``range`` as ``(0, 24)`` and
    ``filter`` as a dict. JSON that does not have this shape gets a ``422``
    that names the place, as a bad value of other list params does.

    To add a query key, such as a search key, subclass this class and set the
    subclass as the view's ``schema_list_params``.
    """

    #: The sort order: a field name and ``"ASC"`` or ``"DESC"``, such as
    #: ``("name", "DESC")``. ``None`` when the request has no sort.
    sort: pydantic.Json[tuple[str, Literal["ASC", "DESC"]]] | None = pydantic.Field(
        None,
        description='The sort order, as JSON: ["field", "ASC"] or ["field", "DESC"].',
    )
    #: The first and the last row to return, both included, such as
    #: ``(0, 24)``. ``None`` when the request has no range.
    range: pydantic.Json[tuple[int, int]] | None = pydantic.Field(
        None,
        description=(
            "The rows to return, as JSON: [start, end]. Both rows are "
            "included, and the first row is 0."
        ),
    )
    #: The filters, as a dict from a field name to a value. ``None`` when
    #: the request has no filter.
    filter: pydantic.Json[dict[str, Any]] | None = pydantic.Field(
        None,
        description=(
            'The filters, as JSON: {"field": value}. A list of values, as in '
            '{"id": [1, 2, 3]}, matches each of them.'
        ),
    )

    @pydantic.field_validator("range")
    @classmethod
    def _check_range(cls, value: tuple[int, int] | None) -> tuple[int, int] | None:
        if value is None:
            return value
        start, end = value
        if start < 0:
            raise ValueError("the start of the range is less than 0")
        if end < start:
            raise ValueError("the end of the range comes before its start")
        if end > _MAX_OFFSET:
            # The database takes a signed 64-bit offset.
            raise ValueError(f"the end of the range is more than {_MAX_OFFSET}")
        return value


# ---------------------------------------------------------------------------
# Query helpers (standalone, analogous to query/_impl.py)
# ---------------------------------------------------------------------------


def _apply_react_admin_filters(
    stmt: sqlalchemy.Select, model: type[Any], schema_cls: Any, filters: dict
) -> sqlalchemy.Select:
    """Apply a react-admin filter dict to a select statement. A list of
    values matches each of them."""
    for name, value in filters.items():
        key = FilterKey(model, schema_cls, name)
        if isinstance(value, list):
            stmt = stmt.where(key.column.in_([key.parse(item) for item in value]))
        else:
            stmt = stmt.where(key.column == key.parse(value))
    return stmt


def _apply_react_admin_sort_and_range(
    stmt: sqlalchemy.Select,
    model: type[Any],
    schema_cls: Any,
    sort: tuple[str, str] | None,
    start: int,
    end: int,
    sort_nulls: SortNulls = "last",
) -> sqlalchemy.Select:
    """The sort and the range (limit/offset) of a react-admin list."""
    sorted_on: list[str] = []
    if sort:
        field, direction = sort
        key = SortKey(model, schema_cls, field)
        stmt = stmt.order_by(
            key.term(descending=direction == "DESC", sort_nulls=sort_nulls)
        )
        sorted_on.append(key.column.key)
    stmt = _order_by_primary_key(stmt, model, sorted_on)

    return stmt.limit(end - start + 1).offset(start)


def _order_by_primary_key(
    stmt: sqlalchemy.Select, model: type[Any], sorted_on: list[str]
) -> sqlalchemy.Select:
    """Order by the primary key last, so that a row with the same sort value
    as another one never moves to another page.

    :param sorted_on: The attributes that the list is sorted on already. A
        key column among them is not added again.
    """
    mapper = sqlalchemy.inspect(model)
    for column in mapper.primary_key:
        key = mapper.get_property_by_column(column).key
        if key not in sorted_on:
            stmt = stmt.order_by(getattr(model, key))
    return stmt


def _check_pagination(cls: type) -> None:
    """React-admin pages with ``range`` and reports the total in the
    ``Content-Range`` header. So a react-admin view needs the count of a
    ``NumberedPagination``, and uses only its two page sizes."""
    # Disabling the count would report a total of 0 and silently break the
    # client's paging.
    pagination = getattr(cls, "pagination", None)
    if not isinstance(pagination, NumberedPagination):
        raise RestlyConfigurationError(
            f"{cls.__name__}: a react-admin view cannot disable pagination; "
            "'pagination' must stay a fr.NumberedPagination so the "
            "Content-Range total is counted."
        )
    # any other setting would be silently ignored
    defaults = NumberedPagination()
    ignored = [
        name
        for name in _UNUSED_PAGINATION_SETTINGS
        if getattr(pagination, name) != getattr(defaults, name)
    ]
    if ignored:
        raise RestlyConfigurationError(
            f"{cls.__name__}: a react-admin view uses only "
            "pagination.default_page_size and max_page_size. It pages with "
            "the 'range' parameter and reports the total in the "
            "Content-Range header, so these settings do not apply: "
            f"{', '.join(ignored)}."
        )


def _check_list_params(cls: type) -> None:
    """The view reads the parsed ``sort``, ``range`` and ``filter`` from its
    list params, so they must be a ``ReactAdminListParams``."""
    list_params = getattr(cls, "schema_list_params", None)
    if not (
        isinstance(list_params, type) and issubclass(list_params, ReactAdminListParams)
    ):
        raise RestlyConfigurationError(
            f"{cls.__name__}.schema_list_params is {list_params!r}. A react-admin "
            "view reads sort, range and filter from its list params, so they "
            "must be a subclass of fr.views.ReactAdminListParams. To add a "
            "query key, subclass ReactAdminListParams."
        )


def _reject_list_settings(cls: type) -> None:
    """React-admin sends its filters and sort order in its own parameters.
    So a list setting other than the default, or a filter method, would be
    silently ignored."""
    # A view that inherits a setting sets the default again, to show it knows.
    for name in _LIST_SETTINGS:
        default = getattr(BaseRestView, name)
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


# ---------------------------------------------------------------------------
# Shared implementation mixin
# ---------------------------------------------------------------------------


class _ReactAdminViewProtocol(Protocol):
    model: ClassVar[type[Any]]
    schema_response: ClassVar[type[pydantic.BaseModel]]
    schema_update: ClassVar[type[pydantic.BaseModel]]
    pagination: ClassVar[NumberedPagination | NoPagination | None]
    sort_nulls: ClassVar[SortNulls]
    put: ClassVar[Any]

    def get_react_admin_range_unit(self) -> str: ...


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

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        _check_pagination(cls)
        # A class that inherits list params of its own gets generated list
        # params when it sets a schema or a model. So each class declares
        # the list params that it inherits again.
        if "schema_list_params" not in cls.__dict__:
            inherited = getattr(cls, "schema_list_params", ReactAdminListParams)
            setattr(cls, "schema_list_params", inherited)
        _check_list_params(cls)

    def get_react_admin_range_unit(self) -> str:
        """Return the unit string used in the Content-Range header."""
        return "items"

    def _react_admin_list_params(self, list_params: Any) -> Any:
        """``list_params`` as an instance of the view's list params.

        A ``QueryParams`` or a mapping holds query values, so it is checked
        as on the list route. No public form yet.
        """
        return cast(Any, self)._validated_list_params(list_params)

    def _react_admin_range(self, list_params: Any) -> tuple[int, int]:
        """The first and the last row of ``range``, or of the first page
        when the client sends no ``range``.

        :raises RequestValidationError: the range asks for more rows than
            ``pagination.max_page_size``.
        """
        # ``pagination`` is a NumberedPagination on react-admin (see
        # _check_pagination).
        pagination = cast(
            NumberedPagination, cast(_ReactAdminViewProtocol, self).pagination
        )
        row_range = getattr(list_params, "range", None)
        if row_range is None:
            return 0, pagination.default_page_size - 1
        start, end = row_range
        if end - start + 1 > pagination.max_page_size:
            raise RequestValidationError(
                [
                    {
                        "type": "value_error",
                        "loc": ("query", "range"),
                        "msg": (
                            "Value error, a range can ask for at most "
                            f"{pagination.max_page_size} rows"
                        ),
                        "input": [start, end],
                    }
                ]
            )
        return start, end

    def _react_admin_filters(
        self, stmt: sqlalchemy.Select, list_params: Any
    ) -> sqlalchemy.Select:
        view = cast(_ReactAdminViewProtocol, self)
        params = self._react_admin_list_params(list_params)
        filters = getattr(params, "filter", None) or {}
        return _apply_react_admin_filters(
            stmt, view.model, view.schema_response, filters
        )

    def _react_admin_sort_and_range(
        self, stmt: sqlalchemy.Select, list_params: Any
    ) -> sqlalchemy.Select:
        view = cast(_ReactAdminViewProtocol, self)
        params = self._react_admin_list_params(list_params)
        start, end = self._react_admin_range(params)
        return _apply_react_admin_sort_and_range(
            stmt,
            view.model,
            view.schema_response,
            getattr(params, "sort", None),
            start,
            end,
            view.sort_nulls,
        )

    def to_list_response(self, list_result: ListResult[Any]) -> fastapi.Response:
        """Build the react-admin list response: a JSON array with a
        ``Content-Range`` header."""
        # The standard list response holds each row as the response class
        # sends it, also when to_single_response returns another model.
        rows = cast(Any, super()).to_list_response(list_result).data
        serialized = [row.model_dump(mode="json", by_alias=True) for row in rows]
        params = self._react_admin_list_params(list_result.list_params)
        start, _ = self._react_admin_range(params)
        view = cast(_ReactAdminViewProtocol, self)
        unit = view.get_react_admin_range_unit()
        last = start + len(serialized) - 1 if serialized else start
        total = list_result.total_count or 0
        return fastapi.Response(
            content=json.dumps(serialized),
            media_type="application/json",
            headers={
                "Content-Range": f"{unit} {start}-{last}/{total}",
                "Access-Control-Expose-Headers": "Content-Range",
            },
        )

    @classmethod
    def before_include_view(cls) -> None:
        # At registration, as Restly checks these settings. No public form
        # yet.
        _reject_list_settings(cls)
        cast(Any, super()).before_include_view()


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
    async def get_many_endpoint(self, list_params: Any) -> fastapi.Response:
        result = await self.handle_get_many(list_params)
        return self.to_response(result, ResponseShape.LIST)

    async def apply_list_params(
        self, stmt: sqlalchemy.Select, list_params: Any
    ) -> sqlalchemy.Select:
        """Apply the react-admin list params to the scoped base statement:
        :meth:`apply_filters`, then ``sort`` and ``range``."""
        stmt = await self.apply_filters(stmt, list_params)
        return self._react_admin_sort_and_range(stmt, list_params)

    async def apply_filters(
        self, stmt: sqlalchemy.Select, list_params: Any
    ) -> sqlalchemy.Select:
        """Apply the react-admin ``filter`` to ``stmt``, without the sort and
        the range."""
        return self._react_admin_filters(stmt, list_params)

    @put("/{id}")
    async def put(self, id: ViewID, data: ViewUpdate) -> ViewResponse:
        """``PUT /{id}`` endpoint method, for react-admin's ``update``. It
        runs the same steps as ``PATCH``:
        :meth:`~fastapi_restly.views.AsyncRestView.handle_update`, with the
        view's ``schema_update`` body. An ``update`` override changes both
        routes. Replace this method to give ``PUT`` its own rules; see
        :ref:`react-admin-put`."""
        obj = await self.handle_update(id, data)
        return self.to_response(obj)


class ReactAdminView(_ReactAdminMixin, RestView):
    """
    RestView that follows the ra-data-simple-rest HTTP contract.

    Use this instead of RestView when your frontend is react-admin
    with ra-data-simple-rest.
    """

    @get("/")
    def get_many_endpoint(self, list_params: Any) -> fastapi.Response:
        result = self.handle_get_many(list_params)
        return self.to_response(result, ResponseShape.LIST)

    def apply_list_params(
        self, stmt: sqlalchemy.Select, list_params: Any
    ) -> sqlalchemy.Select:
        """Apply the react-admin list params to the scoped base statement:
        :meth:`apply_filters`, then ``sort`` and ``range``."""
        stmt = self.apply_filters(stmt, list_params)
        return self._react_admin_sort_and_range(stmt, list_params)

    def apply_filters(
        self, stmt: sqlalchemy.Select, list_params: Any
    ) -> sqlalchemy.Select:
        """Apply the react-admin ``filter`` to ``stmt``, without the sort and
        the range."""
        return self._react_admin_filters(stmt, list_params)

    @put("/{id}")
    def put(self, id: ViewID, data: ViewUpdate) -> ViewResponse:
        """``PUT /{id}`` endpoint method, for react-admin's ``update``. It
        runs the same steps as ``PATCH``:
        :meth:`~fastapi_restly.views.RestView.handle_update`, with the view's
        ``schema_update`` body. An ``update`` override changes both routes.
        Replace this method to give ``PUT`` its own rules; see
        :ref:`react-admin-put`."""
        obj = self.handle_update(id, data)
        return self.to_response(obj)
