# Technical Details

This page documents the implementation behaviour beneath FastAPI-Restly's
public API: how schemas are generated and derived, how view classes register
their routes, how list parameters are assembled, and which session defaults
the framework sets.

## Schema Generation Under the Hood

FastAPI-Restly builds request and response schemas from your declared schema class,
or auto-generates one from the SQLAlchemy model when {attr}`schema <fastapi_restly.views.BaseRestView.schema>` is omitted on a
view.

### ReadOnly and WriteOnly

`ReadOnly` and `WriteOnly` are field-level markers implemented with
`typing.Annotated` metadata. A schema declares them inline:

```python
class UserRead(IDSchema):
    id: ReadOnly[int]
    email: str
    password: WriteOnly[str]
```

{class}`IDSchema <fastapi_restly.schemas.IDSchema>` is primarily a response-schema base: it is {class}`BaseSchema <fastapi_restly.schemas.BaseSchema>` with a
read-only `id` field. {class}`IDRef[Model] <fastapi_restly.schemas.IDRef>` and `IDSchema[Model]` are the model-aware
[reference forms](howto_relationship_idschema.md#choosing-a-reference-style);
their validators coerce the `id` value to match the SQLAlchemy
model's actual primary-key type.

The markers take effect as follows:

- `ReadOnly[...]` fields are excluded from generated create/update input schemas.
- `WriteOnly[...]` fields are accepted on input and excluded from serialized
  responses. The marker carries Pydantic's field-level `exclude`, so the
  filtering happens in every serialization of the schema, including FastAPI's
  response model and nested schemas. A response that never passes through the
  schema (a raw dict or ORM object returned without a `response_model`) is the
  only way a `WriteOnly` value can leak.

The user-facing behaviour of both markers is covered in
[ReadOnly and WriteOnly](howto_custom_schema.md#readonly-and-writeonly).

### Generated Input Schemas

Restly derives two input schemas from the declared view schema in
{meth}`before_include_view() <fastapi_restly.views.BaseRestView.before_include_view>`. For a view schema `UserRead`:

- {attr}`schema_create <fastapi_restly.views.BaseRestView.schema_create>`: produced by `create_model_without_read_only_fields()`,
  which creates a subclass mixing in `OmitReadOnlyMixin` before `UserRead` in
  the MRO. `OmitReadOnlyMixin.__pydantic_init_subclass__` directly deletes
  `ReadOnly` entries from `cls.model_fields` and calls `model_rebuild(force=True)`.
  The subclass still inherits validators from `UserRead` for the fields that
  remain.
- {attr}`schema_update <fastapi_restly.views.BaseRestView.schema_update>`: produced by `create_model_with_optional_fields()`, which
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
on the view class before {func}`include_view() <fastapi_restly.views.include_view>` is called.

(auto-generated-schemas)=
### Auto-Generated Schemas

{func}`create_schema_from_model(model_cls, ...) <fastapi_restly.schemas.create_schema_from_model>` walks all `Mapped[...]` annotations
on the model (including inherited ones) and builds a Pydantic schema. Three of
its behaviours are worth noting:

- **Base class selection**: The function checks whether the model has fields
  *named* `id`, `created_at`, and `updated_at` to decide which schema base
  classes to mix in ({class}`IDSchema <fastapi_restly.schemas.IDSchema>`, {class}`TimestampsSchemaMixin <fastapi_restly.schemas.TimestampsSchemaMixin>`, {class}`BaseSchema <fastapi_restly.schemas.BaseSchema>`). It does
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

When a {class}`RestView <fastapi_restly.views.RestView>` / {class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` omits {attr}`schema <fastapi_restly.views.BaseRestView.schema>`, the internal view setup calls
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

The generated schemas feed the view layer, which turns a model and a schema
into registered FastAPI routes.

### AsyncRestView and RestView

Both {class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` (async) and {class}`RestView <fastapi_restly.views.RestView>` (sync) are public API and
share the same CRUD structure via an internal abstract base class (not exposed
as `fr.*`). The choice between them is determined by which class you subclass:
`AsyncRestView` declares `session: AsyncSessionDep` and `RestView` declares
`session: SessionDep`. A subclass can override that `session` annotation with
its own `Annotated[..., Depends(...)]` dependency for
[per-view session wiring](howto_existing_project.md#use-a-custom-session-dependency-on-one-view).
The async and sync variants have identical endpoint signatures; the only
difference is that the async variant uses `await` in its process methods.

`AsyncSessionDep` and `SessionDep` use Restly's built-in session generators.
Those generators yield a SQLAlchemy session and manage lifecycle: rollback and
close on exit. They do **not** commit on response. `handle_<verb>` owns the
commit and runs {meth}`before_commit <fastapi_restly.views.RestView.before_commit>`, then the commit itself, then {meth}`after_commit <fastapi_restly.views.RestView.after_commit>` around domain logic.
Custom write routes can use the same bracket with
`async with self.write_action(action, ...)`. If a request ends with uncommitted
changes, Restly warns with {class}`RestlyUncommittedChangesWarning <fastapi_restly.exc.RestlyUncommittedChangesWarning>` by default.
[Custom session generators](howto_existing_project.md#provide-your-own-session-generator)
control construction and cleanup, not commit ownership.

Both views expose several class variables that affect endpoint registration and
runtime behaviour:

- {attr}`schema <fastapi_restly.views.BaseRestView.schema>` holds the Pydantic schema class; it is auto-generated if absent.
- {attr}`schema_create <fastapi_restly.views.BaseRestView.schema_create>` and {attr}`schema_update <fastapi_restly.views.BaseRestView.schema_update>` are derived from `schema` if not declared.
- {attr}`model <fastapi_restly.views.BaseRestView.model>` names the SQLAlchemy model class.
- {attr}`id_type <fastapi_restly.views.BaseRestView.id_type>` sets the Python type of the scalar `{id}` path parameter (default `int`).
  Composite primary keys are outside the generated CRUD route contract; use
  {class}`View <fastapi_restly.views.View>` directly when a resource needs a multi-part identity.
- {attr}`exclude_routes <fastapi_restly.views.BaseRestView.exclude_routes>` is an iterable of route names to suppress (e.g.
  `exclude_routes = [fr.ViewRoute.DELETE]`). Route-name strings such as
  `"delete"` are also accepted. Routes listed here have their
  `_api_route_args` marker removed during {meth}`before_include_view() <fastapi_restly.views.BaseRestView.before_include_view>` so FastAPI
  never registers them.
- {attr}`include_pagination_metadata <fastapi_restly.views.BaseRestView.include_pagination_metadata>`, if `True`, makes the {meth}`get_many_endpoint <fastapi_restly.views.RestView.get_many_endpoint>` route
  return a [paginated envelope](howto_response_schema.md#list-metadata-and-total-count)
  with `items`, `total`, `page`, `page_size`, and `total_pages`.

### include_view()

{func}`include_view() <fastapi_restly.views.include_view>` is the registration boundary between declarative view modules
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

Both forms call {meth}`before_include_view() <fastapi_restly.views.BaseRestView.before_include_view>` (which generates derived schemas,
annotates endpoint signatures, and registers the {attr}`listing_param_schema <fastapi_restly.views.BaseRestView.listing_param_schema>`), then
attach an `APIRouter` to the parent app/router.

### The Three Tiers of a CRUD Verb

Every CRUD verb is split into an endpoint method (`<verb>_endpoint`), a
handler (`handle_<verb>`), and a business method (`<verb>`); the model and the
override decision table live in
[Customize RestView](customize.md).

The implementation detail worth knowing here is that the endpoint method calls
{meth}`to_response(obj, shape) <fastapi_restly.views.BaseRestView.to_response>`, the single response method, which delegates to
{meth}`to_response_schema(obj) <fastapi_restly.views.BaseRestView.to_response_schema>` for the per-object serialization
(relationship-id normalization and response-schema validation).

### Nested Response Schemas vs Write Payloads

Nested schemas serve two different roles in Restly today:

- **Response serialization** is supported. The CRUD views recursively build
  `selectinload(...)` options for nested relationship fields in the response
  schema, so related objects can be serialized efficiently and with aliases.
  Reads apply those options in `get_one` / `get_many`; writes apply the same
  ones in `save_object`, because the refresh that follows a flush leaves
  relationships unloaded. Without that, serializing a create or update response
  would reach them one lazy query at a time, which on an async session raises
  `MissingGreenlet` rather than merely costing queries. The reload is skipped
  when everything the schema names is already loaded, and it runs without
  `populate_existing`, so a relationship the caller has already populated keeps
  its value.

  Loader options follow relationships the schema *names*. Code that reaches
  past that set -- an `after_commit` hook, a custom business method, a
  `@property` walking a relationship nothing else loads -- runs in plain async
  context, where a bare attribute access raises `MissingGreenlet`. Restly's
  declarative base mixes in SQLAlchemy's `AsyncAttrs` for exactly that case, so
  those reads can be spelled `await obj.awaitable_attrs.items`.
- **Create/update payloads** are not supported in the general case. The default
  `make_new_object()` / `update_object()` flow expects payload keys to map
  directly to model attributes, with `*_id: fr.MustExist[int, Model]`
  (see [MustExist](howto_custom_schema.md#mustexist)) as the FK
  case. For a relationship-named field, after resolving an {class}`IDRef <fastapi_restly.schemas.IDRef>` / {class}`IDSchema <fastapi_restly.schemas.IDSchema>` to an ORM object, Restly chooses the FK
  scalar, relationship object, or both based on the model constructor. If the
  client supplies both fields, Restly checks they refer to the same row.

If you declare a nested input field like `address: AddressRead` on a write
schema, the default CRUD implementation will pass that nested Pydantic object
through to the SQLAlchemy model constructor or attribute setter, which usually
does not match the ORM model shape. Use a flattened schema or override the
{meth}`create() <fastapi_restly.views.RestView.create>` / {meth}`update() <fastapi_restly.views.RestView.update>` business methods to transform the payload first.
[Work with Foreign Keys and Relationships](howto_relationship_idschema.md)
describes the supported reference-field patterns.

(list-parameters-lifecycle)=
## List Parameters Lifecycle

List endpoints accept URL query parameters of the form
``name=John``, ``age__gte=18``, ``sort=-created_at``, and
``page=2&page_size=50``. The full operator surface (`__ne`, `__isnull`,
`__contains`, `__icontains`, and more) is documented in
[Filter, Sort, and Paginate Lists](howto_query_modifiers.md).

During {meth}`before_include_view() <fastapi_restly.views.BaseRestView.before_include_view>`, the framework freezes a single class-level
attribute, {attr}`cls.listing_param_schema <fastapi_restly.views.BaseRestView.listing_param_schema>`: the query-parameter Pydantic schema generated
by {func}`create_list_params_schema(cls.schema, cls.model, default_page_size=..., max_page_size=...) <fastapi_restly.query.create_list_params_schema>`. The schema covers pagination, sorting, and one filter
parameter per response-schema field that maps to a filterable column on the
model, with optional operator suffixes. It is generated once per registration
and never re-derived.

Custom dialects (e.g. react-admin's
[`AsyncReactAdminView` / `ReactAdminView`](howto_react_admin.md)) live as
parallel view classes that bypass {func}`apply_list_params <fastapi_restly.query.apply_list_params>` entirely and
implement their own request/response contract.

## Restly Runtime Configuration

Restly exposes one public process-wide runtime configuration. Most applications
configure it once during startup:

```python
fr.configure(async_database_url="sqlite+aiosqlite:///app.db")
```

{func}`fr.configure(...) <fastapi_restly.db.configure>` rejects no-op calls; pass at least one setup option. The
authoritative list of accepted options is the
[API Reference's Database section](api_reference.md#database); this page does not
duplicate the contract.

Internally, Restly keeps a private context object so its own tests and fixtures
can isolate runtime state. That context is not a public multi-engine feature.
If an application needs multiple databases, wire a custom FastAPI dependency or
session generator for that view. Restly does not currently bind different views
to different named contexts.

## Session Factory Defaults

When {func}`fr.configure() <fastapi_restly.db.configure>` creates session factories from URLs or engines, Restly
sets a few SQLAlchemy session options intentionally:

| Factory | Autoflush | Expire on commit |
|---|---|---|
| Async `async_sessionmaker` | `False` | `False` |
| Sync `sessionmaker` | SQLAlchemy default (`True`) | `False` |

`expire_on_commit=False` is used for both sync and async sessions so ORM
objects remain readable after a route commits. Restly's write handlers commit
inside the request, and both the `after_commit` hook and the response-schema
conversion read attributes from the committed object afterwards. With
`expire_on_commit=True` the commit expires those attributes, so each of those
reads becomes an implicit database read: in async code it raises
`MissingGreenlet`, because the hook and the serializer both run in plain async
context; in sync code it quietly makes response rendering database-dependent.

The autoflush setting is intentionally different. Async sessions disable
autoflush because autoflush can turn a read operation into an implicit write and
database I/O must happen at explicit awaited SQLAlchemy boundaries. Restly's
async CRUD helpers flush explicitly when writes should hit the database. Sync
sessions keep SQLAlchemy's default autoflush behavior, preserving the usual
unit-of-work ergonomics where ORM queries see pending in-session changes.

Projects with custom sessionmakers or generators should preserve these defaults
unless they need different behavior.

## See Also

- [Filter, Sort, and Paginate Lists](howto_query_modifiers.md): the full
  filter, sort, and pagination reference.
