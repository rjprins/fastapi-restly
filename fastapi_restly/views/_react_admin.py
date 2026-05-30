"""
React Admin compatible views for fastapi-restly.

Implements the ra-data-simple-rest wire contract for list:
- response body: plain JSON array
- sort:   sort=["field","ASC|DESC"]
- range:  range=[start, end]  (both inclusive, e.g. [0,24] = 25 items)
- filter: filter={"field":"value"} or filter={"id":[1,2,3]} for getMany
- Content-Range: items 0-24/315
"""

import json
from typing import Any, ClassVar, Protocol, Sequence, cast

import fastapi
import pydantic
import sqlalchemy
from sqlalchemy.orm import DeclarativeBase, RelationshipProperty

from ..exceptions import BadQueryParam
from ._async import AsyncRestView
from ._base import _annotate, get, put
from ._sync import RestView

#: Default page size used when the react-admin client does not send a `range`
#: query parameter. Override per-view via ``default_page_size``.
DEFAULT_REACT_ADMIN_PAGE_SIZE = 25


# ---------------------------------------------------------------------------
# Query parsing helpers (standalone, analogous to query/_impl.py)
# ---------------------------------------------------------------------------


def parse_react_admin_sort(sort_raw: str | None) -> tuple[str, str] | None:
    """
    Parse a react-admin sort query parameter.

    Expected: '["field","ASC"]' or '["field","DESC"]'
    Returns (field, direction) or None if absent.
    Raises HTTPException 400 on malformed input.
    """
    if not sort_raw:
        return None
    try:
        parsed = json.loads(sort_raw)
    except json.JSONDecodeError:
        raise BadQueryParam("Invalid sort parameter: must be a JSON array")
    if not isinstance(parsed, list) or len(parsed) != 2:
        raise fastapi.HTTPException(
            400, "Invalid sort parameter: must be [field, direction]"
        )
    field, direction = parsed
    if not isinstance(field, str) or direction not in ("ASC", "DESC"):
        raise fastapi.HTTPException(
            400, "Invalid sort parameter: direction must be 'ASC' or 'DESC'"
        )
    return field, direction


def parse_react_admin_range(
    range_raw: str | None, default_page_size: int = DEFAULT_REACT_ADMIN_PAGE_SIZE
) -> tuple[int, int]:
    """
    Parse a react-admin range query parameter.

    Expected: '[0,24]' (both inclusive).
    Returns (start, end). Defaults to (0, default_page_size - 1) if absent.
    Raises HTTPException 400 on malformed input.
    """
    if not range_raw:
        return 0, default_page_size - 1
    try:
        parsed = json.loads(range_raw)
    except json.JSONDecodeError:
        raise fastapi.HTTPException(
            400, "Invalid range parameter: must be a JSON array"
        )
    if not isinstance(parsed, list) or len(parsed) != 2:
        raise fastapi.HTTPException(
            400, "Invalid range parameter: must be [start, end]"
        )
    start, end = parsed
    if not isinstance(start, int) or not isinstance(end, int):
        raise fastapi.HTTPException(
            400, "Invalid range parameter: values must be integers"
        )
    return start, end


def parse_react_admin_filter(filter_raw: str | None) -> dict:
    """
    Parse a react-admin filter query parameter.

    Expected: '{"field":"value"}' or '{"id":[1,2,3]}' for getMany.
    Returns a dict. Defaults to {} if absent.
    Raises HTTPException 400 on malformed input.
    """
    if not filter_raw:
        return {}
    try:
        parsed = json.loads(filter_raw)
    except json.JSONDecodeError:
        raise fastapi.HTTPException(
            400, "Invalid filter parameter: must be a JSON object"
        )
    if not isinstance(parsed, dict):
        raise fastapi.HTTPException(
            400, "Invalid filter parameter: must be a JSON object"
        )
    return parsed


def _resolve_column(
    model: type[DeclarativeBase], schema_cls: Any, field_name: str
) -> Any:
    """
    Resolve a PUBLIC schema field name (or alias) to its SQLAlchemy column.

    Strict: only fields exposed on the response schema may be filtered or
    sorted. A column that exists on the model but is omitted from the schema
    (or marked write-only) is rejected, so the list endpoint cannot be used as
    an oracle to filter/sort on -- and thereby probe -- hidden data. Mirrors the
    standard REST dialect's schema-driven resolution.

    Raises HTTPException 400 if the field is not a public, filterable schema field.
    """
    from ..schemas._base import is_writeonly_field

    resolved_name: str | None = None
    if schema_cls is not None:
        for name, field in schema_cls.model_fields.items():
            if is_writeonly_field(schema_cls, name):
                continue
            if name == field_name or field.alias == field_name:
                resolved_name = name
                break

    if resolved_name is None:
        raise BadQueryParam(f"Unknown filter field: {field_name!r}")

    col = getattr(model, resolved_name, None)
    if col is None:
        raise BadQueryParam(f"Unknown filter field: {field_name!r}")
    if hasattr(col, "property") and isinstance(col.property, RelationshipProperty):
        raise fastapi.HTTPException(
            400, f"Cannot sort or filter by relationship field: {field_name!r}"
        )
    return col


