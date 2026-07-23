# API Reference

This page is the condensed reference for FastAPI-Restly. It documents the
generated HTTP endpoints and their query behavior, lists the key public
symbols with brief descriptions, and links to the full Python API reference
generated via Sphinx autodoc.

## Generated REST Endpoints

Register a view with `fr.include_view(app, ViewClass)` or `@fr.include_view(app)`. `fr.AsyncRestView` and `fr.RestView` expose the same generated resource surface:

| Method | Path | Purpose | Default Status |
|---|---|---|---|
| `GET` | `/{prefix}/` | List resources | `200` |
| `POST` | `/{prefix}/` | Create resource | `201` |
| `GET` | `/{prefix}/{id}` | Get resource by ID | `200` |
| `PATCH` | `/{prefix}/{id}` | Partial update | `200` |
| `DELETE` | `/{prefix}/{id}` | Delete resource | `204` |

The generated routes share these conventions:

- Updates use `PATCH`, not `PUT`. React Admin views also expose `PUT /{id}` for `ra-data-simple-rest`; see [React Admin Integration](howto_react_admin.md).
- `GET /{id}` and `DELETE /{id}` return `404` when the object is not found.
- Read-only schema fields are ignored on create/update.
- `*_id: fr.MustExist[int, Model]` inputs are validated against the database: the referenced row must exist. The scalar id is the related primary-key type, such as `int` or `UUID`.

## List Endpoint Behavior

`GET /{prefix}/` accepts filter, sort, and pagination parameters derived from
the response schema; keys use public field names (aliases included), and
dotted paths filter on relations. The table below gives the grammar in one
line each; the canonical treatment, including comma semantics, LIKE escaping,
foreign-key filtering, and alias rules, is
[Filter, Sort, and Paginate Lists](howto_query_modifiers.md):

| Kind | Form |
|---|---|
| Equality / OR | `?name=John`, `?status=active,pending` |
| Operators | `__in`, `__gte`, `__lte`, `__gt`, `__lt`, `__ne`, `__isnull`, `__contains`, `__icontains` |
| Relation paths | `?writer.authorName=Alice` (aliases per segment) |
| Sorting | `?sort=name,-created_at` |
| Pagination | `?page=2&page_size=10` |
| Unknown keys | rejected with `422` |

Pagination is opt-in, and the response is a bare JSON array unless the view
opts into the metadata envelope. Four class attributes on `RestView` /
`AsyncRestView` tune this behavior:

| Attribute | Type | Default | Purpose |
|---|---|---|---|
| {attr}`default_page_size <fastapi_restly.views.BaseRestView.default_page_size>` | `ClassVar[int \| None]` | `None` | Default `?page_size=`. `None` means no implicit cap: every matching row is returned. Set it and `max_page_size` on public endpoints. |
| {attr}`max_page_size <fastapi_restly.views.BaseRestView.max_page_size>` | `ClassVar[int]` | `1000` | Upper bound for `?page_size=`; higher values are rejected with `422`. |
| {attr}`include_pagination_metadata <fastapi_restly.views.BaseRestView.include_pagination_metadata>` | `ClassVar[bool]` | `False` | Set `True` to wrap the list items in the metadata envelope (`items`, `total`, `page`, `page_size`, `total_pages`). |
| {attr}`extra_query_params <fastapi_restly.views.BaseRestView.extra_query_params>` | `ClassVar[Iterable[str]]` | `()` | Query keys to allow beyond those derived from the response schema, for view-specific parameters consumed outside the list grammar (e.g. `?include_deleted=true`). |

The envelope's shape, when its page fields are populated versus `null`, and
custom alternatives are covered in
[Response Envelopes and List Metadata](howto_response_schema.md).

At a lower level, `fr.query.create_list_params_schema(...)` and `fr.query.apply_list_params(...)` power the generated list endpoints. Use the view classes for normal CRUD; call these helpers directly only for custom endpoints that need the same list grammar, and pass a validated params-schema instance rather than raw `QueryParams`.

## Endpoint Decorators

Use these decorators on methods in a view class:

| Decorator | HTTP Method | Default Status |
|---|---|---|
| `@fr.get(path)` | `GET` | `200` |
| `@fr.post(path)` | `POST` | `201` |
| `@fr.patch(path)` | `PATCH` | `200` |
| `@fr.put(path)` | `PUT` | `200` |
| `@fr.delete(path)` | `DELETE` | `204` |
| `@fr.route(path, ...)` | Custom | As configured |

