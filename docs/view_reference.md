# View Reference

This page lists everything you can set, override, or call on a view.
[CRUD Routes](crud_routes.md) explains the default HTTP contract, and
[Request Lifecycle & Hooks](lifecycle.md) explains which method to override.
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
| {class}`fr.AsyncReactAdminView <fastapi_restly.views.AsyncReactAdminView>` | Async CRUD view that follows the `ra-data-simple-rest` HTTP contract used by [react-admin](https://marmelab.com/react-admin/). See [React Admin](react_admin.md). |
| {class}`fr.ReactAdminView <fastapi_restly.views.ReactAdminView>` | Sync variant of `AsyncReactAdminView`. |

(view-class-attributes)=
## Class attributes

Every `View` subclass, CRUD or not, honors these class attributes:

| Attribute | Type | Description |
|---|---|---|
| {attr}`prefix <fastapi_restly.views.View.prefix>` | `ClassVar[str]` | URL prefix for all routes in the view (e.g. `"/users"`). A view without a prefix puts its routes at the root. Each class in the hierarchy adds its own, base first. |
| {attr}`tags <fastapi_restly.views.View.tags>` | `ClassVar[Iterable[str \| Enum] \| None]` | OpenAPI tags. When unset, the tag is the last part of the prefix in title case (`/order-items` gives `Order Items`), or the class name when there is no prefix. Setting this replaces that tag. |
| {attr}`dependencies <fastapi_restly.views.View.dependencies>` | `ClassVar[Sequence[Depends] \| None]` | FastAPI dependencies applied to every route in the view. Each class in the hierarchy adds its own, base first. |
| {attr}`responses <fastapi_restly.views.View.responses>` | `ClassVar[dict[int \| str, dict[str, Any]]]` | OpenAPI response overrides. `View` defaults to `{}`; `BaseRestView` defaults to `{404: {"description": "Not found"}}`. Each class in the hierarchy adds its own, and a subclass's entry for a status code wins. |

`RestView` and `AsyncRestView` add the following:

| Attribute | Type | Description |
|---|---|---|
| {attr}`schema <fastapi_restly.views.BaseRestView.schema>` | `ClassVar[type[pydantic.BaseModel]]` | The view's schema. Restly derives the response, create and update schemas from it. If omitted, auto-generated from `model` as `<Model>Schema`. |
| {attr}`schema_response <fastapi_restly.views.BaseRestView.schema_response>` | `ClassVar[type[pydantic.BaseModel]]` | The class of everything that goes out: the responses, the list items, the fields the list params filter on, and the relationships the view loads. Auto-derived by removing `WriteOnly` fields and named `ModelResponse`. See [Set your own response class](#own-response-class). |
| {attr}`schema_create <fastapi_restly.views.BaseRestView.schema_create>` | `ClassVar[type[pydantic.BaseModel]]` | Schema for `POST` input. Auto-derived by removing `ReadOnly` fields and named `ModelCreate`. |
| {attr}`schema_update <fastapi_restly.views.BaseRestView.schema_update>` | `ClassVar[type[pydantic.BaseModel]]` | Schema for `PATCH` input. Auto-derived by making all writable fields optional and named `ModelUpdate`. |
| {attr}`model <fastapi_restly.views.BaseRestView.model>` | `ClassVar[type[Any]]` | The SQLAlchemy mapped class. A class without a mapper raises at class definition. |
| {attr}`id_type <fastapi_restly.views.BaseRestView.id_type>` | `ClassVar[type \| None]` | Type of the `{id}` path parameter on the default routes. `None` (the default) takes the model's primary key type; a composite key gets `int`. |
| {attr}`exclude_routes <fastapi_restly.views.BaseRestView.exclude_routes>` | `ClassVar[Iterable[str \| ViewRoute]]` | Default routes to leave out: `fr.ViewRoute` values (`GET_MANY`, `GET_ONE`, `CREATE`, `UPDATE`, `DELETE`) or endpoint method names such as `"delete_endpoint"`. See [Exclude CRUD routes](#exclude-crud-routes). |
| {attr}`schema_list_params <fastapi_restly.views.BaseRestView.schema_list_params>` | `ClassVar[type[pydantic.BaseModel]]` | The list params (filter, sort, page) as a Pydantic model, generated from `schema_response` by `fr.query.derive_schema_list_params`. Any route method that declares a `list_params` parameter is annotated with it, so a custom list route takes the same parameters as `GET /`. |
| {attr}`include_filters <fastapi_restly.views.BaseRestView.include_filters>` | `ClassVar[tuple[str, ...]]` | The filter keys to keep, as keys or patterns. `*` matches any characters except `.`, and `**` any characters. `("*",)` by default: the keys of the resource's own fields, and no dotted keys. See [Choose the keys of a list](#choose-list-keys). |
| {attr}`exclude_filters <fastapi_restly.views.BaseRestView.exclude_filters>` | `ClassVar[tuple[str, ...]]` | The filter keys to leave out, after `include_filters`. `()` by default. |
| {attr}`include_sort_keys <fastapi_restly.views.BaseRestView.include_sort_keys>` | `ClassVar[tuple[str, ...]]` | The keys that `sort` accepts, as keys or patterns. `("*",)` by default: the resource's own fields. `()` removes the `sort` parameter. |
| {attr}`exclude_sort_keys <fastapi_restly.views.BaseRestView.exclude_sort_keys>` | `ClassVar[tuple[str, ...]]` | The sort keys to leave out, after `include_sort_keys`. `()` by default. |
| {attr}`sort_query_param <fastapi_restly.views.BaseRestView.sort_query_param>` | `ClassVar[str]` | The name of the sort parameter. `"sort"` by default. See [Sort a list](#sort-a-list). |
| {attr}`pagination <fastapi_restly.views.BaseRestView.pagination>` | `ClassVar[NumberedPagination \| NoPagination \| None]` | How list endpoints paginate, `NumberedPagination()` by default. A `NoPagination` returns every row and runs no count query; `None` is short for `NoPagination()`, a plain `Envelope` (`data` only). Views inherit it, so a project base view sets it once. See [Pagination settings](#pagination-settings). |
| {attr}`unknown_query_params <fastapi_restly.views.BaseRestView.unknown_query_params>` | `ClassVar[Literal["forbid", "ignore"]]` | What a list route does with a query key that it does not read. `"forbid"` (the default) answers `422`. `"ignore"` leaves the key out, as FastAPI does. See [Accept extra query parameters](#extra-query-parameters). |
| `Action` | class | The view's actions: the names that `authorize` and the transaction hooks get. {class}`fr.Action <fastapi_restly.views.Action>` (the five CRUD actions) by default. A view with actions of its own declares a subclass with the same name, and inherits from the `Action` class of each base that has one. The class does not change after Python creates it. `write_action` takes only a declared name. See [Action names](#action-names). |

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
[Responses](responses.md).

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

Each CRUD route on `RestView` / `AsyncRestView` runs an endpoint method
(`<verb>_endpoint`), a final handler (`handle_<verb>`), and a business method
(`create`, `update`, `delete`) or a final load (`get_one`, `get_many`).
[Request Lifecycle & Hooks](#view-request-steps) shows the steps, and which
method to override for each change.

Alongside these are the declared read [scope](scopes.md) (the `scope` class
attribute) and cross-cutting **override points** (`where_id`, `not_found`,
`get_loader_options`, `apply_list_params`, `apply_filters`, `count`,
`authorize`, `before_action_commit` / `after_action_commit`, `to_response`,
`snapshot`). The handlers, the loads and `select` are final: you call them
rather than override them, and a view class that defines one fails at class
definition.

On `AsyncRestView`, the handlers, the loads, the business methods,
`apply_list_params`, `apply_filters`, `count`, `authorize` and the
transaction hooks are `async def`. The response methods, `snapshot`,
`select`, `where_id` and `get_loader_options` are plain `def`. `not_found`
may be either. The two brackets are entered with `async with`. Argument
names are identical between variants.

| Kind | Method | Signature | Return | Purpose |
|---|---|---|---|---|
| Endpoint method | {meth}`get_many_endpoint <fastapi_restly.views.RestView.get_many_endpoint>` | `(list_params)` | the list response, such as `UserListResponse`: the pagination's envelope (`PaginatedEnvelope` by default, or `Envelope`) filled with the response class | `GET /`; validates query parameters and serializes the list result via `to_response`. |
| Endpoint method | {meth}`get_one_endpoint <fastapi_restly.views.RestView.get_one_endpoint>` | `(id)` | response schema | `GET /{id}`; serializes one retrieved object. |
| Endpoint method | {meth}`create_endpoint <fastapi_restly.views.RestView.create_endpoint>` | `(data)` | response schema | `POST /`; serializes the created object. |
| Endpoint method | {meth}`update_endpoint <fastapi_restly.views.RestView.update_endpoint>` | `(id, data)` | response schema | `PATCH /{id}`; serializes the updated object. |
| Endpoint method | {meth}`delete_endpoint <fastapi_restly.views.RestView.delete_endpoint>` | `(id)` | `fastapi.Response` | `DELETE /{id}`; returns `204` by default. |
| Handler | {meth}`handle_get_many <fastapi_restly.views.RestView.handle_get_many>` | `(list_params, *, scope=None, where=None)` | `ListResult[Model]` | Run `authorize("get_many")`, then `get_many`, forwarding `scope=`. A route names its own scope here (a trash list); `fr.clauses.UNSCOPED` reads past the view scope. `where=` takes an expression or a clause and [narrows inside the scope](#per-read-where); the handler passes it on to `get_many`. Final. |
| Handler | {meth}`handle_get_one <fastapi_restly.views.RestView.handle_get_one>` | `(id, *, scope=None)` | `Model` | Load through `get_one` (scoped, 404), forwarding `scope=`, then `authorize("get_one", obj=...)`. Reusable from a custom read route as "scoped load + 404 + read-auth"; a write action loads with `get_one` and gates its own action. `id` takes a predicate the way `get_one` does, so a [natural-key route](#natural-key-route) is this handler with `Item.slug == slug`. Final. |
| Handler | {meth}`handle_create <fastapi_restly.views.RestView.handle_create>` | `(data)` | `Model` | Authorize, check the references, run `create`, then save what it returns and run the commit bracket. Inside `shared_write_action_commit`, return after the save but before the commit and the after-hook. Final. |
| Handler | {meth}`handle_update <fastapi_restly.views.RestView.handle_update>` | `(id, data)` | `Model` | Load, authorize, check the references, snapshot, run `update`, then save what it returns and run the commit bracket. `id` also takes a predicate, so an update by natural key runs the same bracket. Inside `shared_write_action_commit`, return after the save but before the commit and the after-hook. Final. |
| Handler | {meth}`handle_delete <fastapi_restly.views.RestView.handle_delete>` | `(id)` | `None` | Load, authorize, snapshot, run `delete`, then flush and run the commit bracket. `id` also takes a predicate. Inside `shared_write_action_commit`, return after the flush but before the commit and the after-hook. Final. |
| Custom-action bracket | {meth}`write_action <fastapi_restly.views.RestView.write_action>` | `(action, *, obj, data=None)` | context manager | Entered as `async with self.write_action(self.Action.PUBLISH, obj=post):`, it runs the full bracket around your change: authorize, the reference check of `data` and the snapshot on enter; the save, `before_action_commit`, the commit and `after_action_commit` on exit. `obj` is required: a row from the database, a new object that the action creates (Restly adds it), or `None` for a write with no single row. Use it for a custom write *action* that is not a plain create, update or delete. `action` is a name that the view's `Action` declares; another name raises `ValueError`. The block gets nothing from `as`. |
| Commit bracket | {meth}`shared_write_action_commit <fastapi_restly.views.RestView.shared_write_action_commit>` | `()` | context manager | Share one commit across write actions on the session. The outermost block commits, then runs their after-hooks. See [Commit several writes together](#shared-write-action-commit). |
| Load | {meth}`get_many <fastapi_restly.views.RestView.get_many>` | `(list_params, *, scope=None, where=None)` | `ListResult[Model]` | `select()`, then `apply_list_params`. Paginated views return one page plus a total count; unpaginated views return every matching row with `total_count=None` and skip `count`. Auth-free. Final. |
| Load | {meth}`get_one <fastapi_restly.views.RestView.get_one>` | `(id, *, scope=None)` | `Model` | `select()` with `where_id(id)`, or raise the exception of `not_found(id)`. Visibility comes from the scope, so a hidden row is a clean 404 for every caller. Auth-free. A SQLAlchemy boolean expression can take the place of the id (`get_one(Item.slug == slug)`). It narrows inside the scope, and more than one match raises `MultipleResultsFound`. See [Look a row up by another key](#natural-key-route). Final. |
| Load | {meth}`select <fastapi_restly.views.BaseRestView.select>` | `(*, scope=None)` | `sqlalchemy.Select` | `select(model)` with the scope and `get_loader_options()`. Both loads start here, and so does a custom route that builds its own read. Final. |
| Override point | {meth}`where_id <fastapi_restly.views.BaseRestView.where_id>` | `(id)` | `ColumnElement[bool]` | The WHERE for an id from the path. The primary key by default. Override to find the row by another key, such as a slug; a model with a composite primary key needs an override for its item routes. It must compare columns of the view's model. Plain `def`, also on an async view. |
| Override point | {meth}`not_found <fastapi_restly.views.BaseRestView.not_found>` | `(id)` | `Exception` | The exception that `get_one` raises for an id that names no row: `fr.exc.NotFound` by default. `id` is `None` when a route loaded by an expression. May be `async def` on an async view. |
| Business method | {meth}`create <fastapi_restly.views.RestView.create>` | `(data)` | `Model` | Build a new object and return it, without a flush: the handler saves it. Commit-free: the usual create override point. |
| Business method | {meth}`update <fastapi_restly.views.RestView.update>` | `(obj, data)` | `Model` | Apply the update payload to `obj` and return it, without a flush: the handler saves it. Commit-free. |
| Business method | {meth}`delete <fastapi_restly.views.RestView.delete>` | `(obj)` | `None` | Mark `obj` for deletion, without a flush. Override (e.g. on a soft-delete mixin) to flip a timestamp instead. |
| Configuration | {attr}`scope <fastapi_restly.views.BaseRestView.scope>` | class attribute | `WhereClause \| None` | The clause every read applies, shared by `get_many`, `count`, and `get_one`. `None` falls back to the model's declared `default_scope`; `fr.clauses.UNSCOPED` reads unscoped. See [Scopes](scopes.md). |
| Function | {func}`fr.resolve_scope(view_or_model) <fastapi_restly.views.resolve_scope>` | `(view_or_model)` | `WhereClause` | The scope a read applies, resolved down the ladder: the view's `scope`, the model's `default_scope`, then `fr.clauses.UNSCOPED`. Takes a view class or instance, or a mapped model class for the model rung alone. Apply it with `fr.apply_clauses` in a route that builds a statement that `self.select()` cannot start, such as a select of some columns, and the route sees the rows `get_one` and `get_many` see. See [Use the scope in your own query](#reading-the-scope). |
| Override point | {meth}`apply_list_params <fastapi_restly.views.RestView.apply_list_params>` | `(query, list_params)` | `sqlalchemy.Select` | Apply the list params to `query`: `apply_filters`, then the sort, then the page. Override for list params of your own. |
| Override point | {meth}`apply_filters <fastapi_restly.views.RestView.apply_filters>` | `(stmt, list_params)` | `sqlalchemy.Select` | Apply the generated filters and the view's own filters to `stmt`, without the sort and the page. Call it from a route that has its own order, such as a count with `GROUP BY`. See [Use the filters in a route of your own](#filters-in-own-route). |
| Decorator | {func}`fr.list_filter(key) <fastapi_restly.views.list_filter>` | `(key)` | the method | Make a view method the filter for the query key `key`. The type of its value becomes the type of the key. It returns a condition, a clause or `None`. See [Add a filter of your own](#own-list-filter). |
| Override point | {meth}`count <fastapi_restly.views.RestView.count>` | `(query)` | `int` | Total for a paginated list. It gets the same query with the list params applied, leaves out `ORDER BY`, `LIMIT` and `OFFSET`, and counts the distinct primary keys of the model that the query selects. So a query that joins a to-many relationship does not make the total too high, and a `json` column works on PostgreSQL. A query of columns, not of a model, counts its distinct rows. Unpaginated views skip it. Override for estimated counts on huge tables. |
| Override point | {meth}`authorize <fastapi_restly.views.RestView.authorize>` | `(action, obj=None, data=None)` | `None` | Gate a verb. A no-op by default; override to enforce policy and raise `fr.exc.Forbidden` / `fr.exc.NotFound` to reject. Row *visibility* belongs in the scope. |
| Override point | {meth}`before_action_commit <fastapi_restly.views.RestView.before_action_commit>` | `(action, new, old=None)` | `None` | In-transaction side effect (outbox/audit rows), atomic with the write. `old` is the pre-mutation snapshot dict. |
| Override point | {meth}`after_action_commit <fastapi_restly.views.RestView.after_action_commit>` | `(action, new, old=None)` | `None` | Post-commit side effect (email, webhook, cache invalidation). `old` enables dirty detection. An exception here fails the request but cannot undo the write; see [When `after_action_commit` raises](#after-action-commit-raises). |
| Override point | {meth}`to_response <fastapi_restly.views.BaseRestView.to_response>` | `(result, shape=ResponseShape.SINGLE)` | response payload | The single response method, called by the endpoint methods with the `ResponseShape` (`SINGLE` / `LIST` / `EMPTY`), not the write action. Override for envelopes or custom status codes; for a per-verb HTTP contract change, override that verb's endpoint method. |
| Override point | {meth}`snapshot <fastapi_restly.views.BaseRestView.snapshot>` | `(obj)` | `dict[str, Any]` | Frozen capture of an object's already-loaded column values, taken after `authorize` and before the mutation, passed as `old` to the commit hooks. |
| Override point | {meth}`get_loader_options <fastapi_restly.views.BaseRestView.get_loader_options>` | `()` | `list[Any]` | The loader options of every read: by default a `selectinload(...)` for each relationship the response class names. `select()` applies them, and so does the reload after the save of a write. Override to load a relationship the response class does not name, or a `query_expression` with `with_expression(...)`. See [Return related objects in a response](#relationship-loading). |
| Helper | {meth}`to_single_response <fastapi_restly.views.BaseRestView.to_single_response>` | `(obj)` | instance of `schema_response` | Validate and serialize one ORM object with Restly's alias/reference/write-only handling. Override for custom projections or an intentional `model_construct()` fast path. |
| Helper | {meth}`to_list_response <fastapi_restly.views.BaseRestView.to_list_response>` | `(list_result)` | envelope model instance | Build the list response body: an instance of the pagination's envelope, or of `Envelope` when `pagination` is `None`. The page comes from `list_result.list_params`. To change the shape, set the pagination's `envelope`. A shape no envelope model can express, such as a header, needs `get_many_endpoint` replaced with a matching `response_model`. |

The `scope` argument of the loads and of `select` has the type
`fr.views.ReadScope`: `None` resolves the view's scope, a clause replaces it,
and `fr.clauses.UNSCOPED` reads unscoped.

Internal methods prefixed with `_`, such as `_reject_unknown_query_params`, are implementation details even though they are visible on instances.

See [Views](#the-view-hierarchy) for the class hierarchy, [Request Lifecycle & Hooks](lifecycle.md) for examples of choosing which method to override, and [Type Annotations](typing.md) for the typed signatures of these methods.

(advanced-object-helpers)=
### The same operations outside a view

The default `create` and `update` call the functions in
{mod}`fr.objects <fastapi_restly.objects>` with `self.session` and
`self.model`. Use the `fr.objects` forms in custom routes that touch a model
other than `self.model`, and in services, workers, or tests. Each function has a sync and an async variant, such as
{func}`make_new_object <fastapi_restly.objects.make_new_object>` and
{func}`async_make_new_object <fastapi_restly.objects.async_make_new_object>`.