def _coerce_value(col: Any, value: Any) -> Any:
    """Coerce a filter value to the column's Python type if needed.

    Handles cases such as UUID strings that must be converted to uuid.UUID
    objects before being passed to SQLAlchemy's type processor.
    """
    try:
        py_type = col.type.python_type
    except NotImplementedError:
        return value
    if not isinstance(value, py_type):
        try:
            return py_type(value)
        except (ValueError, TypeError):
            raise fastapi.HTTPException(
                400, f"Invalid filter value for {col.key!r}: {value!r}"
            )
    return value


def _apply_react_admin_filters(
    query: sqlalchemy.Select,
    model: type[DeclarativeBase],
    schema_cls: Any,
    filters: dict,
) -> sqlalchemy.Select:
    """Apply a react-admin filter dict to a select query."""
    for key, value in filters.items():
        col = _resolve_column(model, schema_cls, key)
        if isinstance(value, list):
            coerced = [_coerce_value(col, v) for v in value]
            query = query.where(col.in_(coerced))
        else:
            query = query.where(col == _coerce_value(col, value))
    return query


def apply_react_admin_query(
    query: sqlalchemy.Select,
    model: type[DeclarativeBase],
    schema_cls: Any,
    sort: tuple[str, str] | None,
    start: int,
    end: int,
    filters: dict,
) -> sqlalchemy.Select:
    """
    Apply filter, sort, and range (limit/offset) to a select query.

    This is the main query transformation entry point, analogous to
    :func:`fastapi_restly.query.apply_list_params` for the standard REST dialect.
    """
    query = _apply_react_admin_filters(query, model, schema_cls, filters)

    if sort:
        field, direction = sort
        col = _resolve_column(model, schema_cls, field)
        order_fn = sqlalchemy.desc if direction == "DESC" else sqlalchemy.asc
        query = query.order_by(order_fn(col))
    else:
        id_col = getattr(model, "id", None)
        if id_col is not None:
            query = query.order_by(id_col)

    query = query.limit(end - start + 1).offset(start)
    return query


# ---------------------------------------------------------------------------
# Shared implementation mixin
# ---------------------------------------------------------------------------


class _ReactAdminViewProtocol(Protocol):
    request: fastapi.Request
    model: ClassVar[type[DeclarativeBase]]
    schema: ClassVar[type[pydantic.BaseModel]]
    schema_update: ClassVar[type[pydantic.BaseModel]]
    id_type: ClassVar[type[Any]]
    default_page_size: ClassVar[int | None]
    get_many_endpoint: ClassVar[Any]
    put: ClassVar[Any]

    def get_react_admin_range_unit(self) -> str: ...
    def get_relationship_loader_options(self) -> list[Any]: ...
    def to_response_schema(self, obj: Any) -> pydantic.BaseModel: ...
    def build_query(self) -> sqlalchemy.Select: ...

    @classmethod
    def before_include_view(cls) -> None: ...


