# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

This release adds `SchemaType`, a type that depends on the view, and a setting
for the list response. The settings and markers for a view's schemas now have
names that end with what they are. Each filter key now has the type of its field,
and a field has only the keys that work for it. To upgrade, work through these steps. A
missed step fails with an error. A step marked "Silent" gives no error when you
miss it.

1. Rename these settings of a view: `schema_response` to `response_schema`,
   `schema_create` to `create_schema`, `schema_update` to `update_schema`, and
   `schema_list_params` to `list_params_schema`.
2. Rename these markers of `fastapi_restly.views`: `ViewID` to `IDType`,
   `ViewResponse` to `ResponseSchema`, `ViewListResponse` to
   `ListResponseSchema`, `ViewCreate` to `CreateSchema`, and `ViewUpdate` to
   `UpdateSchema`.
3. Rename these functions: `fr.schemas.derive_schema_response` to
   `derive_response_schema`, `fr.schemas.derive_schema_list_response` to
   `derive_list_response_schema`, and `fr.query.derive_schema_list_params` to
   `derive_list_params_schema`. A parameter `schema_response` of these functions,
   of `fr.query.apply_list_params`, of `fr.query.FilterKey` or of
   `fr.query.SortKey` is now `response_schema`.
4. Remove `to_response` and `ResponseShape`. In a custom route, declare
   `-> ResponseSchema` and return the row, or call `to_single_response`. For a
   list, declare `-> ListResponseSchema` and return the `ListResult`, or call
   `to_list_response`. For `ResponseShape.EMPTY`, return
   `fastapi.Response(status_code=204)`. An override of `to_response` for one
   object becomes an override of `to_single_response`. An override that wraps
   each response becomes endpoint methods that you replace on a base view; see
   "Change the shape of every route" in the guide.
5. Silent: a default endpoint method that you call yourself, such as
   `await super().get_one_endpoint(id)`, returns the row or the `ListResult`.
   Restly builds the response after the route returns. So code that reads the
   result gets a row, not the response. Call `to_single_response` or
   `to_list_response` if you need the response, also in a test.
6. Silent: a route that declares no response, or a type without fields such as
   `dict[str, Any]`, and returns a row of the view's model, a list of rows, or a
   dict with these as values, sends the fields of the response schema. A row further
   down still goes out as FastAPI sends it. Before, FastAPI sent each column
   of the row, also a `WriteOnly` column. To send more fields, declare a class. In
   async code, a relationship that the response schema names and your own `select`
   did not load now raises `MissingGreenlet`, so load it.
7. Silent: on a react-admin view, `GET /` now has a response model, the list type.
   An override of `to_list_response` that returns data, and not a `Response`, loses
   the fields that this type does not have. Set `list_response_schema` to the type
   that you send.
8. Silent: a custom route without a response model, which calls an override of
   `to_single_response` that returns another model, sends all fields of that
   model. Before, `to_response` copied the value into the response schema. Declare
   `-> ResponseSchema` on the route.
9. Rename `fr.views.PaginatedEnvelope` to `fr.views.NumberedEnvelope`. A pagination
   style now has an envelope with its own name. The JSON does not change. In
   OpenAPI, a class that you fill yourself, such as `NumberedEnvelope[Summary]`,
   gets a new name: `NumberedEnvelope_Summary_`. A client that you generate from
   the spec can see this new name.
10. A view with one of these now fails when it registers. Before, each one
    started without an error:
    - A route that reads a key of its list params itself, such as `limit` next to
      `list_params` on a view with `fr.OffsetPagination`, or `name` when `name` is
      a filter. One value filled both, and OpenAPI showed the key twice. Read the
      value from `list_params`, or give the parameter of the route another name.
    - List params of your own with more than one field for a query key.
    - A subclass of `fr.query.PagedListParams` or `fr.query.OffsetListParams` that
      declares a page field again. The view ignored its default and its bounds.
      Set them on the pagination.
    - A hand-written `page_size` or `limit` field with the default `None`. A
      request without it returned every row, and the response reported the default
      size. Give the field a number as its default.
    - A SQLModel table class, or a subclass of one, as `schema`, `create_schema` or
      `update_schema`. With a table class as `schema`, each create gave `500`. As
      `create_schema` or `update_schema`, the body was not validated, so a wrong type
      was saved. Leave the setting out, or set a class without `table=True`. The
      functions `fr.schemas.derive_response_schema`, `derive_create_schema` and
      `derive_update_schema` now also fail when you call them with such a class.
    - A SQLModel table class in a field that a client can write, on a `schema`,
      `create_schema` or `update_schema`: `team: Team`, `teams: list[Team]`, or a data
      model that holds one. A client could write a row through that field, with any id
      and any column. To let a client choose a row, send its id with `fr.MustExist`,
      `fr.IDRef` or `fr.IDSchema`. To show the row in responses, mark the field
      `fr.ReadOnly`.
    - A subclass of a SQLModel table class as `response_schema` or as the items of
      `list_response_schema`, or anywhere inside a schema of the view, such as in a
      read-only field. OpenAPI showed no required fields. Use the table class itself,
      or a class that does not inherit from it.
11. Silent: a custom route keeps the name of its method, without the name of the
    view. On `ItemView`, a method `stats` at `/items/stats` had the route name
    `itemview_stats`, the summary `Itemview Stats` and the operation ID
    `itemview_stats_items_stats_get`. Now these are `stats`, `Stats` and
    `stats_items_stats_get`. The same happens to a method named after a CRUD route
    on a plain `View`. A client that you generate from the spec gets the new IDs.
    To keep an ID, pass `operation_id` to the decorator.
12. Silent: a CRUD route with a `name` of your own now gets its summary and
    operation ID from FastAPI, as any route with a name does. So `name="search"`
    on the list route at `/items` gives the summary `Search` and the ID
    `search_items_get`, in place of `List` and `items_search`. To keep them, pass
    `summary` and `operation_id` to the decorator.
13. Silent: a CRUD view documents `404` only on a route that can answer it. That
    is a route with a parameter in its path, or with a reference to a row in its
    request body. So `GET /`, a simple `POST /` and a custom route without a path
    parameter no longer list it. A custom route that answers 404 for another reason
    needs `responses={404: {"description": "Not found"}}` on its decorator.
    `BaseRestView.responses` is now `{}`, so code that reads its 404 entry must
    write this entry itself.
14. Silent: the default `delete_endpoint` returns `None`, not a `Response`. So
    code that reads `.status_code` from the result of
    `await super().delete_endpoint(id)` fails when the route runs. The route now
    sends the status code of its decorator: `@fr.delete("/{id}", status_code=200)` that calls
    `super()` answers `200` with the body `null`, where it answered `204` before.
15. A wrapper of a decorator that reads `self` from `args[0]` now gets an
    `IndexError` on each request, on a method that a view inherits and on a route
    that takes `list_params`. Before, only a method that the view defines without
    `list_params` had this. Name the parameter instead:
    `def wrapper(self, *args, **kwargs)`.
16. Silent: on those same methods, `self` is now a keyword in a wrapper. So a wrapper
    that tests `if args` now skips that code. If that code is a permission check, the
    check no longer runs. And a cache key made from `kwargs` now holds `self`, so the
    cache gets few hits or none. Build the key from what the response depends on.
17. Silent: OpenAPI no longer has `x-resource-ref`. A client that reads it must
    stop. To keep it on one field, set it on that field:
    `pydantic.Field(json_schema_extra={"x-resource-ref": "authors"})`.
18. A CRUD view with two paths that differ only in the names of their parameters,
    such as `/users/{id}` and a route of your own at `/users/{user_id}`, now raises
    `RestlyConfigurationError` when it registers. OpenAPI does not allow such
    paths, so the spec was wrong. Use the same name in both paths. A custom route
    with `id: IDType` and a path parameter that nothing reads, such as `/by/{name}`,
    now raises too. `id` read the query, and OpenAPI did not show `name`.
19. Silent: only `__in` and the new `__not_in` split a value at commas. On a text
    field, `?status=a,b` now looks for the text `a,b`, where it meant "a or b", and
    `?status__ne=a,b` now hides almost nothing, where it hid both values. Neither
    gives an error, so search your clients for such URLs. Write `?status__in=a,b`
    and `?status__not_in=a,b`. On another type, such as `?id=1,2`, the request now
    gets `422`.
20. Silent: `__icontains` takes one piece of text. So
    `?name__icontains=john%20doe` now looks for `john doe`, where it looked for each
    word. For every word, write a filter of your own; see "Search for text" in the
    guide.
21. A key that takes one value now gets `422` when it comes twice, also for a text
    search, `sort`, the page parameters, a key of your own and list params of your
    own. Before, a repeated `__icontains` added a condition, and any other key took
    the last value. Change a client that sends `?name=a&name=b` to `?name__in=a,b`.
    `__in` and `__not_in` read each value of a repeated key. Outside a view,
    `fr.query.apply_list_params` still takes the last value of a `QueryParams`.
