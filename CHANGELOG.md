# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

This release reworks how a view reads and writes. To upgrade, work through
these steps. A step that you miss fails with a message that names the
replacement, unless it says otherwise.

1. Declare a base of your own, `class Base(fr.ModelDefaults, DeclarativeBase)`,
   in place of `fr.DataclassBase` and `fr.IDBase`.
2. Replace `fr.utils.CurrentSettingsMixin` with a `get_settings()` function.
3. In `create` and `update`, call `super()`, change the object and return
   it. Drop `make_new_object`, `save_object` and each flush that only saved.
   Rename `schema_obj` to `data`.
4. Move a `get_one` or `get_many` override to its new place: `scope`,
   `where_id`, `not_found`, `to_single_response`, or SQL on the model.
5. Pass `obj=` to `write_action`, also for a new object, instead of
   `as w` and `w.obj`.
6. Write each reference as `fr.MustExist[int, Model]`, in place of
   `fr.MustExist[int]` and `fr.RefExists`.

### Added

- Markers for the view's own types in `fastapi_restly.views`: `ViewID`,
  `ViewResponse`, `ViewListResponse`, `ViewCreate` and `ViewUpdate`. When
  the view registers, Restly puts the view's classes in their place, so a
  route on a base view or a mixin gets the classes of each view that uses
  it. They work in annotations, in `response_model=` and inside other types,
  such as `list[ViewResponse]`.
- `self.select(*, scope=None)`: `select(self.model)` with the view's scope
  and loader options. The loads start from it, and so can a custom route.
- `where_id(id)` returns the WHERE for an id from the path, such as
  `Recipe.slug == id`. `not_found(id)` returns the error for an id that names
  no row. Every item route and `self.get_one(id)` use them.
- Four list settings choose the keys of a list: `include_filters`,
  `exclude_filters`, `include_sort_keys` and `exclude_sort_keys`. An entry is
  a key, or a pattern: `*` matches any characters except `.`, and `**`
  matches any characters. `fr.query.derive_schema_list_params` takes them
  too.
- `sort_query_param` renames the sort parameter, such as `"order_by"`.
- `unknown_query_params = "ignore"` leaves out a query key that the route
  does not read, as FastAPI does. The default, `"forbid"`, answers `422`, as
  before.
- `@fr.list_filter("key")` makes a view method the filter for a query key.
  The type of its parameter is the type of the key, and its docstring is the
  description. It returns a condition, a clause or `None`, and it may be
  `async def` on an async view.
- `list[fr.MustExist[int, Model]]` checks each id of a list, such as the ids
  in the body of a custom route. The message names every missing id.
- `apply_filters(stmt, list_params)` applies the client's filters without
  the sort and the page, for a route with `GROUP BY`.
- `fr.views.ReactAdminListParams`, the list params of a react-admin view.
  Subclass it to add a key, such as a search key.
- A clause can go into the condition of another clause, such as
  `fr.where_clause(Comment.post.has(PostClauses.default_scope))`.
- `fr.ModelDefaults` adds Restly's model defaults to a base of your own:
  `class Base(fr.ModelDefaults, DeclarativeBase)`. The models get
  keyword-only dataclass constructors, snake_case table names, timezone-aware
  datetimes, enums stored as strings and `awaitable_attrs`. Set dataclass
  options such as `eq=True` on a model, not on the base.
- `fr.IDMixin` at the top level.
- With `warn_on_misuse=True`, Restly warns about a writable field on a column
  that the model sets itself (`init=False` with a default), such as a tenant
  column.

### Changed

#### Reads

- `get_one` and `get_many` are final, as the handlers are. A view class that
  defines one, or `select`, fails when Python creates the class. Change the
  rows with `scope`, the lookup with `where_id`, the list with
  `apply_list_params`, and the loads with `get_loader_options`. A value on
  each row that needs no query goes in `to_single_response`, as
  `super().to_single_response(obj).model_copy(update={...})`. A value from
  SQL goes on the model as a `column_property`, or in `get_loader_options`
  as a `with_expression`. A read from another source replaces the endpoint
  method. `get_many` takes `where=`.