The shorthand decorators explicitly set the default status code shown. Pass `status_code=` to override it.

Other keyword arguments pass through to FastAPI route registration: `response_model=`, `dependencies=`, `responses=`, `tags=`, and other `APIRouter.add_api_route()` options.

`@fr.put(...)` is available for custom endpoints, but default generated update endpoints use `PATCH`.

## Route Exclusion

To disable generated endpoints on a view, set `exclude_routes`:

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    exclude_routes = [fr.ViewRoute.DELETE, fr.ViewRoute.UPDATE]
```

The valid route values for exclusion are `fr.ViewRoute.GET_MANY`, `fr.ViewRoute.GET_ONE`, `fr.ViewRoute.CREATE`, `fr.ViewRoute.UPDATE`, and `fr.ViewRoute.DELETE`.

`exclude_routes` accepts `ViewRoute` values or the equivalent route-shell method names, such as `"delete_endpoint"`. Worked examples are in [Exclude generated routes](customize.md#exclude-generated-routes).

## Response Modeling

Generated CRUD endpoints derive their request and response schemas from the view's configuration:

- The response schema defaults to `schema` (or an auto-generated `*Read` schema when omitted).
- The input schema for `POST` defaults to the schema without read-only fields (`schema_create`, generated as `*Create`).
- The input schema for `PATCH` defaults to the optionalized schema (`schema_update`, generated as `*Update`).
- Alias-aware serialization is applied, so response payload keys follow schema aliases.

The derivation rules are described in [Generated Input Schemas](technical_details.md#generated-input-schemas).

## Key Public Symbols

With the endpoint surface covered, the rest of this page catalogs the public symbols by layer; the complete signatures live in the [autodoc pages](#full-python-api-autodoc).

### Model Base Classes

These base classes and mixins form the declarative foundation for SQLAlchemy models:

| Symbol | Description |
|---|---|
| {class}`fr.DataclassBase <fastapi_restly.models.DataclassBase>` | SQLAlchemy declarative base with dataclass semantics and auto snake_case table names. Mixes in SQLAlchemy's `AsyncAttrs`, so every model has `awaitable_attrs`. |
| {class}`fr.IDBase <fastapi_restly.models.IDBase>` | Convenience alias combining `DataclassBase` with an auto-incrementing integer `id` primary key. |
| {class}`fr.TimestampsMixin <fastapi_restly.models.TimestampsMixin>` | Dataclass mixin adding `created_at` / `updated_at` to any `DataclassBase` subclass. |
| {class}`fr.models.IDMixin <fastapi_restly.models.IDMixin>` | Dataclass mixin adding integer `id` to a custom `DataclassBase` subclass. |
| `fastapi_restly.models.CASCADE_ALL_ASYNC` | Cascade string for use with `relationship(cascade=...)` in async SQLAlchemy models. Equivalent to `"save-update, merge, delete, expunge"`. SQLAlchemy's default `"all"` includes `"refresh-expire"` which is incompatible with async sessions. Import from `fastapi_restly.models` (not exposed at the top level). |
| `fastapi_restly.models.CASCADE_ALL_DELETE_ORPHAN_ASYNC` | Like `CASCADE_ALL_ASYNC` but also includes `"delete-orphan"`. |

FastAPI-Restly also works with ordinary SQLAlchemy models that inherit from your own `DeclarativeBase`. Use `fr.IDBase` for Restly's dataclass convenience base; bring your own base for standard constructor semantics or existing model layers.

`RestView` and `AsyncRestView` assume one scalar resource identifier at `/{id}`. The column can have another name when you provide explicit schemas and `id_type`, but the generated CRUD routes, `IDSchema`, `IDRef`, React Admin, and OpenAPI identity shape all remain scalar-id contracts. For composite keys, use `fr.View` and explicit routes such as `@fr.get("/{tenant_id}/{slug}")`.

### Schema Classes and Utilities

These classes and markers define how model data crosses the wire; the reference-field types (`MustExist`, `IDRef`, `IDSchema`) are treated in depth in [Work with Foreign Keys and Relationships](howto_relationship_idschema.md):

| Symbol | Description |
|---|---|
| {class}`fr.BaseSchema <fastapi_restly.schemas.BaseSchema>` | Thin Pydantic base equivalent to `class BaseSchema(pydantic.BaseModel): model_config = pydantic.ConfigDict(from_attributes=True)`. Plain Pydantic models are also accepted for explicit create/update schemas. |
| {class}`fr.IDSchema <fastapi_restly.schemas.IDSchema>` | Response-schema base class that adds the resource's own read-only `id` field. |
| {class}`fr.MustExist[int, Model] <fastapi_restly.schemas.MustExist>` | Existence-checked scalar FK type for a `*_id` column. Primary-key type first, target model second (`fr.MustExist[UUID, Account]` for a UUID key; drop the model to infer it from a single `ForeignKey`). The value stays a plain id on request and response, and Restly validates the referenced row exists. |
| {class}`fr.IDRef[Model] <fastapi_restly.schemas.IDRef>` | Relationship reference with a flat-id wire; resolves the id to the related object. Wire format is the raw id (`5`) on request and response; dict input (`{"id": 5}`) is also accepted. Use this for a relationship field and React Admin scalar id arrays. |
| {class}`fr.IDSchema[Model] <fastapi_restly.schemas.IDSchema>` | Nested relationship-object field type. Wire format is `{"id": 5}` on request and response. Use this when a client expects relationship objects instead of flat scalar ids. |
| {class}`fr.TimestampsSchemaMixin <fastapi_restly.schemas.TimestampsSchemaMixin>` | Pydantic mixin adding read-only `created_at` / `updated_at` fields to a schema. |
| `fr.ReadOnly[T]` | Type annotation marker. Fields annotated `ReadOnly[T]` are excluded from create/update inputs. |
| `fr.WriteOnly[T]` | Type annotation marker. Fields annotated `WriteOnly[T]` are stripped by `self.to_response_schema(obj)`, which the generated CRUD and ReactAdmin routes use. Direct FastAPI/Pydantic serialization treats it as schema metadata only. |
| {func}`fastapi_restly.schemas.create_schema_from_model(model) <fastapi_restly.schemas.create_schema_from_model>` | Auto-generate a Pydantic schema from a SQLAlchemy model. Useful for scaffolding, prototypes, and internal tools; prefer explicit schemas for stable public API contracts. Import from `fastapi_restly.schemas`; it is intentionally not exported at the top level. |

### View Classes

Views are the routing layer; each class below is a registration entry point:

| Symbol | Description |
|---|---|
| {class}`fr.View <fastapi_restly.views.View>` | Base class for all class-based views. Subclass this directly when you do not need CRUD; add endpoints with `@fr.get`, `@fr.post`, etc. |
| {class}`fastapi_restly.views.BaseRestView` | Supported advanced base class for custom CRUD foundations shared by sync and async views. Import from `fastapi_restly.views`; it is intentionally not exported at the top level. |
| {class}`fr.AsyncRestView <fastapi_restly.views.AsyncRestView>` | Async CRUD view. Use with async SQLAlchemy sessions. |
| {class}`fr.RestView <fastapi_restly.views.RestView>` | Sync CRUD view. Use with sync SQLAlchemy sessions. |
| {class}`fr.ListingResult <fastapi_restly.views.ListingResult>` | Value object returned by `get_many` (and `handle_get_many`), with `.objects`, `.total_count`, and `.query_params`, before `to_listing_response` formats the HTTP response. |
| {class}`fr.AsyncReactAdminView <fastapi_restly.views.AsyncReactAdminView>` | Async CRUD view that speaks the `ra-data-simple-rest` wire contract used by [react-admin](https://marmelab.com/react-admin/). See [React Admin Integration](howto_react_admin.md). |
| {class}`fr.ReactAdminView <fastapi_restly.views.ReactAdminView>` | Sync variant of `AsyncReactAdminView`. |

### View Method Surface

Each CRUD verb on `RestView` / `AsyncRestView` is split into three tiers: the
endpoint method (`<verb>_endpoint`), the handler (`handle_<verb>`), and
the business method (`<verb>`). You override the layer that owns your change;
the model and the decision table live in
[Customize RestView](customize.md).

Alongside the tiers are cross-cutting **override points** (`build_query`,
`apply_query_params`, `count`, `authorize`,
`before_commit` / `after_commit`, `to_response`, `snapshot`) and **domain
utilities** that you call rather than override (`make_new_object`,
`update_object`, `save_object`, `delete_object`). `make_new_object` /
`update_object` are also the cooperative override point for field stamps.

On `AsyncRestView` every method below is `async`; the signatures are otherwise identical.

| Tier / kind | Method | Signature | Return | Purpose |
|---|---|---|---|---|
| Route shell | {meth}`get_many_endpoint <fastapi_restly.views.RestView.get_many_endpoint>` | `(query_params)` | response schema list or pagination envelope | `GET /`; validates query parameters and serializes the listing result via `to_response`. |
| Route shell | {meth}`get_one_endpoint <fastapi_restly.views.RestView.get_one_endpoint>` | `(id)` | response schema | `GET /{id}`; serializes one retrieved object. |
| Route shell | {meth}`create_endpoint <fastapi_restly.views.RestView.create_endpoint>` | `(schema_obj)` | response schema | `POST /`; serializes the created object. |
| Route shell | {meth}`update_endpoint <fastapi_restly.views.RestView.update_endpoint>` | `(id, schema_obj)` | response schema | `PATCH /{id}`; serializes the updated object. |
| Route shell | {meth}`delete_endpoint <fastapi_restly.views.RestView.delete_endpoint>` | `(id)` | `fastapi.Response` | `DELETE /{id}`; returns `204` by default. |
| Request handler | {meth}`handle_get_many <fastapi_restly.views.RestView.handle_get_many>` | `(query_params)` | `ListingResult[Model]` | Run `authorize("get_many")`, then `get_many`. |
| Request handler | {meth}`handle_get_one <fastapi_restly.views.RestView.handle_get_one>` | `(id)` | `Model` | Load through `get_one` (scoped, 404), then `authorize("get_one", obj=...)`. Reusable from a custom action as "scoped load + 404 + read-auth". |
| Request handler | {meth}`handle_create <fastapi_restly.views.RestView.handle_create>` | `(schema_obj)` | `Model` | Authorize, run `create`, then the commit bracket. |
| Request handler | {meth}`handle_update <fastapi_restly.views.RestView.handle_update>` | `(id, schema_obj)` | `Model` | Load, authorize, snapshot, run `update`, then the commit bracket. |
| Request handler | {meth}`handle_delete <fastapi_restly.views.RestView.handle_delete>` | `(id)` | `None` | Load, authorize, snapshot, run `delete`, then the commit bracket. |
| Custom-action bracket | {meth}`write_action <fastapi_restly.views.RestView.write_action>` | `(action, *, obj=None, data=None)` | context manager | Entered as `async with self.write_action("publish", obj=...):`, it runs the full bracket around your inline mutation: authorize and snapshot on enter; `before_commit`, commit, and `after_commit` on exit. Use it for a custom write *action* that is not a plain create/update/delete; deposit a create's new object on the yielded handle's `.obj`. The implementation is shared with the CRUD handlers via the self-free `run_write_action` (in `fastapi_restly.views`). |
| Business method | {meth}`get_many <fastapi_restly.views.RestView.get_many>` | `(query_params)` | `ListingResult[Model]` | Scoped, filtered, paginated page plus total count, via `build_query` + `apply_query_params` + `count`. Auth-free. |
| Business method | {meth}`get_one <fastapi_restly.views.RestView.get_one>` | `(id)` | `Model` | Load one row through `build_query` or raise `fr.exc.NotFound`. Visibility comes from `build_query`, so a hidden row is a clean 404 for every caller. Auth-free. |
| Business method | {meth}`create <fastapi_restly.views.RestView.create>` | `(schema_obj)` | `Model` | Build a new object and save it. Commit-free: the usual create override point. |
| Business method | {meth}`update <fastapi_restly.views.RestView.update>` | `(obj, schema_obj)` | `Model` | Apply the update payload to `obj` and save it. Commit-free. |
| Business method | {meth}`delete <fastapi_restly.views.RestView.delete>` | `(obj)` | `None` | Delete `obj`. Override (e.g. on a soft-delete mixin) to flip a timestamp instead. |
| Override point | {meth}`build_query <fastapi_restly.views.RestView.build_query>` | `()` | `sqlalchemy.Select` | Base read query shared by `get_many`, `count`, and `get_one`; add `WHERE` clauses here for scope/soft-delete/visibility. |
| Override point | {meth}`apply_query_params <fastapi_restly.views.RestView.apply_query_params>` | `(query, query_params)` | `sqlalchemy.Select` | Apply URL filter/sort/pagination to `query`. Override for a non-default URL grammar. |
| Override point | {meth}`count <fastapi_restly.views.RestView.count>` | `(query)` | `int` | Total for the list: receives the same params-applied query and strips `ORDER BY`, `LIMIT`, and `OFFSET` before counting. Override for estimated counts on huge tables. |
| Override point | {meth}`authorize <fastapi_restly.views.RestView.authorize>` | `(action, obj=None, data=None)` | `None` | Gate a verb. A no-op by default; override to enforce policy and raise `fr.exc.Forbidden` / `fr.exc.NotFound` to reject. Row *visibility* belongs in `build_query`. |
| Override point | {meth}`before_commit <fastapi_restly.views.RestView.before_commit>` | `(action, new, old=None)` | `None` | In-transaction side effect (outbox/audit rows), atomic with the write. `old` is the pre-mutation snapshot dict. |
| Override point | {meth}`after_commit <fastapi_restly.views.RestView.after_commit>` | `(action, new, old=None)` | `None` | Post-commit side effect (email, webhook, cache invalidation). `old` enables dirty detection. |
| Override point | {meth}`to_response <fastapi_restly.views.BaseRestView.to_response>` | `(obj_or_list, shape=ResponseShape.SINGLE)` | response payload | The single wire-level response method, called by the endpoint methods with the wire `ResponseShape` (`SINGLE` / `LISTING` / `EMPTY`), not the write action. Override for envelopes or custom status codes; for a per-verb HTTP contract change, override that verb's endpoint method. |
| Override point | {meth}`snapshot <fastapi_restly.views.BaseRestView.snapshot>` | `(obj)` | `dict[str, Any]` | Frozen capture of an object's column values at load time, passed as `old` to the commit hooks. |
| Helper | {meth}`to_response_schema <fastapi_restly.views.BaseRestView.to_response_schema>` | `(obj)` | response schema | Validate and serialize an ORM object with Restly's alias/reference/write-only handling. Override for custom projections or an intentional `model_construct()` fast path. |
| Helper | {meth}`to_listing_response <fastapi_restly.views.BaseRestView.to_listing_response>` | `(query_params, listing_result)` | response schema list or pagination envelope | Serialize a `ListingResult` into the configured list HTTP response shape. |
| Helper | {meth}`to_paginated_listing_response <fastapi_restly.views.BaseRestView.to_paginated_listing_response>` | `(query_params, listing_result)` | pagination envelope | Serialize a `ListingResult` into the paginated list response shape. |
| Domain utility | `make_new_object` | `(schema_obj)` | `Model` | Build and stage a new object without flushing. The cooperative override point for stamping extra fields on create: call `super()`, then mutate the returned object. |
| Domain utility | `update_object` | `(obj, schema_obj)` | `Model` | Apply writable fields without flushing. The cooperative override point for stamping extra fields on update: call `super()`, then mutate the returned object. |
| Domain utility | `save_object` | `(obj)` | `Model` | Flush and refresh a staged object, then eager-load the relationships the response schema names. Does not commit; `handle_<verb>` owns the commit. |
| Domain utility | `delete_object` | `(obj)` | `None` | Delete and flush an existing object. Does not commit. |

Internal methods prefixed with `_`, such as `_reject_unknown_query_params`, are implementation details even though they are visible on instances.

See [Class-Based Views](class_based_views.md#the-view-hierarchy) for the class hierarchy, [Customize RestView](customize.md) for examples of choosing which tier to override, and [Use Type Annotations](howto_typing.md) for the typed signatures of these methods.

### View Class Attributes

Every `View` subclass, CRUD or not, honors these class attributes:

| Attribute | Type | Description |
|---|---|---|
| {attr}`prefix <fastapi_restly.views.View.prefix>` | `ClassVar[str]` | URL prefix for all routes in the view (e.g. `"/users"`). Required. |
| {attr}`tags <fastapi_restly.views.View.tags>` | `ClassVar[Iterable[str \| Enum] \| None]` | OpenAPI tags. When unset, a tag derived from the view class name is used; setting this replaces the derived tag. |
| {attr}`dependencies <fastapi_restly.views.View.dependencies>` | `ClassVar[Sequence[Depends] \| None]` | FastAPI dependencies applied to every route in the view. |
| {attr}`responses <fastapi_restly.views.View.responses>` | `ClassVar[dict[int \| str, dict[str, Any]]]` | OpenAPI response overrides. `View` defaults to `{}`; `BaseRestView` defaults to `{404: {"description": "Not found"}}`. |

`RestView` and `AsyncRestView` add the following:

| Attribute | Type | Description |
|---|---|---|
| {attr}`schema <fastapi_restly.views.BaseRestView.schema>` | `ClassVar[type[pydantic.BaseModel]]` | The read/response schema. If omitted, auto-generated from `model` as `ModelRead`. |
| {attr}`schema_create <fastapi_restly.views.BaseRestView.schema_create>` | `ClassVar[type[pydantic.BaseModel]]` | Schema for `POST` input. Auto-derived by removing `ReadOnly` fields and named `ModelCreate`. |
| {attr}`schema_update <fastapi_restly.views.BaseRestView.schema_update>` | `ClassVar[type[pydantic.BaseModel]]` | Schema for `PATCH` input. Auto-derived by making all writable fields optional and named `ModelUpdate`. |
| {attr}`model <fastapi_restly.views.BaseRestView.model>` | `ClassVar[type[DeclarativeBase]]` | The SQLAlchemy model class. |
| {attr}`id_type <fastapi_restly.views.BaseRestView.id_type>` | `ClassVar[type]` | Scalar primary-key type used in the generated `/{id}` routes. Defaults to `int`. |
| {attr}`exclude_routes <fastapi_restly.views.BaseRestView.exclude_routes>` | `ClassVar[Iterable[str \| ViewRoute]]` | Route names to suppress. |

The list-tuning attributes (`default_page_size`, `max_page_size`, `include_pagination_metadata`, `extra_query_params`) are tabulated under [List Endpoint Behavior](#list-endpoint-behavior).

### Advanced Object Helpers

These helpers build, update, delete, and save ORM objects from schemas. Use them outside view instance methods: custom routes, services, workers, or tests. Sync and async variants are exported at the top level and from `fastapi_restly.objects`.

| Symbol | Description |
|---|---|
| {func}`fr.objects.make_new_object(session, model_cls, schema_obj, schema_cls=None) <fastapi_restly.objects.make_new_object>` | Build a new `model_cls` instance from `schema_obj`, existence-check any `MustExist[...]` FK ids and resolve any `IDRef[...]` / `IDSchema[...]` reference fields against the database, and add the object to `session`. It does not flush; call `fr.objects.save_object(session, obj)` afterwards to persist. |
| {func}`fr.objects.update_object(session, obj, schema_obj, schema_cls=None) <fastapi_restly.objects.update_object>` | Apply the schema's writable fields onto an existing ORM `obj` and resolve FK fields. It does not flush; call `fr.objects.save_object(session, obj)` afterwards to persist. |
| {func}`fr.objects.save_object(session, obj) <fastapi_restly.objects.save_object>` | Flush the session and refresh `obj` so server-side defaults and generated columns (PKs, timestamps) are populated. Returns `obj`. This is where create/update writes hit the database. |
| {func}`fr.objects.delete_object(session, obj) <fastapi_restly.objects.delete_object>` | Delete `obj` and flush the session. |
| {func}`fr.objects.async_make_new_object(session, model_cls, schema_obj, schema_cls=None) <fastapi_restly.objects.async_make_new_object>` | Async equivalent of `fr.objects.make_new_object`. Pass an `AsyncSession`. |
| {func}`fr.objects.async_update_object(session, obj, schema_obj, schema_cls=None) <fastapi_restly.objects.async_update_object>` | Async equivalent of `fr.objects.update_object`. |
| {func}`fr.objects.async_save_object(session, obj) <fastapi_restly.objects.async_save_object>` | Async equivalent of `fr.objects.save_object`. |
| {func}`fr.objects.async_delete_object(session, obj) <fastapi_restly.objects.async_delete_object>` | Async equivalent of `fr.objects.delete_object`. |

The view methods of the same names (in the
[method surface](#view-method-surface)) wrap these helpers, binding
`self.session`, `self.model`, and `self.schema`; reach for the `fr.objects`
forms in custom routes that touch a model other than `self.model`, and in
services, workers, or tests.

### Database

These symbols cover connection configuration and session access:

| Symbol | Description |
|---|---|
| `fr.AsyncSessionDep` | FastAPI `Depends`-compatible async session dependency. |
| `fr.SessionDep` | FastAPI `Depends`-compatible sync session dependency. |
| {func}`fr.open_async_session() <fastapi_restly.db.open_async_session>` | Open an async SQLAlchemy session context manager for use outside request handling, for example in background jobs or scripts. |
| {func}`fr.open_session() <fastapi_restly.db.open_session>` | Open a sync SQLAlchemy session context manager for use outside request handling, for example in background jobs or scripts. |
| {func}`fr.configure(async_database_url=..., ...) <fastapi_restly.db.configure>` | Configure the framework. Accepts async/sync URLs, engines, session makers, custom session generators, and the `warn_on_uncommitted` / `warn_on_misuse` settings. |
| {func}`fr.db.get_async_engine() <fastapi_restly.db.get_async_engine>` | Return the configured `AsyncEngine` instance. |
| {func}`fr.db.get_engine() <fastapi_restly.db.get_engine>` | Return the configured sync `Engine` instance. |

Restly has one public process-wide configuration, described further in
[Restly Runtime Configuration](technical_details.md#restly-runtime-configuration).
Configure it once during application startup:

```python
fr.configure(async_database_url="sqlite+aiosqlite:///app.db")
```

`fr.configure(...)` must receive at least one setup option: an app, database URL, engine, session maker, custom session generator, or a `warn_on_uncommitted` / `warn_on_misuse` setting. A bare `fr.configure()` raises `TypeError`.

Pass `warn_on_misuse=True` to enable opt-in registration-time misuse warnings (`fr.exc.RestlyMisuseWarning`): `include_view` then flags route-shell overrides, direct `session.commit()` calls in view methods, and CRUD route sets hand-rolled on a bare `View`, each with the idiomatic fix named. It is off by default and intended for development, project templates, and CI.

For multiple databases, use FastAPI and SQLAlchemy directly: add a custom dependency on a view, or pass a custom session generator to `fr.configure(...)`. Restly does not provide a public multi-context or multi-engine API. See [Use a custom session dependency on one view](howto_existing_project.md#use-a-custom-session-dependency-on-one-view).

Restly's write handlers own the commit: each runs `before_commit`, then the commit, then `after_commit` around domain logic. Session dependencies do **not** commit on response; they roll back and close on exit.

A **custom write route** should use `self.write_action(...)` or reuse a `handle_<verb>`; see [Customize RestView](customize.md). Commit manually only for shapes the bracket does not model, such as a batch write with one final commit.

Restly warns (`RestlyUncommittedChangesWarning`) when a request finishes with uncommitted session changes; this is the tell of a custom write route that forgot to commit. Fix the missing commit (`write_action(...)` or a `handle_<verb>`), or suppress a deliberate dry run with `session.info["_fr_suppress_uncommitted"] = True`. The global `fr.configure(warn_on_uncommitted=False)` opt-out exists but is rarely the right response to the warning.

### Exceptions

There are two families: configuration errors subclass `RestlyError`, and request-time HTTP errors subclass `fastapi.HTTPException` via `RestlyHTTPError`. Typed classes let you target Restly errors with `app.add_exception_handler(...)`; recipes, the app-wide envelope pattern, and the 422-vs-400 boundary are in [Shape Error Responses](howto_error_responses.md).

| Symbol | Description |
|---|---|
| {class}`fr.exc.RestlyError <fastapi_restly.exc.RestlyError>` | Base class for FastAPI-Restly framework (configuration-time) errors. |
| {class}`fr.exc.RestlyConfigurationError <fastapi_restly.exc.RestlyConfigurationError>` | Raised when a public Restly helper needs configuration that has not been set up yet, such as calling `fr.open_session()` before `fr.configure(...)`. |
| {class}`fr.exc.RestlyHTTPError <fastapi_restly.exc.RestlyHTTPError>` | Base for Restly's request-time HTTP errors. Subclass of `fastapi.HTTPException`; each subclass sets a status code. |
| {class}`fr.exc.NotFound <fastapi_restly.exc.NotFound>` | HTTP `404`. Raised by `get_one` when a row does not exist or is hidden by `build_query`; also raisable from `authorize` to hide a row's existence. |
| {class}`fr.exc.Forbidden <fastapi_restly.exc.Forbidden>` | HTTP `403`. Raise from an `authorize` override to reject a verb. |
| {class}`fr.exc.Conflict <fastapi_restly.exc.Conflict>` | HTTP `409`. For request conflicts with the current resource state. |
| {class}`fr.exc.BadQueryParam <fastapi_restly.exc.BadQueryParam>` | HTTP `400`. For an invalid filter/sort/pagination query parameter. |

### Testing

The testing utilities provide a status-asserting client and savepoint-based isolation:

| Symbol | Description |
|---|---|
| {class}`fastapi_restly.testing.RestlyTestClient` | Sync test client wrapper around FastAPI's `TestClient` with default status-code assertions. It can test async FastAPI routes and `AsyncRestView` endpoints. |
| {func}`fastapi_restly.testing.activate_savepoint_only_mode(make_session) <fastapi_restly.testing.activate_savepoint_only_mode>` | Wrap a session factory in savepoint-only mode so test data never commits to the database; intended for tests. Use it when building your own harness without the shipped fixtures (which implement the same isolation themselves). |
| {func}`fastapi_restly.testing.deactivate_savepoint_only_mode(make_session) <fastapi_restly.testing.deactivate_savepoint_only_mode>` | Restore normal session behavior after testing. |

The pytest fixtures below are auto-loaded by the `testing` extra; their full
behavior is documented in [Testing](howto_testing.md#fixture-reference):

| Fixture | Scope | One-liner |
|---|---|---|
| `restly_app` | function | Bare `FastAPI()`; override in `conftest.py` to return your app. |
| `restly_client` | function | `RestlyTestClient` wrapping `restly_app`. |
| `restly_session` | function | Savepoint-isolated SQLAlchemy `Session`; skips without a sync DB. |
| `restly_async_session` | function | Async savepoint-isolated session; skips without an async DB. |
| `restly_project_root` | session | `Path` of the nearest ancestor with a `pyproject.toml`. |

### Default Exception Handling

FastAPI-Restly installs a default handler for SQLAlchemy `IntegrityError` on FastAPI apps. The handler translates database integrity conflicts (unique constraint, foreign-key, not-null, and check-constraint violations) into HTTP `409 Conflict` responses using FastAPI's normal error body shape:

```json
{
  "detail": "Unique constraint violated on user.email"
}
```

The exact `detail` text is best-effort and depends on the database driver. The handler recognizes common PostgreSQL SQLSTATE integrity codes and SQLite constraint messages; unknown dialects fall back to a generic conflict message. This mapping and the surrounding error-shaping recipes are also covered in [Shape Error Responses](howto_error_responses.md#database-conflicts-integrityerror-to-409).

Registration is automatic in either of these cases:

- `fr.configure(app=app, ...)` is called with the default
  `install_default_exception_handlers=True`.
- A view is registered directly on a `FastAPI` app with `fr.include_view(app)`.
  This fallback covers apps that configure database sessions separately.

Restly skips this default only when the app already has a `sqlalchemy.exc.IntegrityError` handler. Generic handlers do not block registration.

To opt out, disable the default handlers in `fr.configure`:

```python
fr.configure(
    app=app,
    async_database_url="sqlite+aiosqlite:///app.db",
    install_default_exception_handlers=False,
)
```

To use your own response format, register your `IntegrityError` handler before Restly installs its defaults:

```python
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