22. A filter value that fails now gets `422` in the form of FastAPI, not `400`, and
    `loc` names the key. Change a client or a test that expects `400` or reads the
    text of its `detail`. A handler for `RestlyHTTPError` no longer gets these errors;
    a handler for `RequestValidationError` does. A key inside the `filter` of a
    react-admin view, and `FilterKey.parse()`, keep the `400`.
23. Silent: a filter value no longer has the bounds of its field, such as `gt` or
    `min_length`. `?price=-1` on a field with `gt=0` is now `200` with no rows, where
    it was `400`. OpenAPI no longer shows bounds on these keys. A number in a filter
    must be between `-1e50` and `1e50`, so a huge value gets `422`. If a bound kept a
    value away from a column type that Restly does not check, that value now reaches
    the database, which can answer with a `500`. Such types are `Numeric`, `Float`,
    `Uuid` and the types of a dialect. Write the limit in a field validator for
    this; a validator still runs.
24. Silent: the fields of generated list params now hold the type of their field, not
    text. So `list_params.price__gte` is a `Decimal`, `list_params.price__in` is a
    list, and a reference gives its id. Change code that reads them as text.
25. Silent: `__ne` now keeps the rows where the field is NULL, as `__not_in` does.
    Before, SQL left them out, so `?status__ne=archived` also hid each row without a
    status. To leave them out too, add `&status__isnull=false`.
26. Silent: a dotted filter key now uses a `LEFT OUTER JOIN`, as a sort key does. So
    `?vendor.id__isnull=true` finds the rows without a vendor, where it found no
    rows, and `?vendor.name__ne=acme` keeps them. A key that looks for a value, such
    as `?vendor.name=acme`, gives the same rows as before.
27. `__contains` is gone, so `?name__contains=a` now gets `422`. Use `__icontains`.
    Its case counted on one database and not on another. For a search where the case
    counts, write a filter of your own; see "Search for text" in the guide. In list
    params of your own, a field named `name__contains` now gets `400`.
28. `__isnull` now exists only on a column that can be NULL, and on each dotted key.
    So `?id__isnull=false` now gets `422`. On such a column, it gave the same answer
    for each row.
29. `__gte`, `__lte`, `__gt` and `__lt` no longer exist on a text column, so
    `?name__gte=a` now gets `422`. The database sorts such values as text, so `"9"`
    comes after `"10"`. A field of type `date` on a text column keeps them. For
    another field, write a filter of your own.
30. `__icontains` now follows the column, not the type of the field. A `str` field on
    a column that does not hold text loses it, so such a key now gets `422`. A field
    of another type on a text column, such as `HttpUrl`, gets it. An enum, a `Literal`
    and a `bool` do not.
31. Silent: in `exclude_filters`, a name without a suffix now leaves out the whole
    field. So `exclude_filters = ("name",)` also removes `name__in`, where it removed
    only `?name=`. To keep the other keys, list the keys in `include_filters`, such
    as `("*__*", "id", "email")`, and remove the name from `exclude_filters`.
32. A field with the name of a page or sort parameter now raises
    `RestlyConfigurationError`, not `ValueError`. Change an `except ValueError` around
    `derive_list_params_schema`.
33. A key now has the name that a response shows. So a field with a
    `serialization_alias` has its filter and sort keys under that name, and a client
    that sends the alias or the Python name gets `422`. Change the client.
34. Generated list params now forbid a key that they do not have. In a route outside
    a view, with `Annotated[ListParams, Query()]`, an unknown key now gets `422`. A
    view still follows `unknown_query_params`.
35. The key settings now take the Python names of the fields: `include_filters`,
    `exclude_filters`, `include_sort_keys`, `exclude_sort_keys` and `default_sort`,
    on a view and in `derive_list_params_schema`. Before, an entry was the key in
    the URL, so it used the alias of a field with one. Such an entry now fails when
    the view registers, also when the view inherits it, and the message gives the
    Python name. Write `created_at` for a field with the alias `createdAt`, also in
    each part of a dotted key. The URL does not change, and neither does the key of
    `fr.list_filter`.
36. A filter value that a validator of its field turns into `None`, such as empty
    text, now gets `422`. Before, `?note=` found the rows where `note` is NULL. A
    client that wants these rows sends `?note__isnull=true`. A list of values for
    `__in` or `__not_in` cannot hold `None` either, so `?note__not_in=x,` gets
    `422` too. `FilterKey.parse()` refuses such a value with `400`. A `None` that
    your code puts in the list params is still no filter.

### Added

- `atomic_write_actions`: a setting of a view, `True` by default. With `False`, code
  in a write action may call `session.commit()`, for a library or a function that
  must keep its commit. The action then has no savepoint, and all else stays. So
  the action is no longer one transaction: what is committed stays saved when a
  later step raises. See "Code that must commit in a write action" in the guide.
- `__not_in`, on each field that has `__in`: rows with none of these values.
- `fr.modify_route`: change the options of a route that a view inherits, and keep
  the rest. Use it under the name of the route method, as in
  `create_endpoint = fr.modify_route(status_code=200, name="create_tag")`. The route
  needs no method of its own for a name, a status code, a path, dependencies or the
  documented responses. See "Change one option of a route" in the guide.
- A status code set to `None` in `responses`, on a view or on a route, is left
  out of the spec. So `responses = {404: None}` removes a 404 that a route of a
  CRUD view documents by itself.
- `fr.configure(warn_on_misuse=True)` warns about a method that replaces an
  inherited route and has no route decorator, so that the route is gone.
- `fr.OffsetPagination`: a list that pages with `limit` and `offset`, next to
  `fr.NumberedPagination`. It takes `default_limit`, `max_limit`, `max_offset`,
  `limit_query_param`, `offset_query_param` and `envelope`, and its default
  envelope is `fr.views.OffsetEnvelope`. `offset_query_param="skip"` serves a
  client that sends `skip`. Generated list params, `@fr.list_filter` and the list
  settings work with it; see "Page with limit and offset" in the guide.
- `fr.query.OffsetListParams`: the base of list params of your own with `limit`
  and `offset`, next to `fr.query.PagedListParams`.
- `fr.Pagination`: the base class of the pagination settings. Use it to annotate
  the setting on a base view, as in `pagination: ClassVar[fr.Pagination | None]`.
- `fastapi_restly.views.SchemaType(fn)`: a type that depends on the view. The
  function gets the view class and returns the type. It goes on a setting that
  takes a schema class, as a decorator on such a setting, or in the annotation of
  a route. OpenAPI shows the type of each view; see "Make dynamic schemas that depend on the view"
  in the guide.
- `list_response_schema`, a setting for the class of the list response. Restly
  fills it as it fills an envelope. So a view can send smaller items, or other
  keys, without a replaced endpoint. A list type, such as `list[UserSummary]`,
  sends a JSON array. On a react-admin view the setting is such a list.
- `fr.schemas.derive_create_schema` and `fr.schemas.derive_update_schema`, which
  build the classes of a view from a schema and a model.
- A marker in the `model` of `responses=`, on a route or on a view, gets the type
  of the view.
- You can read a view's schemas before the view registers, as in
  `UserView.response_schema`.

### Changed

- Each filter key in OpenAPI has the type of its field, such as `integer` or a date,
  not text. Each description is written once, not on both the parameter and its
  schema.
- A field whose public name has `.` or `__` gets no filter and no sort key, and its
  view starts. Before, this raised `ValueError`.
- Restly builds a view's schemas when something first reads them, once for each
  view. Registration no longer writes them on the view class.
- Registration raises `RestlyConfigurationError` for a schema setting that is not
  a Pydantic class or a `SchemaType`, and for a function that gives another type.
- Registration raises `RestlyConfigurationError` for a class in a route with a
  marker in a plain field: of a Pydantic class, a dataclass, a `TypedDict` or a
  `NamedTuple`. The field had no type in OpenAPI.
- Registration raises `RestlyConfigurationError` for a marker in a setting, as in
  `list_response_schema = list[ResponseSchema]`, for a function that returns
  `None`, and for `list_response_schema = NumberedEnvelope` without a type for
  the items. OpenAPI showed items without a type.
- Registration raises `RestlyConfigurationError` for an own list response schema
  that `GET /` cannot fill: items that are not a Pydantic class, an item field that
  the response schema does not have, `list[X] | None`, or a class without a field
  named `data`. Before, the first row gave a `500`, or the list was always empty.
  The check on a field named `data` also holds for the `envelope` of a pagination.
  A `RootModel` and a class with a `mode="before"` validator read the page values
  themselves, so they need no such field.
- Registration raises `RestlyConfigurationError` for a marker in a field of a class
  in `responses=` of a view, and for a type alias that names itself and holds a
  marker. OpenAPI showed no type for these.
- Two views with the same schema and model share one create schema and one update
  schema.
- A subclass of a view with a new `schema` gets the function of a base view back,
  if a base view has one, and otherwise Restly's default. A function on the
  subclass counts as a new `schema`.