- A model with a composite primary key needs `where_id` for its item routes,
  and `id_type = str` for an id such as `3-7`. Without `where_id`, the view
  fails when it registers.
- `get_relationship_loader_options` is now `get_loader_options`. It holds
  everything that a read loads, a `with_expression` too.
- A custom route that declares the view's response goes through the view's
  response step, so it can return the row. A row under `ViewResponse` goes
  through `to_response`, each row under `list[ViewResponse]` through
  `to_single_response`, and a `ListResult` under `ViewListResponse` through
  the list response. `-> Project` or `-> list[Project]` for the view's own
  model works like `ViewResponse`, and so does the view's own response
  class, such as the 0.11 form
  `response_model=fr.schemas.derive_schema_response(ProjectSchema)`. A route
  that names another Pydantic class keeps FastAPI's behaviour and skips
  `to_single_response`.

#### Writes

- The write business methods no longer flush. `create` and `update` return
  the object, and `delete` only marks the row. After them, the commit bracket
  saves: it adds a new object, flushes, refreshes it and the other rows of
  the view's model that the action wrote, and loads them with the loader
  options. So a change after `super().create()` is part of the INSERT. Inside
  `create`, the new object has no id yet: code that needs it goes in
  `before_action_commit`, which runs after the save, or calls
  `session.flush()`. A `try` around `super().create()` no longer catches an
  error of the INSERT.
- A business method and a `write_action` block run under
  `session.no_autoflush`, also on a sync view. A query there does not see your
  unflushed changes, so a get-or-create loop or a count against a limit calls
  `flush()` first.
- `write_action(action, *, obj, data=None)`: `obj` is required. It is a row
  from the database, a new object that the action creates, or `None` for an
  action on several rows. Restly adds a new object to the session when the
  block starts, so a `flush()` in the block gives it an id, and `authorize`
  gets `obj=None`. A detached row is added back, and a row
  that another open session holds raises `TypeError`. The block gets nothing
  from `as`: pass the new object as `obj` instead of setting `w.obj`.
- `new` and `old` in the hooks follow the row that the action is about. So a
  soft delete through `DELETE /{id}` gives the row as `new`, and a block that
  deletes its `obj` gives `new=None`.
- A `create` or `update` that does not return one object of a model raises
  `TypeError`, and nothing is committed.
- The request body is `data` in every view method: `create(self, data)`,
  `update(self, obj, data)`, the handlers and the endpoint methods. Rename the
  parameter in your overrides; Pyright reports the old name. The functions in
  `fr.objects` take `model` and `data` too, not `model_cls` and
  `schema_obj`.
- The write bracket checks the references in the data after `authorize` and
  before the business method, also in nested schemas and in each item of a
  list. So a `create` that builds the object itself is checked, and so is the
  `data=` of `write_action`.
- A handler or a `write_action` inside a `write_action` block joins its
  commit, as inside `shared_write_action_commit()`. A direct
  `session.commit()` inside a block raises `RuntimeError`.
- Inside a shared commit, a write action that raises after it changed objects,
  or after it ran a statement such as `session.execute(update(...))`, stops
  the commit, also when the route catches the error. Put
  `session.begin_nested()` around each write that may fail.
- A database conflict in a write is a `fr.exc.Conflict` (409), raised by the
  write block, with the `IntegrityError` as its `__cause__`. So a handler or
  an `except` for `IntegrityError` around a block no longer sees it: catch
  `fr.exc.Conflict` instead. Inside `shared_write_action_commit()` the error
  stays raw. A handler for `fr.exc.RestlyHTTPError` now shapes conflicts too.
  Code outside a write block gets the raw `IntegrityError`.
- A replaced endpoint method gets the view's types only from the markers,
  not from its parameter names. Annotate it, such as
  `create_endpoint(self, data: ViewCreate) -> ViewResponse`. `list_params`
  still works by its name.

#### Lists

- A list has no dotted filter keys and no dotted sort keys by default, such as
  `?vendor.name=`. Turn them on with `include_filters = ("**",)` and
  `include_sort_keys = ("**",)`, or with a narrower pattern.
  `fr.query.derive_schema_list_params` has the same default. A sort key never
  goes through a to-many relationship.
