# Technical Details

## Schema Generation Under the Hood

FastAPI-Restly builds request and response schemas from your declared schema class,
or auto-generates one from the SQLAlchemy model when `schema` is omitted on a
view.

### ReadOnly and WriteOnly

Field-level markers are implemented with `typing.Annotated` metadata:

```python
class UserRead(IDSchema):
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
  which skips any field where the internal write-only predicate returns `True`. FastAPI's
  response model serialization does **not** filter them; a custom serialization
  path that bypasses `to_response_schema()` would expose `WriteOnly` fields.

### Generated Input Schemas

For a view schema `UserRead`, Restly derives two input schemas in
`before_include_view()`:

- `creation_schema`: produced by `create_model_without_read_only_fields()`,
  which creates a subclass mixing in `OmitReadOnlyMixin` before `UserRead` in
  the MRO. `OmitReadOnlyMixin.__pydantic_init_subclass__` directly deletes
  `ReadOnly` entries from `cls.model_fields` and calls `model_rebuild(force=True)`.
  The subclass still inherits validators from `UserRead` for the fields that
  remain.
- `update_schema`: produced by `create_model_with_optional_fields()`, which
  mixes in both `PatchMixin` and `OmitReadOnlyMixin`. After `OmitReadOnlyMixin`
  strips the read-only fields, `PatchMixin.__pydantic_init_subclass__` sets
  `field.default = None` and wraps every remaining annotation in `Optional[...]`.
  Original field defaults from `UserRead` are **replaced** by `None`, not
  preserved.

The generated class names use resource-first role suffixes. `UserRead` derives
`UserCreate` and `UserUpdate`. The `Read` suffix is the only suffix Restly
strips when deriving request-schema names; other schema names are kept literally,
so `UserSchema` derives `UserSchemaCreate` and `UserSchemaUpdate`. When `schema`
is omitted entirely, a model named `User` auto-generates `UserRead` as the
response schema.

Both derived schemas are stored as class attributes on the view and are frozen
at registration time (see [List Parameters Lifecycle](#list-parameters-lifecycle)).
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

When a `RestView` / `AsyncRestView` omits `schema`, the internal view setup calls
`create_schema_from_model(model_cls, schema_name=schema_name, include_relationships=False)`.
It does not apply any other filtering beyond excluding relationship attributes;
foreign-key columns appear in the generated schema as ordinary scalar fields.

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

`AsyncSessionDep` and `SessionDep` use Restly's built-in session generators.
Those generators yield a SQLAlchemy session to the endpoint and, by default,
commit when the endpoint successfully produces a response. On FastAPI versions
where `Depends(..., scope="function")` exists, Restly requests that scope so
the commit runs before FastAPI sends the response. On older FastAPI versions,
cleanup timing follows FastAPI's default `yield` dependency behavior and may
run after the response has already been sent. Set
`commit_session_on_response=False` in `fr.configure(...)` to disable the
built-in commit and manage transactions explicitly. Custom session generators
configured with `session_generator` or `sync_session_generator` are passed
through unchanged; their generator body owns transaction handling.

Both views expose several class variables that affect endpoint registration and
runtime behaviour:

- `schema` — the Pydantic schema class; auto-generated if absent.
- `creation_schema`, `update_schema` — derived from `schema` if not declared.
- `model` — the SQLAlchemy model class.
- `id_type` — Python type for the `{id}` path parameter (default `int`).
- `exclude_routes` — iterable of method names to suppress (e.g.
  `exclude_routes = ["delete"]`). Routes listed here have their `_api_route_args`
  marker removed during `before_include_view()` so FastAPI never registers them.
- `include_pagination_metadata` — if `True`, the `index` endpoint returns a
  paginated envelope with `items`, `total`, `page`, `page_size`, and `total_pages`.

### include_view()

`include_view()` is the registration boundary between declarative view modules
and application composition. For larger apps, define view classes without
side effects in feature modules, then include them from the module that builds
your `FastAPI` app or `APIRouter`:

```python
fr.include_view(app, MyView)
```

This keeps imports predictable: importing `myapp.users.views` defines
`UserView`, while `myapp.main` or `myapp.users.router` decides which app/router
receives it. For small apps and examples, `include_view()` also works as a
decorator:

```python
@fr.include_view(app)
class MyView(fr.AsyncRestView):
    ...
```

Both forms call `before_include_view()` (which generates derived schemas,
annotates endpoint signatures, and registers the `index_param_schema`), then
attach an `APIRouter` to the parent app/router.

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

If you declare a nested input field like `address: AddressRead` on a write
schema, the default CRUD implementation will pass that nested Pydantic object
through to the SQLAlchemy model constructor or attribute setter, which usually
does not match the ORM model shape. Use a flattened schema or override
`handle_create()` / `handle_update()` to transform the payload first.

(list-parameters-lifecycle)=
## List Parameters Lifecycle

List endpoints accept URL query parameters of the form
``name=John``, ``age__gte=18``, ``sort=-created_at``, and
``page=2&page_size=50``. The full operator surface (`__ne`, `__isnull`,
`__contains`, `__icontains`, …) is documented in
[the how-to guide](howto_query_modifiers.md).

During `before_include_view()`, the framework freezes a single class-level
attribute:

- `cls.index_param_schema` — the query-parameter Pydantic schema generated
  by `create_list_params_schema(cls.schema, default_page_size=...,
  max_page_size=...)`. The schema covers pagination, sorting, and one
  filter parameter per response-schema field with optional operator
  suffixes. It is generated once per registration and never re-derived.

Custom dialects (e.g. react-admin's
[`AsyncReactAdminView` / `ReactAdminView`](howto_react_admin.md)) live as
parallel view classes that bypass `apply_list_params` entirely and
implement their own request/response contract.

## Restly Runtime Configuration

Restly exposes one public process-wide runtime configuration. Most applications
configure it once during startup:

```python
fr.configure(async_database_url="sqlite+aiosqlite:///app.db")
```

`fr.configure(...)` rejects no-op calls. Pass at least one setup option: an app
for default exception-handler registration, a database URL, an engine, a session
maker, a custom session generator, or an explicit
`commit_session_on_response` policy.

Internally, Restly keeps a private context object so its own tests and fixtures
can isolate runtime state. That context is not a public multi-engine feature.
If an application needs multiple databases, wire a custom FastAPI dependency or
session generator for that view. Restly does not currently bind different views
to different named contexts.

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
  filter, sort, and pagination reference.