- A subclass of a derived class can declare a field of `omit` again.
- The check for two classes with the same name in OpenAPI now also runs on an app
  that has only plain `fr.View` classes. Before, it started with the first CRUD view.
- On SQLite, Restly no longer sends a `BEGIN` of its own before the savepoint of a
  write action. The driver begins the transaction, as with a plain
  `session.begin_nested()`. So with the default settings of the driver, the end of
  the savepoint can already commit, just before the commit. A write with
  `sqlite+aiosqlite` and `connect_args={"autocommit": True}` is saved again.
  See "Transactions on SQLite" in the guide.

### Removed

- The `x-resource-ref` extension in OpenAPI. No part of Restly read it, and it could
  name the wrong view when several views served one model.

### Fixed

- `before_action_commit` no longer runs after a `session.rollback()` or a
  `session.close()` in the write action. Before, a hook with an outside effect ran
  for a write that was not saved.
- A text column takes text. A field of another type over `Mapped[str]`, such as
  `HttpUrl`, `IPv4Address`, `Path`, `UUID`, a plain `Enum`, `date`, `time`,
  `datetime`, `timedelta`, `Decimal`, `int` or `bool`, now stores the text that a
  response shows, and a filter finds its row with that text. Before, these gave `500`
  on a create, in a filter, or both, and which ones depended on the database. A
  `datetime` in a filter on SQLite found no rows. A URL that is too long for
  `String(n)` is now `422`.
- The error for list params of your own without page fields now fits a list with
  its own `limit` and `offset`. Its advice was `PagedListParams`, which gives page
  numbers, or `pagination = None`. With `pagination = None`, an envelope with a
  `total_count` field gets the number of rows in the response, without an error. The
  error now names `fr.OffsetPagination`, and the guide warns about the count.
- A route can read a key of its own next to list params with `extra="forbid"`.
  Before, OpenAPI showed the key, and a request with it got `422`.
- A page field with only a `validation_alias` works. Before, a request with that
  key got `400`.
- List params of your own that code creates work on a view that renames its page
  parameters, in `handle_get_many`, in `ListResult` and in
  `fr.query.apply_list_params`. Before, they got `400`, a filter on the renamed key
  or the wrong page in the response.
- OpenAPI shows the `page_size` of generated list params as an integer. Before, it
  also allowed `null`, which a query cannot send.
- Creating a pagination checks the envelope with the default page size, not with
  a page size of 1. So an envelope that rejects the default size, such as one with
  `page_size: int = Field(le=10)`, fails when you create the settings, not on a
  request.
- A marker inside a generic class, as in `Data[ResponseSchema]`, is filled. Before,
  the marker stayed, and OpenAPI showed a field without a type.
- A marker in a type alias, as in `type Resp = ResponseSchema`, is filled. Before,
  OpenAPI showed an empty class, and a row went out with all its columns.
- The list route of a react-admin view shows an array of the response schema in
  OpenAPI. Before, it showed an empty schema. A custom route with the list marker
  no longer shows an envelope while it sends an array.
- A subclass of a derived class keeps out the fields that the class dropped.
  Before, a subclass of the update schema could write a `ReadOnly` field, and a
  subclass of the response schema required a `WriteOnly` field and failed on each
  response.
- A SQLModel table class as `schema`, `create_schema` or `update_schema` of a view
  now fails when the view registers, and the message says what to use instead.
  `derive_response_schema`, `derive_create_schema` and `derive_update_schema` fail when
  you call them with one. Before, with a table class as `schema`, each create gave
  `500` and OpenAPI showed no required fields. As `create_schema` or `update_schema`,
  a wrong type was saved. A subclass of a table class also fails as `response_schema`,
  as the items of a list response, or inside a schema of the view. OpenAPI showed it
  with no required fields.
- A SQLModel table class in a field that a client can write now fails when the view
  registers. Before, `POST {"team": {"id": 777, "is_admin": true}}` on a field
  `team: Team` wrote that row: a client could set any id and any column, with values
  of any type.
- `/openapi.json` no longer gives `500` on an app with a view and a route that uses
  a `pydantic.v1` class, on FastAPI 0.119 to 0.127.
- A decorator that reads the request from `args` or `kwargs`, such as
  `@limiter.limit` from slowapi, works on a method that a view inherits, and on a
  route that takes `list_params`. Before, each request gave `500`. These methods now
  get the same call as a method that the view defines and a FastAPI function: each
  argument by keyword, also `self`. slowapi names a limit after the module and the
  name of the function, so two views in one module that limit a method with the same
  name share their limits. See "Add a decorator of your own" in the guide.
- `fr.configure(warn_on_misuse=True)` no longer warns about `session.commit()` on a
  plain `fr.View`. A route of a plain view that writes must commit. The warning told
  it to use `handle_<verb>()` or `write_action`, and a plain view has neither.
- A view with an `id_type` such as `Annotated[int, {"unit": "row"}]`, with metadata
  that cannot be hashed, no longer raises a `TypeError` when it registers.
- `id: "IDType"` with quotes, under `from __future__ import annotations`, now works
  as `id: IDType`. Before, the spec did not give the type, and the method got a
  string.

## [0.13.0] - 2026-11-01

This release adds `fr.models.PydanticJSON`, a JSON column that holds a
Pydantic type, and runs each write action in a savepoint. To upgrade, work
through these steps. A missed step fails with an error. A step marked
"Silent" gives no error when you miss it.

1. In `fr.configure()`, rename `session_generator=` to
   `async_session_generator=`, and `sync_session_generator=` to
   `session_generator=`.
2. Silent: SQLAlchemy now calls the `before_commit` and `after_commit`
   listeners of a session twice for a write action. The first call is for
   the savepoint. To run only at the commit, return early when
   `session.in_nested_transaction()` is true.
3. Move each `ReadOnly` field out of a document in a JSON column, into a
   column of its own.
4. Silent: a response now shows a NaN or an infinity as the text `"NaN"`,
   `"Infinity"` or `"-Infinity"`, not as `null`. A JSON column that Restly
   writes stores the same text. Check that your clients, and your own code
   that reads such a column, accept the text. To keep `null` in a response,
   set `ser_json_inf_nan="null"` in your schema.

### Added

- `fr.models.PydanticJSON(T)` and `fr.models.PydanticJSONB(T)`, JSON columns
  that hold a Pydantic type, such as `Address` or `dict[str, Address]`. On
  `fr.ModelDefaults`, `Mapped[Address]` alone gives such a column.
- `derive_schema` accepts a Pydantic model as the type of a column, as in
  `Mapped[Address] = mapped_column(JSON)`.
- The `env.py` of `restly new` writes these columns into a migration as
  `sa.JSON()` or `postgresql.JSONB()`. For an older `env.py`, copy
  `render_item` from the guide.

### Changed

- `fr.configure()` takes `async_session_generator` and `session_generator`,
  as it takes `async_engine` and `engine`. A generator of the wrong kind
  raises `TypeError`.
- Each outermost write action and `shared_write_action_commit()` block runs
  in a savepoint. An exception rolls back to it, so the session stays
  usable. Each write sends `SAVEPOINT` and `RELEASE SAVEPOINT`.
- What a route added to the session before a write action is written when
  the action begins, outside its savepoint.
- A response and a JSON column keep a NaN or an infinity as text; see "Keep
  NaN and infinity" in the guide. A response needs FastAPI 0.130 or later
  for it.
- A document with a `ReadOnly` field raises `RestlyConfigurationError`: a
  `PATCH` would set the field back to its default.
- Restly reads a row into the response class in lax mode, also for a strict
  class.
- `Envelope.data` is a `list`, not a `Sequence`.
- The `testing` extra needs `httpx2` 2.12.0 or later.

### Fixed

- A write that raised is no longer committed by the next write, and a caught
  `Conflict` no longer breaks the session.
- `RestlyUncommittedChangesWarning` no longer forgets a write when a later
  savepoint rolls back.
- On SQLite, a `session.begin_nested()` inside `shared_write_action_commit()`
  no longer commits its rows too early.
- A JSON column with a field such as `dict[str, Address]` or `list[datetime]`
  no longer gives a 500 error on each write. A secret in it raises
  `RestlyConfigurationError`.
- A JSON column stores the names of the fields, not their aliases, and no
  computed fields. It stores a `Json[T]` field as text. Before, each of these
  broke every response with the row.
- A document with a `Field(exclude_if=...)` field raises
  `RestlyConfigurationError`, as one with `Field(exclude=True)` does.
- A field on a JSON column gets only the `__isnull` filter and is no sort
  key. Other filters found no row on SQLite and gave a 500 error on
  PostgreSQL.
- `derive_schema` keeps each member of a union, as in
  `Mapped[Cat | Dog | None]`.
- A plain `View` that sets a `model` no longer fails when you register it.
- The react-admin list writes a NaN as text. Before, the list was not valid
  JSON.

## [0.12.0] - 2026-10-27