- An unknown sort key gets `422`, not `400`. The `422` for an unknown query
  key is a `RequestValidationError`, not an `HTTPException`; the body is the
  same.
- The `400` texts of a list name the problem: `Unknown sort key: 'nope'`,
  `Unknown filter key: 'nope'`, or `Invalid filter value for 'age': 'old'.`
  with Pydantic's reason. A field validator that raises another exception
  gives `500`.
- On async views, `apply_list_params` is `async def`. An override must match
  its view: `async def` on an async view, `def` on a sync one.
- `apply_list_params` and `apply_filters` validate a `QueryParams` or a
  mapping through `schema_list_params` first, as the list route does. A
  mapping without `page_size` gets the default page size, so
  `get_many({})` returns the first page, not every row.
- A response field named like a pagination parameter, such as `page`, fails
  only when it gets a filter key. Leave the key out with
  `exclude_filters = ("page",)`.
- A declared `schema_list_params` must follow the view's list settings and
  filter methods, or the view fails when it registers. A subclass that
  changes `pagination` or a list setting gets new list params when its base
  uses generated ones.

#### React-admin

- React-admin views reject the list key settings, `sort_query_param` and
  `@fr.list_filter`.
- The list route of a react-admin view declares `list_params`. Overrides of
  `apply_list_params`, `apply_filters` and `to_list_response` get the parsed
  values: `sort` as `("name", "DESC")`, `range` as `(0, 24)` and `filter` as
  a dict.
- A react-admin view answers `422` instead of `400` for a `sort`, `range` or
  `filter` with the wrong shape, and for a bad `range`. A sort key or a filter
  that it cannot apply is a `fr.exc.BadQueryParam` (400).
- A react-admin view with pagination that it cannot use fails when Python
  creates the class.

#### Schemas and models

- Each field of the update body keeps its type from the view's schema, so
  `null` is valid only where the type allows `None`. `PATCH {"title": null}`
  for `title: str` gets `422` now, not `409` from the database.
- In a generated schema, a field allows `null` only when its column is
  nullable.
- The create body of a generated schema follows the model's constructor. On a
  dataclass model, a column with only an `insert_default` or a
  `server_default` is now a required field. Add `default=None` to let the
  client leave it out.
- Models compare by identity, as plain SQLAlchemy models do. Pass `eq=True`
  on a model to compare its fields. A dataclass mixin of your own declares
  `kw_only=True, eq=False, repr=False`, as `fr.IDMixin` does.
- The repr of a model on `fr.ModelDefaults` shows the loaded columns and loads
  nothing, so it no longer raises `MissingGreenlet`. Pass `repr=True` on a
  model for the dataclass repr.
- `restly new` generates the project's own `Base` in `app/models.py`.

#### Other

- `fr.clauses.UNSCOPED` is a `WhereClause` with the condition `TRUE`, so it
  goes everywhere a clause goes. `isinstance(x, WhereClause)` now matches it
  too.
- The test clients accept any 2xx status by default. Pass
  `assert_status_code` for an exact status. `assert_status_code=None` skips
  the check.

### Removed

- The view methods `make_new_object`, `update_object` and `save_object`. Use
  `super().create(data)` and `super().update(obj, data)`, and `fr.objects`
  for another model.
- `run_write_action` and `async_run_write_action`. Use `self.write_action()`.
- `fr.RefExists` and the `scope=` of a reference check. Write
  `fr.MustExist[int, Post]`. A tenant rule goes on the session. A rule for one
  caller goes in `authorize`: raise `fr.exc.NotFound` there to keep the
  404.
- `fr.MustExist[int]` without a model. Name the model:
  `fr.MustExist[int, Project]`.
