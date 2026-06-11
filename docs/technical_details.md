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
  responses. `to_response_schema()` performs the filtering. FastAPI's response
  model serialization does **not** filter them; bypassing `to_response_schema()`
  can expose `WriteOnly` fields.

### Generated Input Schemas

For a view schema `UserRead`, Restly derives two input schemas in
`before_include_view()`:

- `schema_create`: produced by `create_model_without_read_only_fields()`,
  which creates a subclass mixing in `OmitReadOnlyMixin` before `UserRead` in
  the MRO. `OmitReadOnlyMixin.__pydantic_init_subclass__` directly deletes
  `ReadOnly` entries from `cls.model_fields` and calls `model_rebuild(force=True)`.
  The subclass still inherits validators from `UserRead` for the fields that
  remain.
- `schema_update`: produced by `create_model_with_optional_fields()`, which
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
They can be overridden by declaring `schema_create` or `schema_update` directly
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
`AsyncRestView` declares `session: AsyncSessionDep` and `RestView` declares
`session: SessionDep`. A subclass can override that `session` annotation with
its own `Annotated[..., Depends(...)]` dependency for per-view session wiring.
The async and sync variants have identical endpoint signatures; the only
difference is that the async variant uses `await` in its process methods.

`AsyncSessionDep` and `SessionDep` use Restly's built-in session generators.
Those generators yield a SQLAlchemy session and manage lifecycle: rollback and
close on exit. They do **not** commit on response. `handle_<verb>` owns the
commit and runs `before_commit` → commit → `after_commit` around domain logic.
Custom write routes can use the same bracket with
`async with self.write_action(action, ...)`. If a request ends with uncommitted
changes, Restly warns with `RestlyUncommittedChangesWarning` by default.
Custom session generators control construction and cleanup, not commit
ownership.

Both views expose several class variables that affect endpoint registration and
runtime behaviour:

- `schema` — the Pydantic schema class; auto-generated if absent.
- `schema_create`, `schema_update` — derived from `schema` if not declared.
- `model` — the SQLAlchemy model class.
- `id_type` — Python type for the scalar `{id}` path parameter (default `int`).
  Composite primary keys are outside the generated CRUD route contract; use
  `View` directly when a resource needs a multi-part identity.
- `exclude_routes` — iterable of route names to suppress (e.g.
  `exclude_routes = [fr.ViewRoute.DELETE]`). Route-name strings such as
  `"delete"` are also accepted. Routes listed here have their
  `_api_route_args` marker removed during `before_include_view()` so FastAPI
  never registers them.
- `include_pagination_metadata` — if `True`, the `get_many_endpoint` route
  returns a paginated envelope with `items`, `total`, `page`, `page_size`, and
  `total_pages`.

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
annotates endpoint signatures, and registers the `listing_param_schema`), then
attach an `APIRouter` to the parent app/router.

### The Three Tiers of a CRUD Verb

Every CRUD verb is split into a route shell (`<verb>_endpoint`), a request
handler (`handle_<verb>`), and a business verb (`<verb>`); the model and the
override decision table live in
[How Overrides Work: The Three Tiers](the_handle_design.md).

The implementation detail worth knowing here: the route shell calls
`to_response(obj, shape)`, the single response method, which delegates to
`to_response_schema(obj)` for the per-object serialization (`WriteOnly`
filtering, relationship-id normalization).

### Nested Response Schemas vs Write Payloads

Nested schemas serve two different roles in Restly today:

- **Response serialization**: supported. The CRUD views recursively build
  `selectinload(...)` options for nested relationship fields in the response
  schema, so related objects can be serialized efficiently and with aliases.
- **Create/update payloads**: not supported in the general case. The default
  `make_new_object()` / `update_object()` flow expects payload keys to map
  directly to model attributes, with `*_id: IDRef[Model]` as the FK case. After
  resolving an `IDRef` / `IDSchema` to an ORM object, Restly chooses the FK
  scalar, relationship object, or both based on the model constructor. If the
  client supplies both fields, Restly checks they refer to the same row.

If you declare a nested input field like `address: AddressRead` on a write
schema, the default CRUD implementation will pass that nested Pydantic object
through to the SQLAlchemy model constructor or attribute setter, which usually
does not match the ORM model shape. Use a flattened schema or override the
`create()` / `update()` business verbs to transform the payload first.

(list-parameters-lifecycle)=
## List Parameters Lifecycle

List endpoints accept URL query parameters of the form
``name=John``, ``age__gte=18``, ``sort=-created_at``, and
``page=2&page_size=50``. The full operator surface (`__ne`, `__isnull`,
`__contains`, `__icontains`, …) is documented in
[the how-to guide](howto_query_modifiers.md).

During `before_include_view()`, the framework freezes a single class-level
attribute:

- `cls.listing_param_schema` — the query-parameter Pydantic schema generated
  by `create_list_params_schema(cls.schema, cls.model, default_page_size=...,
  max_page_size=...)`. The schema covers pagination, sorting, and one filter
  parameter per response-schema field that maps to a filterable column on the
  model, with optional operator suffixes. It is generated once per registration
  and never re-derived.

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

`fr.configure(...)` rejects no-op calls — pass at least one setup option. The
authoritative list of accepted options is the
[API Reference's Database section](api_reference.md); this page does not
duplicate the contract.

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

Projects with custom sessionmakers or generators should preserve these defaults
unless they need different behavior.

## See Also

- [Filter, Sort, and Paginate Lists](howto_query_modifiers.md) — full
  filter, sort, and pagination reference.