This release reworks how a view reads and writes. To upgrade, work through
these steps. A missed step fails with a message that names the replacement.
A step marked "Silent" gives no error when you miss it.

1. Declare a base of your own, `class Base(fr.ModelDefaults, DeclarativeBase)`,
   in place of `fr.DataclassBase`, and use `class Post(fr.IDMixin, Base)` in
   place of `fr.IDBase`. Use `Base` everywhere you used `fr.DataclassBase`,
   also in `alembic/env.py`. The tables stay the same.
2. Replace `fr.utils.CurrentSettingsMixin` with a `get_settings()` function.
3. Add `pydantic-settings` and `alembic` to the dependencies of your project
   if it uses them. Restly no longer installs them. A missed step fails with
   `ImportError`.
4. In `create` and `update`, call `super()`, change the object and return it.
   Drop `make_new_object`, `save_object` and each flush that only saved.
   Rename `schema_obj` to `data`.
5. Move a `get_one` or `get_many` override to `scope`, `where_id`,
   `not_found`, `to_single_response` or SQL on the model.
6. Pass `obj=` to `write_action`, also for a new object, in place of `as w`
   and `w.obj`.
7. Write each reference as `fr.MustExist[int, Model]`, in place of
   `fr.MustExist[int]` and `fr.RefExists`.
8. Declare your own actions in a class `Action` on the view, such as
   `class Action(fr.Action): PUBLISH = "publish"`, and pass
   `self.Action.PUBLISH` to `write_action`. A missed action fails when its
   route runs.
9. Let list params of your own on a view with pages subclass
   `fr.query.PagedListParams`, or set `pagination = None` on the view.
10. Silent: Restly now stores the times that clients send in UTC, also in a
    column without a time zone, and a response shows them with `Z`. If a
    column holds local times, give its field a validator that converts them.
11. Compare a time from the database with a time from a request through
    `fr.in_utc()`, as in `data.ends_at > fr.in_utc(obj.starts_at)`. A missed
    `<` or `>` raises `TypeError`. A missed `==` is silent: it is always
    `False`.
12. Silent: on PostgreSQL, an engine that Restly builds puts each session in
    UTC. Check SQL that depends on the time zone of the session, such as
    `date_trunc('day', created_at)`.
13. Silent: on SQLite, Restly now refuses a number that does not fit an
    `Integer` column, and text longer than a `String(n)` column. Give such a
    column a type that fits, such as `BigInteger`.
14. In a react-admin view, a field validator gets only its own field for a
    filter value, so `info.data` is `{}`. Use `info.data.get(...)`.

### Added

- `fr.ModelDefaults` and `fr.IDMixin`, for a base of your own.
- Hooks for the loads: `self.select()`, `where_id(id)` and `not_found(id)`.
- Markers for the view's own types, for routes on a base view or a mixin:
  `ViewID`, `ViewResponse`, `ViewListResponse`, `ViewCreate` and
  `ViewUpdate`.
- `include_routes`, the routes that a view serves.
- List settings: `include_filters`, `exclude_filters`, `include_sort_keys`,
  `exclude_sort_keys`, `sort_query_param`, `default_sort`, `sort_nulls` and
  `unknown_query_params`.
- `@fr.list_filter("key")`, a view method as the filter for a query key.
- `apply_filters(stmt, list_params)`: the filters, without the sort and the
  page.
- For list params of your own: `fr.query.PagedListParams`,
  `fr.query.FilterKey`, `fr.query.SortKey`, `fr.query.order_by_primary_key`
  and `fr.views.ReactAdminListParams`.
- `list[fr.MustExist[int, Model]]` checks each id of a list.
- A clause can go into the condition of another clause.
- `fr.UTCDatetime`, a `datetime` field in UTC, and `fr.in_utc()`.
- `fr.exc.RestlyExpireOnCommitWarning`, for a session that expires on commit.
- With `warn_on_misuse=True`, a warning for a writable field on a column that
  the model sets itself.

### Changed

Reads:

- `get_one`, `get_many` and `select` are final. `get_many` takes `where=`.
- `get_relationship_loader_options` is now `get_loader_options`.
- A model with a composite primary key needs `where_id`.
- A custom route that declares `ViewResponse`, or returns the view's model,
  goes through the view's response step.

Writes:

- The business methods no longer flush. Restly saves after them, so inside
  `create` the object has no id yet. Use `before_action_commit` or
  `session.flush()` for code that needs it.
- Business methods and `write_action` blocks run under
  `session.no_autoflush`.
- The request body is `data` everywhere, also in `fr.objects`.
- Restly checks the references after `authorize`, also in nested schemas.
- A write inside a `write_action` block joins its commit. A direct
  `session.commit()` there raises `RuntimeError`.
- A database conflict is a `fr.exc.Conflict` (409), with the `IntegrityError`
  as its `__cause__`. Catch `Conflict`, not `IntegrityError`.
- `new` and `old` in the hooks follow the row that the action is about.
- A replaced endpoint method gets the view's types only from the markers.
- A time without an offset is UTC, in bodies and in filters.
- A value that its column cannot hold gets a 422 or a 400, not a 500 on
  PostgreSQL. Restly checks only SQLAlchemy's generic integer and text types.

Lists:

- Dotted filter and sort keys are off by default. Turn them on with
  `include_filters = ("*", "*.*")` and `include_sort_keys = ("*", "*.*")`.
- An unknown or repeated sort key gets a 422.
- String enums, `Literal`s of strings and `Enum` columns get no range filters.
- NULLs sort last, on every database. `sort_nulls = None` gives the old order.
- A `dict`, `list` or JSON field is no sort key.
- The statement parameter is `stmt`, not `query`. On async views,
  `apply_list_params` is `async def`.
- The texts of a 400 name the problem, such as `Unknown sort key: 'nope'`.

React-admin:

- A filter value goes through the validation of its field, as in the
  generated filters. `null` works.
- A `sort`, `range` or `filter` with the wrong shape gets a 422.
- React-admin views refuse the key settings, `sort_query_param`,
  `default_sort` and `@fr.list_filter`.

Schemas and models:

- The update body keeps the type of each field, so `null` is valid only where
  the type allows `None`.
- In a generated schema, a field allows `null` only when its column is
  nullable, and each time column is a `fr.UTCDatetime`.
- In a generated create schema, a column with only an `insert_default` or a
  `server_default` is required. Add `default=None` to let clients leave it
  out.
- Models compare by identity. Their repr loads nothing.

Other:

- On PostgreSQL, an engine from a URL puts each session in UTC, and the
  `alembic/env.py` of `restly new` runs migrations in UTC.
- The test clients accept any 2xx status by default.
- `fr.clauses.UNSCOPED` is a `WhereClause`.
- `exclude_routes` is a `Collection`, not an `Iterable`.

### Removed

- Python 3.10.
- `fr.DataclassBase`, `fr.IDBase`, `fr.utils`, `fr.RefExists` and
  `fr.MustExist[int]` without a model. See the upgrade steps.
- The view methods `make_new_object`, `update_object` and `save_object`, and
  `run_write_action`. Use `super()` and `self.write_action()`.
- The dependencies on pydantic-settings and Alembic.
- `install_default_exception_handlers` and Restly's handler for
  `IntegrityError`. Register a handler for `fr.exc.Conflict`.
- `route_options`, `extra_query_params` and `react_admin_query_params`.
- The `warn_on_misuse` warning for CRUD routes on a plain `View`.

### Fixed

These gave a 500 or wrong data before:

- `?id=1` on PostgreSQL, and an id filter on a UUID key.
- A filter on an `Enum` column on PostgreSQL, and an `IntEnum` value.
- A list with pages of a model with a `json` column on PostgreSQL.
- A field of a secret type, such as `SecretStr`.
- A `timedelta` column: its view failed to register.
- A sort on a related field left out the rows without a related row.
- An expression for another table in `get_one` could change a wrong row.
- A field that the client left out stored `null` instead of the model's
  default.
- A react-admin filter: `"false"` matched the rows that are true, and a bad
  UUID gave a 500.
- A field name that ends in `_`, a flag type, a type alias, a `Sequence`
  field, `Annotated` inside `Optional`, and a schema that names itself.
- `restly new` after `pip install`.
- Many writes in one shared commit used memory that grew with the square of
  their number.

Other fixes give clearer errors, and fix type checking, FastAPI 0.115 and
Python 3.14.

### Security

- A generated schema marks each column with `init=False` as `ReadOnly`,
  foreign keys too. Before, a client could write such a column, such as a
  tenant column.

### Known issues

- A route that catches the error of a write action and goes on must put each
  write action in `session.begin_nested()`. See "When a write action raises"
  in the guide.
- On SQLite, a `session.begin_nested()` inside `shared_write_action_commit()`
  can commit its rows too early. See the warning in "Commit several writes
  together" in the guide.

## [0.11.0] - 2026-10-09

