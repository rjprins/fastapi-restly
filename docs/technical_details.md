# Technical Details

## Schema Generation Under the Hood

FastAPI-Restly builds request and response schemas from your declared schema class,
or auto-generates one from the SQLAlchemy model when `schema` is omitted on a
view.

### ReadOnly and WriteOnly

Field-level markers are implemented with `typing.Annotated` metadata:

```python
class UserSchema(IDSchema):
    id: ReadOnly[int]
    email: str
    password: WriteOnly[str]
```

`IDSchema` is primarily a response-schema base: it is `BaseSchema` with a
read-only `id` field. `IDRef[Model]` and `IDSchema[Model]` are the model-aware
reference forms; their validators coerce the `id` value to match the SQLAlchemy
model's actual primary-key type.

- `ReadOnly[...]` fields are excluded from generated create/update input schemas.
- `WriteOnly[...]` fields are accepted on input and excluded from serialized
  responses. The filtering is done explicitly in `to_response_schema()` (a method
  on `AsyncRestView` / `RestView`, defined on the internal abstract base they share),
  which skips any field where `is_field_writeonly()` returns `True`. FastAPI's
  response model serialization does **not** filter them; a custom serialization
  path that bypasses `to_response_schema()` would expose `WriteOnly` fields.

### Generated Input Schemas

For a view schema `MySchema`, Restly derives two input schemas in
`before_include_view()`:

- `creation_schema`: produced by `create_model_without_read_only_fields()`,
  which creates a subclass mixing in `OmitReadOnlyMixin` before `MySchema` in
  the MRO. `OmitReadOnlyMixin.__pydantic_init_subclass__` directly deletes
  `ReadOnly` entries from `cls.model_fields` and calls `model_rebuild(force=True)`.
  The subclass still inherits validators from `MySchema` for the fields that
  remain.
- `update_schema`: produced by `create_model_with_optional_fields()`, which
  mixes in both `PatchMixin` and `OmitReadOnlyMixin`. After `OmitReadOnlyMixin`
  strips the read-only fields, `PatchMixin.__pydantic_init_subclass__` sets
  `field.default = None` and wraps every remaining annotation in `Optional[...]`.
  Original field defaults from `MySchema` are **replaced** by `None`, not
  preserved.

