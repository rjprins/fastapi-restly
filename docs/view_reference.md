# View Reference

This page lists everything you can set, override, or call on a view.
[CRUD Routes](rest_views.md) explains the default HTTP contract, and
[Request Lifecycle & Hooks](customize.md) explains which method to override.
[Modules](api/index.rst) documents the complete signatures.

## View classes

Each class below is a registration entry point:

| Symbol | Description |
|---|---|
| {class}`fr.View <fastapi_restly.views.View>` | Base class for all class-based views. Subclass this directly when you do not need CRUD; add endpoints with `@fr.get`, `@fr.post`, etc. |
| {class}`fastapi_restly.views.BaseRestView` | Supported advanced base class for custom CRUD foundations shared by sync and async views. Import from `fastapi_restly.views`; it is intentionally not exported at the top level. |
| {class}`fr.AsyncRestView <fastapi_restly.views.AsyncRestView>` | Async CRUD view. Use with async SQLAlchemy sessions. |
| {class}`fr.RestView <fastapi_restly.views.RestView>` | Sync CRUD view. Use with sync SQLAlchemy sessions. |
| {class}`fr.ListResult <fastapi_restly.views.ListResult>` | Value object returned by `get_many` (and `handle_get_many`), with `.objects`, `.total_count`, and `.list_params`, before `to_list_response` formats the HTTP response. |
| {class}`fr.AsyncReactAdminView <fastapi_restly.views.AsyncReactAdminView>` | Async CRUD view that follows the `ra-data-simple-rest` HTTP contract used by [react-admin](https://marmelab.com/react-admin/). See [React Admin](howto_react_admin.md). |
| {class}`fr.ReactAdminView <fastapi_restly.views.ReactAdminView>` | Sync variant of `AsyncReactAdminView`. |

(view-class-attributes)=
## Class attributes

Every `View` subclass, CRUD or not, honors these class attributes:

| Attribute | Type | Description |
|---|---|---|
| {attr}`prefix <fastapi_restly.views.View.prefix>` | `ClassVar[str]` | URL prefix for all routes in the view (e.g. `"/users"`). Required. |
| {attr}`tags <fastapi_restly.views.View.tags>` | `ClassVar[Iterable[str \| Enum] \| None]` | OpenAPI tags. When unset, a tag derived from the view class name is used; setting this replaces the derived tag. |
| {attr}`dependencies <fastapi_restly.views.View.dependencies>` | `ClassVar[Sequence[Depends] \| None]` | FastAPI dependencies applied to every route in the view. Each class in the hierarchy adds its own, base first. |
| {attr}`responses <fastapi_restly.views.View.responses>` | `ClassVar[dict[int \| str, dict[str, Any]]]` | OpenAPI response overrides. `View` defaults to `{}`; `BaseRestView` defaults to `{404: {"description": "Not found"}}`. Each class in the hierarchy adds its own, and a subclass's entry for a status code wins. |
| {attr}`route_options <fastapi_restly.views.View.route_options>` | `ClassVar[Mapping[str \| ViewRoute, Mapping[str, Any]]]` | Per-route FastAPI keyword arguments. See [OpenAPI customization](#default-route-metadata) for names, operation IDs, and override rules. |

`RestView` and `AsyncRestView` add the following:

| Attribute | Type | Description |
|---|---|---|
| {attr}`schema <fastapi_restly.views.BaseRestView.schema>` | `ClassVar[type[pydantic.BaseModel]]` | The view's schema. Restly derives the response, create and update schemas from it. If omitted, auto-generated from `model` as `<Model>Schema`. |
| {attr}`schema_response <fastapi_restly.views.BaseRestView.schema_response>` | `ClassVar[type[pydantic.BaseModel]]` | The class of everything that goes out: the responses, the list items, the fields the list params filter on, and the relationships the view loads. Auto-derived by removing `WriteOnly` fields and named `ModelResponse`. See [Your own response class](#own-response-class). |
| {attr}`schema_create <fastapi_restly.views.BaseRestView.schema_create>` | `ClassVar[type[pydantic.BaseModel]]` | Schema for `POST` input. Auto-derived by removing `ReadOnly` fields and named `ModelCreate`. |
| {attr}`schema_update <fastapi_restly.views.BaseRestView.schema_update>` | `ClassVar[type[pydantic.BaseModel]]` | Schema for `PATCH` input. Auto-derived by making all writable fields optional and named `ModelUpdate`. |
| {attr}`model <fastapi_restly.views.BaseRestView.model>` | `ClassVar[type[Any]]` | The SQLAlchemy mapped class. A class without a mapper raises at class definition. |
| {attr}`id_type <fastapi_restly.views.BaseRestView.id_type>` | `ClassVar[type \| None]` | Type of the `{id}` path parameter on the default routes. `None` (the default) takes the model's primary key type; a composite key gets `int`. |
| {attr}`exclude_routes <fastapi_restly.views.BaseRestView.exclude_routes>` | `ClassVar[Iterable[str \| ViewRoute]]` | Default routes to leave out: `fr.ViewRoute` values (`GET_MANY`, `GET_ONE`, `CREATE`, `UPDATE`, `DELETE`) or endpoint method names such as `"delete_endpoint"`. See [Exclude CRUD routes](customize.md#exclude-crud-routes). |
| {attr}`schema_list_params <fastapi_restly.views.BaseRestView.schema_list_params>` | `ClassVar[type[pydantic.BaseModel]]` | The list params (filter, sort, page) as a Pydantic model, generated from `schema_response` by `fr.query.derive_schema_list_params`. Any route method that declares a `list_params` parameter is annotated with it, so a custom list route takes the same parameters as `GET /`. |
| {attr}`pagination <fastapi_restly.views.BaseRestView.pagination>` | `ClassVar[NumberedPagination \| NoPagination \| None]` | How list endpoints paginate, `NumberedPagination()` by default. A `NoPagination` returns every row and runs no count query; `None` is short for `NoPagination()`, a plain `Envelope` (`data` only). Views inherit it, so a project base view sets it once. See [Pagination settings](#pagination-settings). |
| {attr}`extra_query_params <fastapi_restly.views.BaseRestView.extra_query_params>` | `ClassVar[Iterable[str]]` | Query keys to allow beyond those derived from the response class, for view-specific parameters read from the request outside the list grammar (e.g. `?include_deleted=true`). A key that the endpoint method or a dependency declares, or an `APIKeyQuery` key, needs no entry. |

(pagination-settings)=
### Pagination settings

{class}`fr.NumberedPagination <fastapi_restly.views.NumberedPagination>` holds the
settings. Each is a keyword argument, and `replace(**changes)` returns a
checked copy:

| Setting | Default | Purpose |
|---|---|---|
| {attr}`default_page_size <fastapi_restly.views.NumberedPagination.default_page_size>` | `50` | Page size when the client sends none. Lower it and cap `max_page_size` on public endpoints. |
| {attr}`max_page_size <fastapi_restly.views.NumberedPagination.max_page_size>` | `1000` | Largest page size a client may ask for; a larger one is rejected with `422`. |
| {attr}`max_page <fastapi_restly.views.NumberedPagination.max_page>` | `None` | Largest page number a client may ask for; a larger one is rejected with `422`. |
| {attr}`page_query_param <fastapi_restly.views.NumberedPagination.page_query_param>` | `"page"` | Query parameter for the page number. |
| {attr}`page_size_query_param <fastapi_restly.views.NumberedPagination.page_size_query_param>` | `"page_size"` | Query parameter for the page size. |
| `envelope` | `PaginatedEnvelope` | The list response model: a generic model filled by field name from `data`, `total_count`, `page`, `page_size` and `total_pages`. |

{class}`fr.NoPagination <fastapi_restly.views.NoPagination>` has one setting,
`envelope`, `Envelope` by
default, filled from `data` and `total_count`, the number of rows.

The envelope's shape and custom alternatives are covered in
[Responses](howto_response_schema.md).

(endpoint-decorators)=
## Endpoint decorators

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

`@fr.put(...)` is available for custom endpoints, but the default update route
uses `PATCH`.

(view-method-surface)=
## Methods

Each CRUD route on `RestView` / `AsyncRestView` runs through three methods:
the endpoint method (`<verb>_endpoint`), the handler (`handle_<verb>`), and
the business method (`<verb>`). The handler handles the steps and is final.
You override the endpoint method or the business method, whichever owns your
change. The steps and the decision table live in
[Request Lifecycle & Hooks](#view-request-steps).

Alongside these three are the declared read [scope](scopes.md) (the
`scope` class attribute) and cross-cutting **override points**
(`apply_list_params`, `count`, `authorize`,
`before_action_commit` / `after_action_commit`, `to_response`,
`get_relationship_loader_options`, `snapshot`). The handlers and the
**domain utilities** (`make_new_object`, `update_object`, `save_object`) are
final: you call them rather than override them, and a view class that defines
one fails at class definition.

On `AsyncRestView`, the handlers, the business methods, the domain utilities,
`count`, `authorize` and the transaction hooks are `async def`; the response
methods, `apply_list_params`, `snapshot` and `get_relationship_loader_options`
are plain `def`, and the two brackets are entered with `async with`. Argument
names are identical between variants.

| Kind | Method | Signature | Return | Purpose |
|---|---|---|---|---|
| Endpoint method | {meth}`get_many_endpoint <fastapi_restly.views.RestView.get_many_endpoint>` | `(list_params)` | the list response, such as `UserListResponse`: the pagination's envelope (`PaginatedEnvelope` by default, or `Envelope`) filled with the response class | `GET /`; validates query parameters and serializes the list result via `to_response`. |
| Endpoint method | {meth}`get_one_endpoint <fastapi_restly.views.RestView.get_one_endpoint>` | `(id)` | response schema | `GET /{id}`; serializes one retrieved object. |
| Endpoint method | {meth}`create_endpoint <fastapi_restly.views.RestView.create_endpoint>` | `(schema_obj)` | response schema | `POST /`; serializes the created object. |
| Endpoint method | {meth}`update_endpoint <fastapi_restly.views.RestView.update_endpoint>` | `(id, schema_obj)` | response schema | `PATCH /{id}`; serializes the updated object. |
| Endpoint method | {meth}`delete_endpoint <fastapi_restly.views.RestView.delete_endpoint>` | `(id)` | `fastapi.Response` | `DELETE /{id}`; returns `204` by default. |
| Handler | {meth}`handle_get_many <fastapi_restly.views.RestView.handle_get_many>` | `(list_params, *, scope=None, where=None)` | `ListResult[Model]` | Run `authorize("get_many")`, then `get_many`, forwarding `scope=`. A route names its own scope here (a trash list); `fr.clauses.UNSCOPED` reads past the view scope. `where=` takes an expression or a clause and [narrows inside the scope](#per-read-where); it reaches `get_many` folded into `scope`. Final. |
| Handler | {meth}`handle_get_one <fastapi_restly.views.RestView.handle_get_one>` | `(id, *, scope=None)` | `Model` | Load through `get_one` (scoped, 404), forwarding `scope=`, then `authorize("get_one", obj=...)`. Reusable from a custom read route as "scoped load + 404 + read-auth"; a write action loads with `get_one` and gates its own action. `id` takes a predicate the way `get_one` does, so a [natural-key route](#natural-key-route) is this handler with `Item.slug == slug`. Final. |
| Handler | {meth}`handle_create <fastapi_restly.views.RestView.handle_create>` | `(schema_obj)` | `Model` | Authorize, run `create`, then the commit bracket. Inside `shared_write_action_commit`, return after flush but before commit and the after-hook. Final. |
| Handler | {meth}`handle_update <fastapi_restly.views.RestView.handle_update>` | `(id, schema_obj)` | `Model` | Load, authorize, snapshot, run `update`, then the commit bracket. `id` also takes a predicate, so an update by natural key runs the same bracket. Inside `shared_write_action_commit`, return after flush but before commit and the after-hook. Final. |
| Handler | {meth}`handle_delete <fastapi_restly.views.RestView.handle_delete>` | `(id)` | `None` | Load, authorize, snapshot, run `delete`, then the commit bracket. `id` also takes a predicate. Inside `shared_write_action_commit`, return after flush but before commit and the after-hook. Final. |
| Custom-action bracket | {meth}`write_action <fastapi_restly.views.RestView.write_action>` | `(action, *, obj=..., data=None)` | context manager | Entered as `async with self.write_action("publish", obj=...):`, it runs the full bracket around your inline mutation: authorize and snapshot on enter; `before_action_commit`, commit, and `after_action_commit` on exit. Use it for a custom write *action* that is not a plain create/update/delete; deposit a create's new object on the yielded handle's `.obj`; omit `obj` for that create-shaped form, and pass `obj=None` for a write with no single object. The implementation is shared with the CRUD handlers via the self-free `run_write_action` (in `fastapi_restly.views`). |
| Commit bracket | {meth}`shared_write_action_commit <fastapi_restly.views.RestView.shared_write_action_commit>` | `()` | context manager | Share one commit across write actions on the session. The outermost block commits, then runs their after-hooks. See [Commit several writes together](#shared-write-action-commit). |
| Business method | {meth}`get_many <fastapi_restly.views.RestView.get_many>` | `(list_params, *, scope=None)` | `ListResult[Model]` | Scoped and filtered list via `scope` when given, else the view scope, + `apply_list_params`. Paginated views return one page plus a total count; unpaginated views return every matching row with `total_count=None` and skip `count`. Auth-free. The handlers always forward `scope=`, so an override declares the parameter and passes it on. |
| Business method | {meth}`get_one <fastapi_restly.views.RestView.get_one>` | `(id, *, scope=None)` | `Model` | Load one row through `scope` when given, else the view scope, or raise `fr.exc.NotFound`. Visibility comes from the scope, so a hidden row is a clean 404 for every caller. Auth-free. The handlers always forward `scope=`, so an override declares the parameter and passes it on. `id` is the primary key by default; a SQLAlchemy boolean expression replaces that criterion (`get_one(Item.slug == slug)`), which is also how a composite key is addressed. A criterion narrows inside the scope, and more than one match raises `MultipleResultsFound`. See [Look a row up by another key](#natural-key-route). |
| Business method | {meth}`create <fastapi_restly.views.RestView.create>` | `(schema_obj)` | `Model` | Build a new object and save it. Commit-free: the usual create override point. |
| Business method | {meth}`update <fastapi_restly.views.RestView.update>` | `(obj, schema_obj)` | `Model` | Apply the update payload to `obj` and save it. Commit-free. |
| Business method | {meth}`delete <fastapi_restly.views.RestView.delete>` | `(obj)` | `None` | Remove `obj` and flush. Override (e.g. on a soft-delete mixin) to flip a timestamp instead. |
| Configuration | {attr}`scope <fastapi_restly.views.BaseRestView.scope>` | class attribute | `WhereClause \| None` | The clause every read applies, shared by `get_many`, `count`, and `get_one`. `None` falls back to the model's declared `default_scope`; `fr.clauses.UNSCOPED` reads unscoped. See [Scopes](scopes.md). |
| Function | {func}`fr.resolve_scope(view_or_model) <fastapi_restly.views.resolve_scope>` | `(view_or_model)` | `WhereClause` | The scope a read applies, resolved down the ladder: the view's `scope`, the model's `default_scope`, then `fr.clauses.UNSCOPED`. Takes a view class or instance, or a mapped model class for the model rung alone. Apply it with `fr.apply_clauses` in a route that builds its own query (a count, a nested list) and the route sees the rows `get_one` and `get_many` see. See [Reading the resolved scope](#reading-the-scope). |
| Override point | {meth}`apply_list_params <fastapi_restly.views.RestView.apply_list_params>` | `(query, list_params)` | `sqlalchemy.Select` | Apply URL filter/sort/pagination to `query`. Override for a non-default URL grammar. |
| Override point | {meth}`count <fastapi_restly.views.RestView.count>` | `(query)` | `int` | Total for a paginated list: receives the same params-applied query, strips `ORDER BY`, `LIMIT`, and `OFFSET`, and counts it `DISTINCT` through a subquery, so a query that joins a to-many relationship does not inflate the total. Unpaginated views skip it. Override for estimated counts on huge tables. |
| Override point | {meth}`authorize <fastapi_restly.views.RestView.authorize>` | `(action, obj=None, data=None)` | `None` | Gate a verb. A no-op by default; override to enforce policy and raise `fr.exc.Forbidden` / `fr.exc.NotFound` to reject. Row *visibility* belongs in the scope. |
| Override point | {meth}`before_action_commit <fastapi_restly.views.RestView.before_action_commit>` | `(action, new, old=None)` | `None` | In-transaction side effect (outbox/audit rows), atomic with the write. `old` is the pre-mutation snapshot dict. |
| Override point | {meth}`after_action_commit <fastapi_restly.views.RestView.after_action_commit>` | `(action, new, old=None)` | `None` | Post-commit side effect (email, webhook, cache invalidation). `old` enables dirty detection. An exception here fails the request but cannot undo the write; see [When `after_action_commit` raises](#after-action-commit-raises). |
| Override point | {meth}`to_response <fastapi_restly.views.BaseRestView.to_response>` | `(result, shape=ResponseShape.SINGLE)` | response payload | The single response method, called by the endpoint methods with the `ResponseShape` (`SINGLE` / `LIST` / `EMPTY`), not the write action. Override for envelopes or custom status codes; for a per-verb HTTP contract change, override that verb's endpoint method. |
| Override point | {meth}`snapshot <fastapi_restly.views.BaseRestView.snapshot>` | `(obj)` | `dict[str, Any]` | Frozen capture of an object's already-loaded column values, taken after `authorize` and before the mutation, passed as `old` to the commit hooks. |
| Override point | {meth}`get_relationship_loader_options <fastapi_restly.views.BaseRestView.get_relationship_loader_options>` | `()` | `list[Any]` | Loader options (`selectinload(...)`) for the relationships the response class names, applied on reads (`get_one` / `get_many`) and on the write-response reload in `save_object`. Override to eager-load relationships the response class does not name on both paths; see [Relationship Loading and Async](#relationship-loading). |
| Helper | {meth}`to_single_response <fastapi_restly.views.BaseRestView.to_single_response>` | `(obj)` | instance of `schema_response` | Validate and serialize one ORM object with Restly's alias/reference/write-only handling. Override for custom projections or an intentional `model_construct()` fast path. |
| Helper | {meth}`to_list_response <fastapi_restly.views.BaseRestView.to_list_response>` | `(list_result)` | envelope model instance | Build the list response body: an instance of the pagination's envelope, or of `Envelope` when `pagination` is `None`. The page comes from `list_result.list_params`. To change the shape, set the pagination's `envelope`. A shape no envelope model can express, such as a header, needs `get_many_endpoint` replaced with a matching `response_model`. |
| Domain utility | {meth}`make_new_object <fastapi_restly.views.RestView.make_new_object>` | `(schema_obj)` | `Model` | Build and stage a new object without flushing, resolving references and skipping read-only fields. Final. |
| Domain utility | {meth}`update_object <fastapi_restly.views.RestView.update_object>` | `(obj, schema_obj)` | `Model` | Apply writable fields without flushing, resolving references. Final. |
| Domain utility | {meth}`save_object <fastapi_restly.views.RestView.save_object>` | `(obj)` | `Model` | Flush and refresh a staged object, then eager-load the relationships the response class names (via `get_relationship_loader_options`). Does not commit; `handle_<verb>` owns the commit. Final. |

Annotate the `scope` parameter of a `get_one` or `get_many` override with
`fr.views.ReadScope`: `None` resolves the view's scope, a clause replaces it,
and `fr.clauses.UNSCOPED` reads unscoped.

Internal methods prefixed with `_`, such as `_reject_unknown_query_params`, are implementation details even though they are visible on instances.

See [Views](class_based_views.md#the-view-hierarchy) for the class hierarchy, [Request Lifecycle & Hooks](customize.md) for examples of choosing which method to override, and [Type Annotations](howto_typing.md) for the typed signatures of these methods.

(advanced-object-helpers)=
### The same operations outside a view

The domain utilities wrap the functions in
{mod}`fr.objects <fastapi_restly.objects>`, binding `self.session`,
`self.model`, and `self.schema`. Use the `fr.objects` forms in custom routes
that touch a model other than `self.model`, and in services, workers, or
tests. Each function has a sync and an async variant, such as
{func}`make_new_object <fastapi_restly.objects.make_new_object>` and
{func}`async_make_new_object <fastapi_restly.objects.async_make_new_object>`.