- `fr.DataclassBase` and `fr.IDBase`. Declare one base of your own for the
  app:

  ```python
  class Base(fr.ModelDefaults, DeclarativeBase):
      pass


  class Post(fr.IDMixin, Base):  # was: class Post(fr.IDBase)
      title: Mapped[str]
  ```

  Mixins, such as `fr.TimestampsMixin`, go before `Base`. Use `Base` where
  you used `fr.DataclassBase`: in the models,
  `sa.Table(..., Base.metadata, ...)`, `configure_tests(base=Base)`,
  `create_all(Base)` and `target_metadata` in `alembic/env.py`. The tables
  stay the same, so Alembic finds no difference.
- The `install_default_exception_handlers` argument of `fr.configure`, and the
  `IntegrityError` handler that Restly installed. For your own body, register
  a handler for `fr.exc.Conflict` or `fr.exc.RestlyHTTPError`; the original
  error is `exc.__cause__`.
- `fr.utils` with `CurrentSettingsMixin`. Use a plain pydantic-settings class
  and a `get_settings()` function, as the project from `restly new` does.
  Replace `Settings.current` with `get_settings()` in the app factory, and
  `Settings.use(x)` in tests with an argument of the factory, as in
  `create_app(x)`.
- `route_options`. Replace the endpoint method, pass the route arguments to
  its decorator, as in FastAPI, and call `super()`.
- `extra_query_params`. Declare the key where your code reads it: with
  `@fr.list_filter`, as a parameter of the endpoint method, or as a
  dependency. For keys that are known only at run time, set
  `unknown_query_params = "ignore"`.
- `react_admin_query_params`. The keys are the fields of the view's
  `schema_list_params`.

### Fixed

- A response that nests a row of another model, such as the lines of an
  order, shows the new related row after a write set a foreign key column of
  that row. Before, it showed the old one.
- On an async view, a `to_single_response` that reads a relationship from
  `get_loader_options` no longer fails after `PATCH`, and a `with_expression`
  value is there after `POST` and `PATCH`.
- An expression in place of an id that names a column of another table, such
  as `get_one(Shelf.id == 2)` in a view of books, raises `TypeError`. Before,
  SQL joined every row of that table, so a write could change a wrong row.
- On PostgreSQL, a paginated list of a model with a `json` column works. The
  count now counts the primary keys, so it also no longer computes a
  `column_property` for every row.
- Restly no longer passes `None` to the model's constructor for a field that
  the client left out, unless the constructor requires it. So the model's
  defaults apply. Before, a left-out JSON field stored `null`, and a
  `default_factory` was lost.
- A validator for a `ReadOnly` field or for a generated primary key no longer
  needs `check_fields=False`.
- A field type with `Annotated` inside `Optional`, such as
  `fr.MustExist[UUID, Team] | None`, works like the plain type: in the list
  filters, as a reference, and in the loads of an async view.
- A schema that names itself in a to-one field no longer fails with
  `RecursionError`.
- A react-admin view checks a filter value with Pydantic. Before,
  `{"active": "false"}` matched the rows where `active` is true, and a bad
  UUID gave `500`.
- OpenAPI shows the `sort`, `range` and `filter` parameters of a react-admin
  list. A subclass of a react-admin view keeps the list params of its base.
- Pyright and mypy report an unknown name on `fr`.
- Fixes for FastAPI 0.115 with `from __future__ import annotations`, and for
  Python 3.14.

### Security

- A generated schema marks each column with `init=False` as `ReadOnly`,
  foreign keys too. Before, a client could write such a column, such as a
  tenant column, and so put a row in another organization. A client that
  still sends such a field gets no error: Restly ignores the value. To let
  the client write such a column, remove `init=False`.

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

[Unreleased]: https://github.com/rjprins/fastapi-restly/compare/v0.11.0...HEAD
[0.11.0]: https://github.com/rjprins/fastapi-restly/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/rjprins/fastapi-restly/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/rjprins/fastapi-restly/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/rjprins/fastapi-restly/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/rjprins/fastapi-restly/compare/v0.6.1...v0.7.0
[0.6.1]: https://github.com/rjprins/fastapi-restly/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/rjprins/fastapi-restly/compare/v0.5.1...v0.6.0
[0.5.1]: https://github.com/rjprins/fastapi-restly/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/rjprins/fastapi-restly/releases/tag/v0.5.0