This release has many breaking changes. Before, we said to expect small
breaking changes in deeper extension points, and this release changes more
than that. If this affects your project: First of all, thank you for being one
of the very first users. Secondly, we are working towards the best API we can
make before freezing it in 1.0. That's when the promise of stability comes.
There will be more breaking changes every minor release until that time.

### Added

- `fr.NumberedPagination` and `fr.NoPagination` hold a view's pagination
  settings, including the model of the list response.
- A view can set its own response class with `schema_response`. A custom
  route can use `fr.schemas.derive_schema_response` and
  `fr.schemas.derive_schema_list_response` as its `response_model`.
- `fr.exc.RestlyDuplicateSchemaNameWarning` warns when two classes in the
  OpenAPI spec have the same name.

### Changed

- Names around lists and schemas follow the rules in CONTRIBUTING.md. A view
  that still uses an old name fails with an error that names the new one.
  - `listing_param_schema` is now `schema_list_params`.
  - `fr.ListingResult` is now `fr.ListResult`, and its `query_params` is now
    `list_params`.
  - `fr.ResponseShape.LISTING` is now `fr.ResponseShape.LIST`.
  - `to_listing_response(query_params, listing_result)` and react-admin's
    `to_react_admin_listing_response` are now `to_list_response(list_result)`.
  - `to_response_schema` is now `to_single_response`.
  - The `query_params` argument and route parameter are now `list_params`.
  - The view method `apply_query_params` is now `apply_list_params`.
  - `fr.query.apply_list_params(params, select_query, model, schema_cls)` is
    now `fr.query.apply_list_params(query, list_params, model,
    schema_response)`.
  - `fr.query.create_list_params_schema` is now
    `fr.query.derive_schema_list_params`.
  - `fr.schemas.create_schema_from_model(model_cls, schema_name)` is now
    `fr.schemas.derive_schema(model, name=...)`, without
    `include_readonly_fields` and `include_relationships`.
- Restly names the classes it generates `<Resource><Role>`, after the view's
  schema without a final `Schema`. For `UserSchema` they are `UserResponse`,
  `UserCreate`, `UserUpdate`, `UserListResponse` and `UserListParams`, so the
  types of generated clients change. A view without a schema gets
  `UserSchema` instead of `UserRead`.
- A response is an instance of the response class: the view's schema without
  its `WriteOnly` fields. Compare responses with `model_dump()`, not with `==`.
  A response cannot be pickled.
- The list params and the relationships that a view loads follow the response
  class. A `to_single_response` override that reads a relationship that only a
  `WriteOnly` field names must load it in `get_relationship_loader_options`.
- `pagination` replaces `paginated`, `default_page_size` and `max_page_size`,
  also in `fr.query.derive_schema_list_params`. Write `pagination = None` for
  `paginated = False`, and
  `pagination = fr.NumberedPagination(default_page_size=25, max_page_size=200)`
  for the sizes.
- `to_list_response` and `get_many_endpoint` return the list response model,
  not a dict.
- React-admin views answer `400` for a `range` with more rows than
  `max_page_size`, which is 1000 by default.
- Dotted filters and sorts join through SQL aliases. Set
  `include_aliases=True` on `with_loader_criteria` rules so they apply to
  these joins.
- Item routes use `/{id:int}` for integer keys and `/{id:uuid}` for UUID keys.
  An id that does not match answers `404` instead of `422`.
- Default route names are `get_many`, `get_one`, `create`, `update`, `delete`
  and react-admin's `put`. Operation IDs are `items_list`, `items_get` and so
  on, and the summary of `GET /{id}` is "Get". A view's `route_options` can
  set the old ones.
- Set custom session generators before you register routes that use
  `SessionDep` or `AsyncSessionDep`.
- SQLAlchemy 2.0.45 or newer is required.
- `fr.ContextParam` no longer has SQLAlchemy operator methods such as `in_()`.
- Generated create and update schemas leave out a primary key that the
  server generates. Declare `schema_create` to let clients send it.
- A view's `dependencies` and `responses` add up from the base class down, as
  `prefix` does, also from a mixin listed after the view base. A subclass can
  no longer remove a base dependency or response.
- The `fr.objects` write helpers no longer take `schema_cls`. A field that an
  explicit `schema_create` or `schema_update` declares is written even when the
  response schema marks it `ReadOnly`, so keep server-set fields, such as a
  tenant id, out of those schemas.
- A view class attribute with a `Query()` or other request parameter marker
  raises `RestlyConfigurationError`. Declare the parameter on the endpoint
  method instead.

### Fixed

- Restly works with FastAPI 0.137 and later.
- A field with a `default_factory` no longer makes `/openapi.json` answer
  `500`.
- A list endpoint accepts the query keys that its dependencies read, and a
  custom list route can take its own query parameters. A hand-written
  `schema_list_params` takes a field by its alias.
- A create saves the foreign key that the request sends, and works when a
  dataclass relationship has no default. An update can write a to-many
  relationship that the view does not load.
- A view subclass keeps the schemas that its base declares.
- The `{id}` path parameter takes the model's primary key type, so UUID and
  string keys work without `id_type`.
- Generated schemas take their fields from the SQLAlchemy mapper, so models
  with `from __future__ import annotations`, SQLModel tables and plain
  `Column` attributes get all their columns.
- A mapped class that does not subclass `DeclarativeBase`, such as a SQLModel
  table, works as a view's `model` and with reference types. A SQLModel class
  works as a view's `schema`.
- Configured session generators share one session per request with the app's
  own dependencies.
- Dotted filters and sorts work over a self-referential relationship.
- Context members work as `mapped_column(default=Current.user_id)` on models
  that are not dataclasses.
- React-admin views answer `400` for a negative or reversed `range`.
- Fixes for SQLAlchemy 2.1, Python 3.10, Python 3.14, mypy and pyright.

### Security

- List endpoints no longer filter or sort on `WriteOnly` fields or on fields
  with `Field(exclude=True)`, which let a client read a hidden value back. A
  response with `extra="allow"` no longer sends a key with the name of a
  `WriteOnly` field.

## [0.10.0] - 2026-09-23

### Added

- `fr.ContextNamespace` and `fr.ContextParam` support an application-defined
  `Current` context, with manual and FastAPI dependency binding.
- Reusable query predicates through `fr.where_clause`, boolean composition,
  and `fr.apply_clauses`. Group them with `fr.ClauseNamespace`.
- Model default scopes filter view reads and reference checks. Views and
  individual reads can replace the scope or opt out with `fr.clauses.UNSCOPED`.
- `shared_write_action_commit()` groups several write actions in one
  transaction. After-hooks run only after a successful commit.
- Retrieve, update, and delete handlers accept SQLAlchemy expressions to
  identify rows by natural or composite keys.
- Custom view routes can use generated `query_params`. Pass `where=` to
  `handle_get_many()` to narrow a listing without replacing its scope.
- `restly new <name>` generates a project with optional database and Alembic setup.
- `fr.configure(health="/health")` adds a liveness endpoint.
- `fr.utils.CurrentSettingsMixin` provides a shared `Settings.current` instance.

### Changed

- `Mapped[datetime]` and `TimestampsMixin` now use `DateTime(timezone=True)`.
  Datetime filters without an offset use UTC for these columns.

  Migrate affected PostgreSQL columns, preserving the timezone of existing
  values. A bare type change can shift stored timestamps. For values stored
  as naive UTC:

  ```sql
  ALTER TABLE example
    ALTER COLUMN created_at TYPE timestamptz
    USING created_at AT TIME ZONE 'UTC';
  ```

  Use `mapped_column(DateTime())` to keep naive wall-clock values.
- Rename `before_commit` and `after_commit` overrides to
  `before_action_commit` and `after_action_commit`.
- `get_one()` and `get_many()` overrides must accept and forward `scope=`.
  Include `ColumnElement[bool]` in a `get_one()` override's `id` annotation.
- `handle_*`, `make_new_object`, `update_object`, and `save_object` on
  `RestView` and `AsyncRestView` can no longer be overridden. Move custom logic
  to business methods, `authorize`, endpoint methods, or action hooks.
  Use SQLAlchemy column defaults for server-stamped fields.
- Engines created by `fr.configure()` enforce SQLite foreign keys and share
  in-memory databases across threads. Invalid references return 409.
- React-admin listings reject unknown query parameters with 422.
  Allow custom keys with `extra_query_params`.
- Pydantic 2.11.7 or newer is required.

### Removed

- `build_query`. Move visibility rules to `default_scope` or the view's
  `scope`, listing joins and ordering to `apply_query_params`, and eager loading
  to `get_relationship_loader_options`.
- `RestView.delete_object` and `AsyncRestView.delete_object`. Override `delete`
  for soft deletion. The `fr.objects` deletion helpers remain available.

### Fixed

- Query filters support recursive and frozen schemas without running
  cross-field validators. Field validators receive the correct field name.
- Nested Pydantic values serialize into JSON columns. Excluded fields and other
  unsupported document shapes require an explicit `TypeDecorator`.