Both derived schemas are stored as class attributes on the view and are frozen
at registration time (see [Query Modifier Lifecycle](#query-modifier-lifecycle)).
They can be overridden by declaring `creation_schema` or `update_schema` directly
on the view class before `include_view()` is called.

(auto-generated-schemas)=
### Auto-Generated Schemas

`create_schema_from_model(model_cls, ...)` walks all `Mapped[...]` annotations
on the model (including inherited ones) and builds a Pydantic schema. Key
behaviours:

- **Base class selection**: The function checks whether the model has fields
  *named* `id`, `created_at`, and `updated_at` to decide which schema base
  classes to mix in (`IDSchema`, `TimestampsSchemaMixin`, `BaseSchema`). It does
  **not** inspect the model's Python inheritance hierarchy; a model with a field
  accidentally named `id` will receive `IDSchema` as a base.
- **ReadOnly annotation**: Only three field names are automatically marked
  `ReadOnly`: `"id"`, `"created_at"`, and `"updated_at"` (controlled by
  `include_readonly_fields=True`). Any other server-side default or
  auto-populated column will **not** be marked `ReadOnly` by auto-generation.
- **Relationship fields**: Included when `include_relationships=True` (the
  default for `create_schema_from_model`). Relationship fields are set to
  `Optional` with `default=None` in the generated schema and nested schemas are
  generated recursively (one level deep, without relationships, to avoid circular
  references).

`auto_generate_schema_for_view(view_cls, model_cls)` is a thin wrapper that
calls `create_schema_from_model(model_cls, schema_name, include_relationships=False)`.
It does not apply any other filtering beyond excluding relationship attributes;
foreign-key columns appear in the output as ordinary scalar fields.

### SQLAlchemy-to-Pydantic Type Mapping

`convert_sqlalchemy_type_to_pydantic` maps the Python type extracted from each
`Mapped[T]` annotation to its Pydantic equivalent. Pass-through types (those
already understood by Pydantic) are returned unchanged:

| SQLAlchemy / Python annotation | Pydantic field type |
|-------------------------------|---------------------|
| `str` | `str` |
| `int` | `int` |
| `float` | `float` |
| `bool` | `bool` |
| `datetime` | `datetime` |
| `date` | `date` |
| `time` | `time` |
| `UUID` | `UUID` |
| `Decimal` | `Decimal` |
| `dict` / `dict[str, Any]` | `dict` / `dict[str, Any]` |
| `list` / `list[T]` | `list` / `list[T]` |
| `enum.Enum` subclass | same enum subclass |
| SQLAlchemy `Text`, `String` | `str` |
| SQLAlchemy `Integer` | `int` |
| SQLAlchemy `Float` | `float` |
| SQLAlchemy `Boolean` | `bool` |
| SQLAlchemy `DateTime` | `datetime` |
| SQLAlchemy `Date` | `date` |
| SQLAlchemy `Time` | `time` |

Any type not in this table raises `TypeError` at schema-generation time. For
custom column types, declare an explicit schema and bypass auto-generation.

## View Classes and Registration

### AsyncRestView and RestView

Both `AsyncRestView` (async) and `RestView` (sync) are public API and
share the same CRUD structure via an internal abstract base class (not exposed
as `fr.*`). The choice between them is determined by which class you subclass —
`AsyncRestView` hardcodes `session: AsyncSessionDep` and `RestView` hardcodes
`session: SessionDep`. The async and sync variants have identical endpoint
signatures; the only difference is that the async variant uses `await` in its
process methods.

Both views expose several class variables that affect endpoint registration and
runtime behaviour:

- `schema` — the Pydantic schema class; auto-generated if absent.
- `creation_schema`, `update_schema` — derived from `schema` if not declared.
- `model` — the SQLAlchemy model class.
- `id_type` — Python type for the `{id}` path parameter (default `int`).
- `exclude_routes` — tuple of method names to suppress (e.g.
  `exclude_routes = ("delete",)`). Routes listed here have their `_api_route_args`
  marker removed during `before_include_view()` so FastAPI never registers them.
- `include_pagination_metadata` — if `True`, the `index` endpoint returns a
  paginated envelope with `items`, `total`, `page`, `page_size`, `total_pages`,
  `limit`, and `offset`.
- `query_modifier_version` — override the global version for this view class.

### include_view()

`include_view()` works in two equivalent forms:

```python
# Decorator form
@fr.include_view(app)
class MyView(fr.AsyncRestView):
    ...

# Direct call form
fr.include_view(app, MyView)
```

Both forms call `before_include_view()` (which generates derived schemas,
annotates endpoint signatures, and registers the `index_param_schema`), then
attach an `APIRouter` to `app`.

### Endpoint / Handler Separation

Every CRUD endpoint delegates to a `handle_*` handler (`handle_list`,
`handle_get`, `handle_create`, `handle_update`, `handle_delete`). Override
the `handle_*` handler to change business logic while keeping the endpoint
wrapper intact, or override the endpoint method itself (e.g. `index`) to replace
the full request/response flow.

### Nested Response Schemas vs Write Payloads

Nested schemas serve two different roles in Restly today:

- **Response serialization**: supported. The CRUD views recursively build
  `selectinload(...)` options for nested relationship fields in the response
  schema, so related objects can be serialized efficiently and with aliases.
- **Create/update payloads**: not supported in the general case. The default
  `make_new_object()` / `update_object()` flow expects payload keys to map
  directly to model attributes, with `*_id: IDRef[Model]` as the usual
  special case for foreign keys. When an `IDRef` / `IDSchema` reference has
  been resolved to an ORM object, the helpers inspect the SQLAlchemy mapper and
  dataclass constructor fields so FK-first (`author_id` init-enabled) and
  relationship-first (`author` init-enabled) declarations both work. For one
  resolved reference, Restly may pass the FK scalar, the relationship object, or
  both when both dataclass fields are required; both values are derived from the
  same row. If the client explicitly supplies both fields, Restly validates that
  they refer to the same row before construction/update. Explicit `null` is
  treated as an intentional "no row" value for that consistency check; omitted
  optional fields are ignored.

If you declare a nested input field like `address: AddressSchema` on a write
schema, the default CRUD implementation will pass that nested Pydantic object
through to the SQLAlchemy model constructor or attribute setter, which usually
does not match the ORM model shape. Use a flattened schema or override
`handle_create()` / `handle_update()` to transform the payload first.

(query-modifier-lifecycle)=
## Query Modifier Lifecycle

Query modifiers have two versions:

- `QueryModifierVersion.V1`: JSONAPI-style — `filter[name]=John`, `sort`,
  `limit`, `offset`
- `QueryModifierVersion.V2`: standard HTTP — `name=John`, `order_by`, `page`,
  `page_size`

The active version is stored in a `ContextVar` (`_query_modifier_version`),
defaulting to V1. `set_query_modifier_version()` calls `.set()` on this
`ContextVar`, not a plain module-level global. In async frameworks, `ContextVar`
values are scoped to the current task/context, so calling it at module level
during application startup (a single-context moment) works as expected, but the
setting does not propagate into concurrent request contexts automatically.

During `before_include_view()`, two class-level attributes are set (once,
idempotently):

1. `cls.query_modifier_version` — the version read from the `ContextVar` at
   registration time, stored as a class attribute. Once set, later calls to
   `set_query_modifier_version()` do not affect already-registered views.
2. `cls.index_param_schema` — the query-parameter Pydantic schema generated for
   this view's `index` endpoint. It is generated inside a
   `use_query_modifier_version(cls.query_modifier_version)` context so the
   correct V1 or V2 field set is used. Like `query_modifier_version`, it is
   frozen at registration time.

To use a specific version, either:

- call `set_query_modifier_version(...)` before registering the view, or
- set `query_modifier_version = QueryModifierVersion.V1|V2` directly on the
  view class.

## Database Globals and Test Isolation

The database session factories (`async_make_session`, `make_session`) are stored
on an `FRGlobals` instance. A `ContextVar` (`_fr_globals_ctx`) determines which
`FRGlobals` object is active in any given context. The module-level `fr_globals`
is a proxy that delegates attribute access to `get_fr_globals()`, which returns
the context-local instance if one has been set, or the default instance
otherwise.

The `use_fr_globals(globals_obj)` context manager swaps in an alternative
`FRGlobals` during the block and restores the previous one on exit. This is how
`activate_savepoint_only_mode()` achieves test isolation: it injects a
savepoint-backed session factory without touching global state visible to other
concurrent contexts.

## Session Factory Defaults

When `fr.configure()` creates session factories from URLs or engines, Restly
sets a few SQLAlchemy session options intentionally:

| Factory | Autoflush | Expire on commit |
|---|---|---|
| Async `async_sessionmaker` | `False` | `False` |
| Sync `sessionmaker` | SQLAlchemy default (`True`) | `False` |

`expire_on_commit=False` is used for both sync and async sessions so ORM
objects remain readable after a route commits. FastAPI response serialization
and Restly's response-schema conversion read attributes from ORM objects after
the write path has flushed and refreshed them. If commit expired those
attributes, serialization could trigger implicit database reads. In async code
that can fail outside an awaited SQLAlchemy call; in sync code it makes response
rendering unexpectedly database-dependent.

The autoflush setting is intentionally different. Async sessions disable
autoflush because autoflush can turn a read operation into an implicit write and
database I/O must happen at explicit awaited SQLAlchemy boundaries. Restly's
async CRUD helpers flush explicitly when writes should hit the database. Sync
sessions keep SQLAlchemy's default autoflush behavior, preserving the usual
unit-of-work ergonomics where ORM queries see pending in-session changes.

Projects that provide custom sessionmakers or session generators should preserve
these assumptions unless they deliberately want different behavior.

## See Also

- [How-To: Filter, Sort, and Paginate Lists](howto_query_modifiers.md) — full
  V1 / V2 query-parameter reference.