class _ReactAdminMixin:
    """
    Shared transport helpers for react-admin views.

    This is an implementation detail shared by ReactAdminView and
    AsyncReactAdminView. User-facing customization should happen by
    subclassing one of those concrete view classes.

    Set :attr:`default_page_size` on a subclass to change the implicit page
    size used when the client does not send a ``range`` query parameter.

    Type annotations on the mixin methods use ``_ReactAdminViewProtocol`` to
    make the expected ``RestView`` surface explicit to static checkers.
    """

    #: Implicit page size when no ``range`` parameter is sent. Override per-view.
    default_page_size: ClassVar[int | None] = DEFAULT_REACT_ADMIN_PAGE_SIZE

    def get_react_admin_range_unit(self) -> str:
        """Return the unit string used in the Content-Range header."""
        return "items"

    def _parse_react_admin_params(
        self,
    ) -> tuple[tuple[str, str] | None, tuple[int, int], dict]:
        """Parse sort, range, and filter from the current request query string."""
        view = cast(_ReactAdminViewProtocol, self)
        params = view.request.query_params
        default_page_size = view.default_page_size or DEFAULT_REACT_ADMIN_PAGE_SIZE
        sort = parse_react_admin_sort(params.get("sort"))
        start, end = parse_react_admin_range(
            params.get("range"), default_page_size=default_page_size
        )
        filters = parse_react_admin_filter(params.get("filter"))
        return sort, (start, end), filters

    def _serialize_items(self, items: Sequence[Any]) -> list[dict]:
        """Serialize ORM objects to JSON-compatible dicts via the view's response schema."""
        view = cast(_ReactAdminViewProtocol, self)
        return [
            view.to_response_schema(obj).model_dump(mode="json", by_alias=True)
            for obj in items
        ]

    def _build_react_admin_list_response(
        self,
        serialized_items: list[dict],
        total: int,
        start: int,
        end: int,
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

    def _build_count_query(self, filters: dict) -> sqlalchemy.Select:
        """Scoped + filtered query (model rows) for the list total.

        Starts from ``build_query()`` so the react-admin list respects the same
        read scope (tenant, soft-delete, row-level visibility) as every other
        read on the view. The total is produced by the view's ``count`` method
        (so a ``count`` override -- e.g. estimated counts -- applies here too).
        """
        view = cast(_ReactAdminViewProtocol, self)
        base = view.build_query()
        return _apply_react_admin_filters(base, view.model, view.schema, filters)

    def _build_listing_query(
        self,
        sort: tuple[str, str] | None,
        start: int,
        end: int,
        filters: dict,
    ) -> sqlalchemy.Select:
        """List query: filters, sort, and range applied, over the scoped
        ``build_query()`` base (same visibility as every other read)."""
        view = cast(_ReactAdminViewProtocol, self)
        base = view.build_query()
        loader_options = view.get_relationship_loader_options()
        if loader_options:
            base = base.options(*loader_options)
        return apply_react_admin_query(
            base, view.model, view.schema, sort, start, end, filters
        )

    @classmethod
    def before_include_view(cls) -> None:
        cast(Any, super()).before_include_view()
        view_cls = cast(type[_ReactAdminViewProtocol], cls)
        # Override the list return annotation set by BaseRestView to Response,
        # since we return a raw Response with Content-Range header.
        if hasattr(view_cls, "get_many_endpoint"):
            _annotate(view_cls.get_many_endpoint, return_annotation=fastapi.Response)
        # Annotate the PUT handler with the same schema/types as PATCH.
        if hasattr(view_cls, "put"):
            _annotate(
                view_cls.put,
                return_annotation=view_cls.schema,
                schema_obj=view_cls.schema_update,
                id=view_cls.id_type,
            )


# ---------------------------------------------------------------------------
# Concrete view classes
# ---------------------------------------------------------------------------


class AsyncReactAdminView(_ReactAdminMixin, AsyncRestView):
    """
    AsyncRestView that speaks the ra-data-simple-rest wire contract.

    Use this instead of AsyncRestView when your frontend is react-admin
    with ra-data-simple-rest.
    """

    @get("/")
    async def get_many_endpoint(self) -> Any:
        await self.authorize("get_many")
        sort, (start, end), filters = self._parse_react_admin_params()
        total = await self.count(self._build_count_query(filters))
        items = (
            await self.session.scalars(
                self._build_listing_query(sort, start, end, filters)
            )
        ).all()
        return self._build_react_admin_list_response(
            self._serialize_items(items), total, start, end
        )

    @put("/{id}")
    async def put(self, id: Any, schema_obj: Any) -> Any:
        obj = await self.handle_update(id, schema_obj)
        return self.to_response(obj, "update")


class ReactAdminView(_ReactAdminMixin, RestView):
    """
    RestView that speaks the ra-data-simple-rest wire contract.

    Use this instead of RestView when your frontend is react-admin
    with ra-data-simple-rest.
    """

    @get("/")
    def get_many_endpoint(self) -> Any:
        self.authorize("get_many")
        sort, (start, end), filters = self._parse_react_admin_params()
        total = self.count(self._build_count_query(filters))
        items = self.session.scalars(
            self._build_listing_query(sort, start, end, filters)
        ).all()
        return self._build_react_admin_list_response(
            self._serialize_items(items), total, start, end
        )

    @put("/{id}")
    def put(self, id: Any, schema_obj: Any) -> Any:
        obj = self.handle_update(id, schema_obj)
        return self.to_response(obj, "update")