- `MustExist` and `RefExists` honor session-level SQLAlchemy visibility rules.
- `save_object` no longer expires related objects during async writes.
- Stable listing order supports renamed and composite primary keys.
- SaaS example fixes for tenant isolation, authorization, and organization deletion.

## [0.9.0] - 2026-08-13

### Added

- `fr.testing.configure_tests()` for schema setup and per-test database isolation.
- `db_cleanup=` modes (`rollback`, `delete`, and `none`) with per-run
  `--restly-db-cleanup` and `RESTLY_DB_CLEANUP` overrides.
- Pooled asyncpg support for applications configured with only an async
  database; no synchronous database configuration or test-only `NullPool`
  engine is required.
- `AsyncRestlyTestClient` and the `restly_async_client` fixture.
- SQLAlchemy `AsyncAttrs` support through `awaitable_attrs` on Restly models.
- `get_relationship_loader_options()` as an eager-loading override.
- PostgreSQL CI coverage for dialect-specific behavior and database fixtures.
- Public `Envelope` and `PaginatedEnvelope` response models.

### Changed

- List endpoints now paginate by default. `GET /things` changes from an
  unbounded bare array to
  `{"data": [...], "total_count": ..., "page": ..., "page_size": ..., "total_pages": ...}`,
  capped at `default_page_size` (50). Existing paginated responses must rename
  `items` to `data` and `total` to `total_count`.
  `include_pagination_metadata` and `to_paginated_listing_response()` are
  removed: set `paginated = False` for an unpaginated `{"data": [...]}`
  response with no pagination query parameters, override
  `to_listing_response()` to customize that envelope, or replace
  `get_many_endpoint` with a matching `response_model` if the API must keep
  returning a bare array. `default_page_size` must now be an integer from 1
  through `max_page_size`; the old `None` value is replaced by
  `paginated = False`. Single-object responses are unchanged.

- After `configure_tests()`, rollback isolation now applies to every test that
  uses the configured database, including client-only tests and code that calls
  `fr.open_session()` or `fr.open_async_session()` without requesting a public
  session fixture. Sessions join one test transaction through SQLAlchemy
  savepoints, so `commit()` and `rollback()` behave as in production. A value
  added through a session fixture becomes visible to a request after `flush()`
  or `commit()`; the sessions do not share an identity map.

- Under rollback cleanup, an application configured with only an async database
  can no longer combine the synchronous `restly_client` with
  `restly_async_session` or other direct async database access. That would use
  one transaction from two event loops, so Restly raises a configuration error
  before database work begins and directs the test to `restly_async_client`.
  Under `"delete"` and `"none"`, the same check applies to drivers such as
  asyncpg that bind pooled connections to one event loop.

- Under rollback cleanup, requesting `restly_session` or
  `restly_async_session` with only a custom session generator now raises instead
  of skipping. Configure the corresponding database URL, engine, or
  sessionmaker as well so Restly can build the isolated session.

### Fixed

- `restly_client` now runs application lifespan startup and shutdown once per
  test. A view registered through `restly_client.app` after the fixture has
  constructed the client also keeps working, including Restly's integrity-error
  handling.
- PostgreSQL constraint errors now produce clean details with psycopg 3.
- Write responses now eager-load schema relationships, avoiding `MissingGreenlet` errors and sync N+1 queries.
- Rollback isolation now routes applications that configure a custom session
  generator alongside a matching sessionmaker through the isolated factory,
  instead of letting request writes escape the test transaction.
- `restly_project_root` now resolves from the requesting test file, including in monorepos.
- `restly_async_session` no longer errors when a synchronous sessionmaker is
  also configured. Under rollback cleanup, it uses the same pinned connection
  as `restly_session`, so committed writes are visible between them within a
  test.
- Engine and schema helpers now work inside session fixtures.

### Removed

- `activate_savepoint_only_mode()` and `deactivate_savepoint_only_mode()` are
  removed. They isolated each session separately rather than the whole test, so
  a write from one request was not reliably visible to the next. Call
  `fr.testing.configure_tests()` once in `conftest.py` instead; it gives the
  client, session fixtures, and directly opened sessions one transaction for
  the whole test.

## [0.8.0] - 2026-07-22

### Added

- `fr.MustExist[int, Post]` — an existence-checked scalar foreign key.
  `post_id: fr.MustExist[int, Post]` validates that the referenced row exists (a
  clean 404 on a miss, batched — no N+1) while the field stays a plain scalar
  everywhere: on the wire, in the column, in hooks (`data.post_id` is the id
  itself, not an `IDRef`/`IDSchema` wrapper), and to a type checker
  (`data.post_id` is `int`). The primary-key type comes first
  (`fr.MustExist[UUID, Account]` for a UUID key); when the column has a single
  `ForeignKey`, you can drop the model and let Restly infer it — `fr.MustExist[int]`.
  Reserve `IDRef` / `IDSchema` for relationship-named fields.

- The opt-in `fr.configure(warn_on_misuse=True)` lint now flags the
  `post_id: fr.IDRef[Post]` mistake — an `IDRef` / `IDSchema` reference typed on a
  scalar foreign-key **column**, where `data.post_id` becomes a wrapper instead of
  the plain id — steering to `fr.MustExist[int, Post]`. `IDRef` / `IDSchema` are
  now documented as relationship-named types, with `*_id` columns pointed at
  `MustExist`.

### Changed

- The tier vocabulary in docstrings and error messages now matches the docs:
  *endpoint method* (was "route shell"), *handler* (was "request handler"), and
  *business method* (was "business verb"). The misuse warning
  (`RestlyMisuseWarning`) and the bare-verb route-name `TypeError` use the new
  terms; match on `"endpoint method"` / `"business method"` if you asserted on
  those messages.

- `View.tags` is now typed `ClassVar[Iterable[str | Enum] | None]` instead of
  `ClassVar[Any]`, matching what FastAPI accepts for router tags. Runtime
  behaviour is unchanged; type checkers now flag a wrongly-typed `tags`.

- The `[testing]` extra now installs `httpx2` alongside `httpx`. Newer Starlette
  `TestClient` prefers `httpx2` and emits a `StarletteDeprecationWarning` when
  only `httpx` is present; shipping both keeps `RestlyTestClient` on the
  non-deprecated path without dropping support for the older Starlette versions
  in our range, which still import `httpx` directly.

- `RestlyTestClient` now prefers `httpx2` (falling back to `httpx`), matching
  Starlette's own `TestClient`. The "install the `[testing]` extra" hint that
  Restly raises when the test client is missing now also fires under newer
  Starlette, which signals the absence as `httpx2` (and via a `RuntimeError`)
  rather than a missing `httpx`.

### Removed

- The `orjson` dependency and the orjson serializer on URL-created engines.
  `JSON` columns now use SQLAlchemy's default encoder (the standard library
  `json`) on every engine: naive datetimes are no longer silently stamped as
  UTC, and datetime/UUID values or keys now raise `TypeError` at write time
  instead of being coerced to strings. To keep the old behavior, build your
  own engine with `json_serializer=`/`json_deserializer=` and pass it to
  `fr.configure()`.

### Fixed

- Filtering on a dotted path deeper than one relationship hop
  (`?city.country.code=NL`) no longer fails with a 500. Deep paths were
  advertised and resolved, but the filter clause collected its joins into an
  unordered set, so the second hop could be joined before the first — an
  implicit cartesian product that the database rejects as an ambiguous join.
  Filter joins now apply in path order, as sorting always did. Two shapes
  remain unsupported and are now documented as such: paths through a
  self-referential relationship, and two filter paths that reach the same
  table — those need per-path join aliasing.

- List views no longer advertise filter parameters that can never execute for
  collection-typed columns (`JSON`/`ARRAY`, i.e. fields typed `dict`,
  `list[...]`, `Sequence`, and similar). A query-string value cannot coerce
  into a collection, so `eq`/`__in`/`__ne` — and, for parametrized generics
  such as `list[str]`, the range family — answered 400 on every request;
  those parameters are now omitted from the generated schema and OpenAPI, and
  only `__isnull`, which works, is kept. On generated endpoints, requests
  using the removed parameters now fail validation as unknown parameters
  (422) instead of passing validation and failing at execution; callers
  passing raw `QueryParams` to `apply_list_params` still get the 400.
  `pydantic.Json[...]` fields keep their filters: their validation parses
  the query string into the collection, so they execute.

- Registering the same view class twice on the same app or router
  (`fr.include_view(app, V); fr.include_view(app, V)` — a double import, or the
  decorator form combined with an explicit call) no longer mounts its routes
  twice. The duplicate call is now a no-op: each parent tracks the view classes
  already mounted on it, and an app and its `.router` attribute count as one
  parent. The opt-in `fr.configure(warn_on_misuse=True)` lint flags the
  duplicate call with a `RestlyMisuseWarning`. Registering on *different*
  parents (a public and an admin app, `/v1` and `/v2` sub-apps) still mounts
  on each, as before.