@app.exception_handler(IntegrityError)
async def integrity_error_handler(request, exc):
    return JSONResponse(
        status_code=409,
        content={"error": {"code": "constraint_conflict"}},
    )

fr.configure(app=app, async_database_url="sqlite+aiosqlite:///app.db")
```

## Important Limitations and Capabilities

The generated CRUD surface has deliberate boundaries; the points below summarize what is and is not supported.

- Nested schemas are supported for **responses** and relation filtering, including nested aliases.
- Full nested schemas are **not** supported for create/update payloads by the default CRUD flow; write payloads must map directly to model fields, or use model-aware reference fields such as `*_id: fr.MustExist[int, Model]` for FK columns and relationship fields typed as `IDRef[Model]` or `IDSchema[Model]`.
- Ordinary SQLAlchemy `DeclarativeBase` models work with generated CRUD views.
- UUID and other non-`int` scalar primary keys are supported through `id_type`, `fr.MustExist[UUID, Model]`, `IDRef[Model]`, and `IDSchema[Model]`.
- Composite primary keys are not supported by generated `RestView` / `AsyncRestView` CRUD routes; use `fr.View` for custom route shapes.

## Full Python API (Autodoc)

The pages below contain the complete, signature-level API documentation generated from the source:

```{toctree}
:maxdepth: 2

api/index
```

```{toctree}
:maxdepth: 1
:hidden:

technical_details
changelog
```
