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
from typing import Any, ClassVar, Sequence

import fastapi
import sqlalchemy
from sqlalchemy import func, select
from sqlalchemy.orm import DeclarativeBase, RelationshipProperty

from ..schemas import BaseSchema
from ._async import AsyncRestView
from ._base import _annotate, get, put
from ._sync import RestView

#: Default page size used when the react-admin client does not send a `range`
#: query parameter. Override per-view via :attr:`ReactAdminMixin.default_page_size`.
DEFAULT_REACT_ADMIN_PAGE_SIZE = 25


# ---------------------------------------------------------------------------
# Query parsing helpers (standalone, analogous to _v1.py / _v2.py)
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
        raise fastapi.HTTPException(400, "Invalid sort parameter: must be a JSON array")
    if not isinstance(parsed, list) or len(parsed) != 2:
        raise fastapi.HTTPException(400, "Invalid sort parameter: must be [field, direction]")
    field, direction = parsed
    if not isinstance(field, str) or direction not in ("ASC", "DESC"):
        raise fastapi.HTTPException(
            400, "Invalid sort parameter: direction must be 'ASC' or 'DESC'"
        )
    return field, direction


def parse_react_admin_range(
    range_raw: str | None,
    default_page_size: int = DEFAULT_REACT_ADMIN_PAGE_SIZE,
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
        raise fastapi.HTTPException(400, "Invalid range parameter: must be a JSON array")
    if not isinstance(parsed, list) or len(parsed) != 2:
        raise fastapi.HTTPException(400, "Invalid range parameter: must be [start, end]")
    start, end = parsed
    if not isinstance(start, int) or not isinstance(end, int):
        raise fastapi.HTTPException(400, "Invalid range parameter: values must be integers")
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
        raise fastapi.HTTPException(400, "Invalid filter parameter: must be a JSON object")
    if not isinstance(parsed, dict):
        raise fastapi.HTTPException(400, "Invalid filter parameter: must be a JSON object")
    return parsed


def _resolve_column(
    model: type[DeclarativeBase], schema_cls: Any, field_name: str
) -> Any:
    """
    Resolve a field name to a SQLAlchemy column.

    Checks model attributes directly, then falls back to schema alias resolution.
    Raises HTTPException 400 if the field cannot be resolved.
    """
    col = getattr(model, field_name, None)
    if col is not None:
        if hasattr(col, "property") and isinstance(col.property, RelationshipProperty):
            raise fastapi.HTTPException(
                400, f"Cannot sort or filter by relationship field: {field_name!r}"
            )
        return col

    if schema_cls is not None:
        for name, field in schema_cls.model_fields.items():
            if field.alias == field_name:
                col = getattr(model, name, None)
                if col is not None:
                    return col

    raise fastapi.HTTPException(400, f"Unknown filter field: {field_name!r}")


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
    apply_query_modifiers_v1 / apply_query_modifiers_v2.
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
# Shared mixin
# ---------------------------------------------------------------------------


class ReactAdminMixin:
    """
    Shared transport helpers for react-admin views.

    Override these methods to customize the ra-data-simple-rest contract.

    Set :attr:`default_page_size` on a subclass to change the implicit page
    size used when the client does not send a ``range`` query parameter.

    Note: this is a bare mixin that is always combined with ``RestView`` or
    ``AsyncRestView``. The inherited ``session``/``model``/``schema``/etc.
    attributes come from those bases at runtime. Static type checkers cannot
    see that relationship through a bare mixin, which yields a handful of
    ``reportAttributeAccessIssue`` errors when checking this module in
    isolation. They are accepted as a documented limitation of the mixin
    pattern; users importing ``ReactAdminView`` / ``AsyncReactAdminView``
    directly do not see them because the resolved MRO has the attributes.
    """

    #: Implicit page size when no ``range`` parameter is sent. Override per-view.
    default_page_size: ClassVar[int] = DEFAULT_REACT_ADMIN_PAGE_SIZE

    def get_react_admin_range_unit(self) -> str:
        """Return the unit string used in the Content-Range header."""
        return "items"

    def _parse_react_admin_params(
        self,
    ) -> tuple[tuple[str, str] | None, tuple[int, int], dict]:
        """Parse sort, range, and filter from the current request query string."""
        params = self.request.query_params
        sort = parse_react_admin_sort(params.get("sort"))
        start, end = parse_react_admin_range(
            params.get("range"), default_page_size=self.default_page_size
        )
        filters = parse_react_admin_filter(params.get("filter"))
        return sort, (start, end), filters

    def _serialize_items(self, items: Sequence[Any]) -> list[dict]:
        """Serialize ORM objects to JSON-compatible dicts via the view's response schema."""
        return [
            self.to_response_schema(obj).model_dump(mode="json", by_alias=True)
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
        unit = self.get_react_admin_range_unit()
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
        """Count query: filters only, no sort or pagination."""
        base = sqlalchemy.select(self.model)
        filtered = _apply_react_admin_filters(base, self.model, self.schema, filters)
        return select(func.count()).select_from(filtered.subquery())

    def _build_list_query(
        self, sort: tuple[str, str] | None, start: int, end: int, filters: dict
    ) -> sqlalchemy.Select:
        """List query: filters, sort, and range applied."""
        base = sqlalchemy.select(self.model)
        loader_options = self.get_relationship_loader_options()
        if loader_options:
            base = base.options(*loader_options)
        return apply_react_admin_query(base, self.model, self.schema, sort, start, end, filters)

    @classmethod
    def before_include_view(cls) -> None:
        super().before_include_view()
        # Override the index return annotation set by BaseRestView to Response,
        # since we return a raw Response with Content-Range header.
        if hasattr(cls, "index"):
            _annotate(cls.index, return_annotation=fastapi.Response)
        # Annotate the PUT handler with the same schema/types as PATCH.
        if hasattr(cls, "put"):
            _annotate(
                cls.put,
                return_annotation=cls.schema,
                schema_obj=cls.update_schema,
                id=cls.id_type,
            )


# ---------------------------------------------------------------------------
# Concrete view classes
# ---------------------------------------------------------------------------


class AsyncReactAdminView(ReactAdminMixin, AsyncRestView):
    """
    AsyncRestView that speaks the ra-data-simple-rest wire contract.

    Use this instead of AsyncRestView when your frontend is react-admin
    with ra-data-simple-rest.
    """

    @get("/")
    async def index(self) -> Any:
        sort, (start, end), filters = self._parse_react_admin_params()
        total = int(await self.session.scalar(self._build_count_query(filters)) or 0)
        items = (
            await self.session.scalars(self._build_list_query(sort, start, end, filters))
        ).all()
        return self._build_react_admin_list_response(
            self._serialize_items(items), total, start, end
        )

    @put("/{id}")
    async def put(self, id: Any, schema_obj: BaseSchema) -> Any:
        obj = await self.on_update(id, schema_obj)
        return self.to_response_schema(obj)


class ReactAdminView(ReactAdminMixin, RestView):
    """
    RestView that speaks the ra-data-simple-rest wire contract.

    Use this instead of RestView when your frontend is react-admin
    with ra-data-simple-rest.
    """

    @get("/")
    def index(self) -> Any:
        sort, (start, end), filters = self._parse_react_admin_params()
        total = int(self.session.scalar(self._build_count_query(filters)) or 0)
        items = self.session.scalars(
            self._build_list_query(sort, start, end, filters)
        ).all()
        return self._build_react_admin_list_response(
            self._serialize_items(items), total, start, end
        )

    @put("/{id}")
    def put(self, id: Any, schema_obj: BaseSchema) -> Any:
        obj = self.on_update(id, schema_obj)
        return self.to_response_schema(obj)