- `fr.IDRef[T]` / `fr.IDSchema[T]` foreign-key fields now work under any column
  name, not only fields ending in `_id`. A field like
  `post_fk: fr.IDRef[Post]` backed by a non-`_id` FK column was silently
  misrouted — the resolved ORM object was assigned into the integer FK column
  and the request failed at flush (`sqlalchemy.exc.ProgrammingError`) instead of
  at validation. Reference routing (the create plan, the in-place update, and
  the both-supplied FK/relationship consistency check) now decides column vs.
  relationship and derives the partner attribute from the SQLAlchemy mapper
  rather than from the field name, so any FK column name resolves correctly.

- Creating with a relationship exposed as a reference field (e.g.
  `invoice: fr.IDRef[Invoice]`) no longer fails when the model's local FK column
  is a required constructor argument (no `init=False` and no default) and a
  reference to an existing row is supplied. The resolved row's id is now passed
  at construction instead of being assigned afterward, so the dataclass
  `__init__` no longer raises `TypeError: ... missing 1 required keyword-only
  argument`. Declaring the FK column `init=False` is still supported but no
  longer required.

- A null reference — an explicit `post=None` for a `post: fr.IDRef[Post] | None`
  field, or such a field omitted and defaulting to `None` — no longer raises
  `TypeError: __init__() missing 1 required keyword-only argument: 'post_id'`
  when the model's local FK column is a required constructor argument (no
  `init=False` and no default). A null reference now takes a dedicated plan
  path that writes the field's own slot and passes a partner kwarg (as
  `NULL`) at construction when the dataclass requires it — in either
  direction (FK column or relationship) and nothing more, so an unset sibling
  reference field (schemas may declare both names of an FK/relationship pair
  as reference fields) never clobbers the side the client supplied. A null
  reference to a *nullable* FK creates the row with a NULL FK; to a
  *non-nullable* FK it now fails at flush as a regular `IntegrityError` (the
  standard 409 path) instead of the 500 `TypeError`.

- `fr.IDRef[T]` now serializes through the type itself under plain Pydantic
  `from_attributes`, so a reference field validated outside a Restly route — a
  nested model, a custom endpoint, or `response_model=` on a raw schema — no
  longer crashes with a cryptic `int_type` error when the value is the related
  ORM row. Previously only the view layer's `to_response_schema` handled this,
  by pre-extracting the scalar id; that redundant special-casing is removed and
  both `IDRef[T]` and `IDSchema[T]` self-serialize from a related row or a raw
  scalar id along a single path. Response output is unchanged.

## [0.7.0] - 2026-06-11

### Added

- The generated route shells (`get_many_endpoint`, `create_endpoint`, ...)
  now carry one-line override-redirect docstrings, so `help(RestView)`, source
  readers, and coding agents see which tier to override (`<verb>` for domain
  logic, `handle_<verb>` for orchestration, `to_response` for shape). The
  docstrings are stripped from generated routes at registration so framework
  guidance never appears as OpenAPI operation descriptions in your API;
  endpoints you define or override yourself keep FastAPI's normal docstring
  behavior.

- Scalar `fr.IDRef[T]` foreign-key fields are now filterable on list endpoints
  by their own public name. Previously `post_id: fr.IDRef[Post]` — the FK form
  the tutorial teaches — generated no filter parameter at all, so
  `GET /comments/?post_id=1` returned a 422 that looked like client error; the
  only filterable form was a plain `post_id: int`. An `IDRef` id is treated as
  opaque, so it gets equality, `__in`, `__ne`, and `__isnull` (uniform across
  int/UUID/string primary keys) but not the range or substring operator
  families.
- Python 3.14 is now officially supported and tested. It was previously in the
  CI matrix as an experimental (allowed-to-fail) target while `orjson` lacked a
  3.14 wheel; that wheel now ships, the full test suite passes on 3.14, and the
  job gates CI like every other supported version.
- `fr.db.create_all(Base)` / `fr.db.async_create_all(Base)` — dev/demo helpers
  that create every table for a declarative base (or a `MetaData`) on the engine
  configured via `fr.configure()`, replacing the `engine =
  fr.db.get_async_engine(); async with engine.begin() as conn: await
  conn.run_sync(Base.metadata.create_all)` boilerplate in quickstarts and test
  setup. Use Alembic migrations in production.
- Opt-in registration-time misuse warnings: `fr.configure(warn_on_misuse=True)`
  makes `include_view` lint each registered view class and emit
  `fr.exc.RestlyMisuseWarning` for the three dominant misuse patterns —
  overriding a route shell (`<verb>_endpoint`) where a business-verb override
  was meant, calling `session.commit()` directly in a view method, and
  hand-rolling a CRUD route set on a bare `View` instead of subclassing
  `RestView` / `AsyncRestView`. Each message names the idiomatic fix. Off by
  default; intended for development, project templates, and CI.

### Changed

- The `RestlyUncommittedChangesWarning` message now leads with the fix
  (bracket the mutation with `write_action(...)` or reuse a `handle_<verb>`)
  and offers only the per-route suppression
  (`session.info["_fr_suppress_uncommitted"] = True`) for intentional
  dry runs. It no longer advertises the global
  `warn_on_uncommitted=False` opt-out, which readers took as a fix for the
  warning instead of committing their changes.
- **Breaking — top-level `fr.*` namespace curated.** Errors, HTTP exceptions, and
  the uncommitted-changes warning moved to `fr.exc` (`fr.exc.NotFound`,
  `fr.exc.RestlyError`, …) — the `exceptions` module is renamed `exc`, mirroring
  `sqlalchemy.exc`. Advanced helpers moved to their layer submodules: schema↔ORM
  helpers to `fr.objects.*` (`make_new_object` / `save_object` / `snapshot` / …
  and the `async_*` variants), list query helpers to `fr.query.*`
  (`create_list_params_schema`, `apply_list_params`), engine accessors to
  `fr.db.*` (`get_engine`, `get_async_engine`), and `IDMixin` to `fr.models.*`.
  The route decorators (`@fr.get` / `@fr.post` / … / `@fr.route`), views,
  registration, schemas, model bases, `configure`, the session
  helpers/dependencies, and the view support types (`Action` / `ViewRoute` /
  `ResponseShape` / `ListingResult`) stay top-level. **Migration:** e.g.
  `fr.NotFound` → `fr.exc.NotFound`,
  `fr.make_new_object` → `fr.objects.make_new_object`,
  `fr.get_async_engine` → `fr.db.get_async_engine`.

### Fixed

- `fr.open_session()` / `fr.open_async_session()` now resolve the same session
  source as `SessionDep` / `AsyncSessionDep`: a custom session generator passed
  to `fr.configure(session_generator=...)` / `sync_session_generator=...` takes
  precedence over the built-in factory. Previously the context managers always
  used the built-in factory, so a generator-only configuration worked inside
  request handlers but raised `RestlyConfigurationError` off-HTTP (in scripts,
  background jobs, or a custom dependency wrapping `open_*session()`). The two
  session entry points are now consistent.

## [0.6.1] - 2026-06-02

### Changed

- Reference resolution (`IDRef`/`IDSchema`) no longer mutates the validated
  request model in place. The resolver now returns a `{field: resolved}` mapping
  that the write path consumes, so the request model keeps its wire shape (its
  reference fields stay `IDRef[T]` values rather than being overwritten with ORM
  rows). Behavior of create/update is unchanged; the internal helpers
  `build_create_plan` / `apply_update_to_object` /
  `validate_resolved_reference_consistency` gained a `resolved` argument.
- The `standard` extra is now runtime-only and mirrors `fastapi[standard]`. It no
  longer pulls the test toolchain (`pytest`, `pytest-asyncio`, `pytest-cov`,
  `httpx`) or bundles the `aiosqlite` driver, so installing
  `fastapi-restly[standard]` for production no longer drags pytest into the image.
  Test tooling stays in the `testing` extra; the database driver is now an
  explicit choice (Restly remains driver-agnostic). **Migration:** if you relied
  on `[standard]` for test dependencies, switch to `[testing]`; if you ran on
  SQLite, add `aiosqlite` to your dependencies directly.
- The `testing` extra now includes only the third-party packages Restly's shipped
  test helpers import — `pytest`, `pytest-asyncio`, and `httpx` (for
  `RestlyTestClient` and the `restly_*` fixtures). `pytest-cov` is no longer
  pulled in; add it yourself if you want coverage reports.
- Removed the `docs` extra from the published extras. It only installed the
  toolchain for building Restly's own documentation site (a maintainer concern);
  those dependencies remain in the `dev` dependency group. The published extras
  are now `standard` and `testing`.

### Fixed

- `RestlyUncommittedChangesWarning` no longer false-positives on every write run
  under the savepoint test fixtures: the patched `commit` clears the
  pending-changes flag (mimicking the real `after_commit`), while a genuinely
  forgotten commit still warns.
- An `IDRef` list field that references the same id more than once no longer
  raises a confusing `Id not found: set()` 404 when the referenced rows all
  exist; a genuinely missing id is now named in the error.
- An `IDRef` list field now resolves in the client-sent order instead of
  silently reordering to the database's primary-key order (duplicate ids are
  collapsed, first occurrence wins).
- A `ReadOnly` or `WriteOnly` marker nested inside a field's type instead of
  wrapping it (such as `Optional[WriteOnly[str]]`, `WriteOnly[str] | None`, or
  `list[WriteOnly[str]]`) is now rejected with a `RestlyConfigurationError`
  instead of silently no-op'ing. Nested there the marker has no effect — a
  `WriteOnly` field would leak into responses and a `ReadOnly` field would stay
  writable — so the framework now raises when the schema is defined (and again at
  view registration for schemas that do not derive from `BaseSchema`), pointing
  to the safe `Marker[Optional[T]]` form.
- A list view no longer advertises filter query parameters for fields that are
  not filterable columns — a to-many relationship (`books: list[BookRef]`) or a
  reference field that does not resolve to a column. These appeared in OpenAPI
  but always returned 400. Filter-param generation now validates each field
  against the model with the same column-resolution predicate the request path
  uses, so non-column fields no longer get filter params; to-one dotted traversal
  is unchanged. (`create_list_params_schema` now takes the queried `model` as a
  required argument.)

## [0.6.0] - 2026-06-01

Reworks the class-based view API around a three-tier "handle" design. This is a
breaking change; views written for 0.5.x need updating.

### Changed (breaking)

- Each CRUD verb now has three tiers: route shell (`*_endpoint`), request
  handler (`handle_*`), and domain verb (`get_many`, `get_one`, `create`,
  `update`, `delete`). This replaces the `listing`/`get`/`create`/`update`/
  `delete` endpoints and the single `perform_*` tier.
- The framework owns commits. `handle_<verb>` and `write_action` run
  `before_commit` → commit → `after_commit`; request-session dependencies no
  longer commit on response. Custom write routes should reuse `handle_<verb>` or
  bracket mutations with `self.write_action(...)`. Manual `session.commit()` is
  only for shapes the bracket does not model, such as batch commits.
  `commit_session_on_response` is removed; custom session generators construct
  and clean up sessions but do not own commits.
- Renamed: `creation_schema`/`update_schema` → `schema_create`/`schema_update`;
  `build_from_schema`/`apply_schema` → `make_new_object`/`update_object`;
  `count_listing` → `count`. Response shaping goes through a single
  `to_response(obj_or_list, shape=ResponseShape.SINGLE)` method.
  `ViewRoute.LIST`/`GET` → `ViewRoute.GET_MANY`/`GET_ONE`.

### Added

- `authorize(action, obj, data)` override — an empty override by default; raise
  `fr.Forbidden` / `fr.NotFound` to gate a verb (row visibility goes in
  `build_query`).
- `before_commit` / `after_commit` transaction hooks and a `snapshot()` helper
  for old-vs-new comparison.
- Typed request-time exceptions `NotFound`, `Forbidden`, `Conflict`, and
  `BadQueryParam`, subclassing `fastapi.HTTPException` (so a single
  `app.add_exception_handler(fr.NotFound, ...)` can reshape them).
- Top-level `make_new_object` / `update_object` / `save_object` /
  `delete_object` / `snapshot` helpers (and their `async_*` variants where
  applicable) for use outside a view.
- `write_action(action, *, obj, data)` — a context manager for custom write
  actions. It shares the CRUD authorize + commit bracket. Create-shaped actions
  that omit `obj=` must assign the yielded handle's `.obj` before exit.
- `RestlyUncommittedChangesWarning` (default on; `warn_on_uncommitted=False` to
  disable) when a request finishes with uncommitted changes — the tell of a
  write route that forgot to commit.

### Fixed

- A `@route` method named like a bare verb (`create`/`update`/`delete`/
  `get_one`/`get_many`) is now rejected at registration: it shadowed the verb
  and collided with its `*_endpoint` route shell.
- Registering a View subclass alongside its parent on the same app no longer
  duplicates the child's routes.
- React Admin list/count/update paths now use the same `build_query`, `count`,
  authorization, and commit lifecycle as standard REST views; filter/sort
  resolution is limited to public schema fields.
- A paginated list sorted on a non-unique column now appends the primary key as
  a final `ORDER BY` tiebreaker, so rows are no longer skipped or repeated across
  pages. Applies to both the standard and React Admin sort paths.
- A `build_query` that joins a to-many relationship no longer fans out: `get_many`
  de-duplicates entities and the list total counts distinct rows, so the page and
  `total_count` agree. A no-op for queries without such a join.
- A `WriteOnly` field no longer leaks into a response, including from a nested
  response schema. `WriteOnly` fields are now excluded from serialization at the
  field level (`exclude=True` on the marker), so they are stripped recursively on
  the wire and dropped from the OpenAPI response schema, while staying required,
  documented request inputs. Prefer `WriteOnly[Optional[T]]` over
  `Optional[WriteOnly[T]]` — a `WriteOnly` marker buried only inside a union is
  not excluded.

## [0.5.1] - 2026-05-11

### Fixed

- Fixed `fr.include_view(...)` registration on `fastapi.APIRouter` parents.

## [0.5.0] - 2026-05-06

First public beta release.

### Added

- Class-based CRUD views for async and sync SQLAlchemy sessions with generated
  list, get, create, update, and delete routes.
- React Admin compatible `AsyncReactAdminView` and `ReactAdminView` variants
  for the `ra-data-simple-rest` wire contract.
- Generated schema support for read, create, and update payloads, including
  `ReadOnly`, `WriteOnly`, `IDSchema`, `IDRef`, and timestamp schema helpers.
- Standard list query support for filtering, sorting, pagination, relation
  aliases, and pagination metadata.
- Public `RestlyError` and `RestlyConfigurationError` exception hierarchy.
- Testing utilities through `RestlyTestClient`, savepoint-only mode helpers,
  and the `fastapi_restly.pytest_fixtures` pytest plugin.

### Changed

- Consolidated framework setup on `fr.configure(...)`, including async/sync
  engine configuration and response-session commit policy.
- Renamed built-in route methods to resource-oriented names:
  `list`, `get`, `create`, `update`, and `delete`.
- Renamed business-logic hooks to `perform_list`, `perform_get`,
  `perform_create`, `perform_update`, and `perform_delete`.
- Standardized schema component names on `ModelRead`, `ModelCreate`, and
  `ModelUpdate`.
- Made `sort` the standard list ordering parameter.
- Split `__contains` and `__icontains` so case-sensitive and
  case-insensitive matching have distinct public operators.
- Exposed savepoint-only testing helpers through `fastapi_restly.testing`
  instead of the top-level package namespace.
- Renamed `build_listing_query` to `build_query` and broadened its role:
  `perform_get` now also routes through this hook, so a single override filters
  listing, the pagination total, and single-row fetches. A row hidden from
  listing returns 404 from `GET /{id}` too, and `perform_update` /
  `perform_delete` inherit the visibility check via `perform_get`.
- `perform_get` now issues `SELECT ... WHERE pk = ?` instead of
  `session.get(...)`. Single-column primary-key behavior is unchanged.
  Composite-primary-key subclasses must override `perform_get`.
- Advanced schema-to-object helpers now live in `fastapi_restly.objects`:
  `build_from_schema`, `apply_schema`, `save_object`, and `delete_object`,
  with async equivalents. The view methods use the same names for the mapping
  hooks and keep persistence at the `save_object` / `delete_object` boundary.

### Removed

- Removed the pre-stable `query=` argument from `perform_listing`. Override
  `build_query()` for SQL-level base query changes so listing, pagination
  totals, and single-row fetches stay aligned.

- Removed pre-release route and hook names such as `index`, `get`, `post`,
  `patch`, `delete`, `handle_list`, and `handle_get` from the public view API.
- Removed unsupported `get_one_or_create` helpers before the first stable
  release.
- Removed internal model helpers such as `TableNameMixin`, `underscore`, and
  `utc_now` from the public API surface.
- Removed duplicate pytest fixture exports from `fastapi_restly.testing`;
  the pytest plugin path is `fastapi_restly.pytest_fixtures`.

[Unreleased]: https://github.com/rjprins/fastapi-restly/compare/v0.13.0...HEAD
[0.13.0]: https://github.com/rjprins/fastapi-restly/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/rjprins/fastapi-restly/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/rjprins/fastapi-restly/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/rjprins/fastapi-restly/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/rjprins/fastapi-restly/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/rjprins/fastapi-restly/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/rjprins/fastapi-restly/compare/v0.6.1...v0.7.0
[0.6.1]: https://github.com/rjprins/fastapi-restly/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/rjprins/fastapi-restly/compare/v0.5.1...v0.6.0
[0.5.1]: https://github.com/rjprins/fastapi-restly/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/rjprins/fastapi-restly/releases/tag/v0.5.0
