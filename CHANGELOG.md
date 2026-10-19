# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `fastapi_restly.views` has markers for the view's own types: `ViewID`,
  `ViewResponse`, `ViewListResponse`, `ViewCreate` and `ViewUpdate`. A route
  names them, and when the view registers, Restly puts the view's classes in
  place of them. So a route on a base view or a mixin gets the classes of
  each view that uses it. A marker works in a parameter annotation, in the
  return annotation, in `response_model=`, and inside another type, such as
  `list[ViewResponse]`. A path parameter annotated `ViewID` gets the
  converter of the id type, such as `{id:int}`. For a type checker, each
  marker is `Any`.

  ```python
  from fastapi_restly.views import ViewID, ViewResponse


  class RestoreView(fr.AsyncRestView[Any]):
      @fr.post("/{id}/restore", status_code=200)
      async def restore(self, id: ViewID) -> ViewResponse: ...
  ```
- `fr.views.ReactAdminListParams`, the list params of a react-admin view,
  with the fields `sort`, `range` and `filter`. To add a query key, such as
  a search key, subclass it and set the subclass as the view's
  `schema_list_params`.
- Four view settings choose the keys of a list: `include_filters`,
  `exclude_filters`, `include_sort_keys` and `exclude_sort_keys`. An entry is
  a key as the client sends it, or a pattern: `*` matches any characters
  except `.`, and `**` matches any characters, as in a file glob with `.` in
  place of `/`. So `include_filters = ("status", "created_at__*")` keeps the
  filter on `status` and every filter on `created_at` with a suffix. An entry
  that a view sets and that matches no key fails when the view registers, and
  the message lists the keys of the field. `include_sort_keys = ()` removes
  the `sort` parameter. `fr.query.derive_schema_list_params` takes the four
  settings as keywords.
- `sort_query_param` on a view renames the sort parameter, such as
  `sort_query_param = "order_by"`, to keep the URLs of another library.
  `fr.query.derive_schema_list_params` takes it too. The old name is then
  free: a pagination parameter or a filter of your own can be called
  `sort`. Before, `sort` was always reserved.
- `unknown_query_params = "ignore"` on a view leaves out a query key that
  the route does not read, as FastAPI does. The default, `"forbid"`, rejects
  such a key with `422`, as before. The two values are those of Pydantic's
  `extra` setting. A project base view sets it once for every view.
- `@fr.list_filter("key")` makes a view method the filter for a query key.
  The type of the method's value becomes the type of the key, so OpenAPI
  shows it and a bad value gets `422`, and the docstring becomes its
  description. The method returns a condition, a clause or `None`. Without a
  default on the value, the view calls it when the key has a value; with a
  default, on every list request. On an async view the method can be
  `async def`, to load data first. A condition on a table that is not in the
  statement raises `TypeError`, as `where=` does. The key is always on, and a
  subclass drops it by setting the method's name to `None`, such as
  `filter_popular = None`. List params of another class, such as a Pydantic
  model of your own, do not run the filter methods, and a
  `RestlySkippedFilterMethodsWarning` says so.
- `apply_filters(stmt, list_params)` on a view applies the client's filters
  without the sort and the page. A route that counts per category with
  `GROUP BY` uses it: `ORDER BY` from `apply_list_params` would fail there.
  `apply_list_params` calls it first.
- A clause can go into the condition of another clause, written without a
  call. A model's scope can then follow a relationship, such as
  `fr.where_clause(Comment.post.has(PostClauses.default_scope))`: a comment
  is visible when its post is visible. The outer clause reads the context
  members of the inner clause each time it is applied. A clause function
  cannot be placed inside an expression this way, and raises `TypeError`
  with the alternative.
- `fr.ModelDefaults` adds Restly's model defaults to a declarative base of your
  own: `class Base(fr.ModelDefaults, DeclarativeBase)`. The models get
  dataclass constructors that take keyword arguments only, snake_case table
  names, timezone-aware datetimes, enums stored as strings, and
  `awaitable_attrs`. The base keeps its own registry and `MetaData`, so it can
  set a naming convention, and its own `type_annotation_map` entries win over
  Restly's. Set dataclass options such as `eq=True` on a model: on the base
  they raise `RestlyConfigurationError`, because SQLAlchemy would apply them
  to some models only. `fr.ModelDefaults` on a class below another
  declarative base also raises.
- `fr.IDMixin` at the top level. Before, it was only in `fr.models`.
- With `fr.configure(warn_on_misuse=True)`, Restly warns about a writable
  field on a column with `init=False` and a default, in a schema that you
  write. The model sets such a column, as with a tenant or an audit column,
  so the field must be `fr.ReadOnly`. A writable relationship field warns too
  when its foreign key is such a column.

### Changed

- The write bracket checks the references in the data of a write, after
  `authorize` and before the business method. So a `create` or `update` of
  your own that builds the object itself is still checked, and so is the
  `data=` of `write_action`. The check also covers the schemas nested in the
  data, such as the lines of an order, and each item of a list. Before, only
  `make_new_object` and `update_object` checked, and only the top-level
  fields of their schema. The message names the place of a nested id:
  `Id not found for lines[1].product_id: 7`. One query checks all the ids of
  one model, and the builders in `fr.objects` take the rows from that check,
  so a write runs no extra query.
- A handler or a `write_action` inside a `write_action` block joins the
  commit of that block, as inside `shared_write_action_commit()`. The
  outermost block commits once, and then the queued `after_action_commit`
  hooks run. Before, the inner handler committed the session, with the
  changes of the outer block so far. If the outer block then raised, those
  changes stayed saved. A direct `session.commit()` inside a `write_action`
  block now raises `RuntimeError`, because the block owns the commit.
- A replaced endpoint method gets the view's types only from the markers of
  `fastapi_restly.views`. Before, registration filled them in by parameter
  name where the method had no annotation or `Any`: `id` got the id type,
  `schema_obj` got the create or update body, and the return got the
  response model. The built-in endpoint methods now use the markers, such as
  `get_one_endpoint(self, id: ViewID) -> ViewResponse`. A replaced method
  without annotations now gets no types: `schema_obj` becomes a query
  parameter, and OpenAPI shows an empty response schema. Add the markers:

  ```python
  # Before
  @fr.post("/", dependencies=[Depends(require_admin)])
  async def create_endpoint(self, schema_obj): ...

  # After
  from fastapi_restly.views import ViewCreate, ViewResponse


  @fr.post("/", dependencies=[Depends(require_admin)])
  async def create_endpoint(self, schema_obj: ViewCreate) -> ViewResponse: ...
  ```

  `list_params` still takes the view's list params by its name.
- The `422` for an unknown query key is now a `RequestValidationError`, as
  for a bad query value, so an exception handler for
  `RequestValidationError` gets it too. Before, it was an `HTTPException`.
  The response body is the same.

- A list has no dotted filter keys and no dotted sort keys by default, such
  as `?vendor.name=` or `?sort=vendor.name`. Clients that send them now get
  `422`. Turn them on with `include_filters = ("**",)` and
  `include_sort_keys = ("**",)`, or with a narrower pattern such as
  `("*", "vendor.*")`. Each dotted key is a join that a client can add, and a
  new field on a nested schema would add keys to every list that nests it.
  `fr.query.derive_schema_list_params` has the same default. Its model
  ignores a key that it does not have, so a route of your own that uses it
  now ignores a dotted key without an error. Pass `include_filters=("**",)`
  and `include_sort_keys=("**",)` to keep them.
- A sort key that the list does not have now gets `422` from the list
  params, not `400`. The description of `sort` in OpenAPI lists the sort
  keys. A sort key never goes through a to-many relationship.
- On `AsyncRestView` and `AsyncReactAdminView`, `apply_list_params` is now
  `async def`: write `await self.apply_list_params(...)`. An override must
  be `async def` and call `await super().apply_list_params(...)`. A plain
  `def` override fails when the class is defined, with a message that says
  so. On a sync view, an `async def` override fails the same way.
- `apply_list_params` and `apply_filters` on a view validate a
  `QueryParams` or a mapping through `schema_list_params` first, as the list
  route does. A bad `sort` or `page_size`, or a bad value for a filter of the
  view's own, raises `RequestValidationError`, which is a `422`. A key that
  the list params do not have raises it too, as on the list route. Only in a
  `QueryParams`, a key that the route reads, such as `?mode=`, is left out.
  A bad value for a generated filter still raises
  `BadQueryParam`, a `400`, as on the list route. A mapping without
  `page_size` gets the default page size, so `view.get_many({})` now returns
  the first page, not every row.
- A response field named like a pagination parameter, such as `page`, fails
  only when it gets a filter key. Leave the key out with
  `exclude_filters = ("page",)`. Before, such a field always failed when the
  view registered.
- A view with a declared `schema_list_params` fails when it registers if the
  list params do not follow its list settings (the four key settings and
  `sort_query_param`) and its filter methods. A view that inherits a setting
  from a base view sets it again, to the value that the list params were
  created with. Hand-written list params take no list settings and no
  filter methods. The message says what to change.
- A subclass that changes `pagination`, a list setting or a filter method
  gets new `schema_list_params` when its base declares the list params that
  Restly would generate for the base, made with
  `fr.query.derive_schema_list_params`, as a subclass with a new `schema`
  does. Before, the subclass failed when it registered. Other declared list
  params, such as ones from a narrower schema, stay, and the subclass still
  fails.
- React-admin views reject the four key settings, `sort_query_param` and
  `@fr.list_filter` when they register. React-admin sends its filters in its
  own `filter` parameter. A react-admin view that inherits a setting from a
  base view sets the default again, such as `exclude_filters = ()`.
- The list route of a react-admin view declares `list_params`, as every list
  route does. Its `schema_list_params` is `fr.views.ReactAdminListParams`.
  The fields `sort`, `range` and `filter` are `pydantic.Json`, so Pydantic
  parses the JSON of each key. `apply_list_params`, `apply_filters` and
  `to_list_response` read the parsed values from these list params: `sort`
  as `("name", "DESC")`, `range` as `(0, 24)` and `filter` as a dict. So an
  override of these methods now gets the list params. Before, it got a
  private object with the same values. A mapping that you pass to these
  methods holds query values, so `sort`, `range` and `filter` are JSON text,
  as on the other views. A route of your own on a react-admin view gets the
  list params when it declares `list_params`. List params that are not a
  subclass of `ReactAdminListParams` fail when Python defines the class.
- A react-admin view with pagination that it cannot use now fails when
  Python defines the class, not when the view registers. This is
  `pagination = None`, or pagination settings other than the two page sizes.
- The test clients now accept any success status (2xx) by default. Before,
  each method expected one status, such as `201` for POST. So a POST action
  that returns `200` now passes without `assert_status_code`. No test starts
  to fail, but some checks are looser: to test for an exact status, such as
  `201` on create, pass it.
- `assert_status_code=None` now skips the status check. Before, it accepted
  any status below 400.
- `fr.clauses.UNSCOPED` is now a `WhereClause`. Its condition is SQL `TRUE`,
  so it goes everywhere a clause goes: calling it returns `sqlalchemy.true()`,
  and `Model.relationship.has(UNSCOPED)` works. The type
  `WhereClause | Unscoped` is now just `WhereClause`, and `fr.all_of`,
  `fr.any_of` and `fr.none_of` always return a `WhereClause`. So pyright now
  accepts `Comment.post.has(PostClauses.default_scope)`. Code that uses
  `isinstance(x, WhereClause)` to mean "a real filter" now also matches
  `UNSCOPED`: test `x is fr.clauses.UNSCOPED` first.
- Models compare by identity, as SQLAlchemy models without dataclasses do:
  two objects are equal only when they are the same object. This holds for
  `fr.ModelDefaults`, `fr.IDMixin` and `fr.TimestampsMixin`. Before, two
  objects of a model on `fr.DataclassBase` were equal when their fields were
  equal, and a model object could not go in a `set`. To compare a model by
  its fields again, pass `eq=True` on it: `class Post(fr.IDMixin, Base, eq=True)`.
  A dataclass mixin of your own declares the same options as `fr.IDMixin`:
  `class Stamped(MappedAsDataclass, kw_only=True, eq=False, repr=False)`. A
  mixin without `eq=False` makes each model that uses it compare fields, so
  `fr.ModelDefaults` raises `RestlyConfigurationError` for such a model,
  unless the model passes `eq=True` or writes its own `__eq__`.
- The repr of a model on `fr.ModelDefaults` no longer loads anything. It shows
  the primary key and the other columns that are loaded, such as
  `Post(id=1, title='Hello')`. `...` stands for columns that are not loaded,
  and relationships are left out. Before, the dataclass repr read every field,
  so in async code it raised `MissingGreenlet` for a relationship or a column
  that was not loaded. Pass `repr=True` on a model for the dataclass repr. A
  dataclass mixin of your own adds `repr=False`. Without it, the model gets a
  dataclass repr, so `fr.ModelDefaults` raises `RestlyConfigurationError`,
  unless the model passes `repr=True` or writes its own `__repr__`. A model
  that uses `fr.IDMixin` or `fr.TimestampsMixin` on a base without
  `fr.ModelDefaults` gets Python's default repr.
- `restly new` generates `app/models.py` with the project's own
  `class Base(fr.ModelDefaults, DeclarativeBase)`. The models, `alembic/env.py`,
  the test setup and `create_all` use that `Base` instead of
  `fr.DataclassBase`.
- A database conflict in a write is now a `fr.exc.Conflict` (409), raised by
  Restly's write block instead of an exception handler on the app. A CRUD
  write or a `write_action` block in a custom route turns an
  `IntegrityError` into `Conflict`, with the `IntegrityError` as its
  `__cause__`. Inside a `shared_write_action_commit()` block the error stays
  raw, so a bulk route can still catch the error of one row. An
  `IntegrityError` that escapes the outermost block, or that its commit
  raises, becomes the `409`. The default body
  is the same as before. With a handler for `fr.exc.RestlyHTTPError`, such as
  a problem details handler, conflicts now get that handler's body. Code
  outside a write block, such as a bare `View` route that commits itself,
  now gets the raw `IntegrityError` instead of a `409`. A route that caught
  `IntegrityError` around its own `write_action` block catches
  `fr.exc.Conflict` instead.
- Each field of the update body keeps the type that it has in the view's
  schema. The client may still leave out every field, but `null` is valid
  only where the type allows `None`, as in the create body. Before, the
  update body made each type `Optional`. So `PATCH {"title": null}` for a
  field `title: str` passed validation, and the database rejected it with
  `409`. Now it returns `422`. A field validator in the default `after` mode
  no longer gets `None` for such a field. A `before` or `wrap` validator
  still gets the client's `null`, as in the create body. A field typed `str`
  on a nullable column can no longer be set to `null` with `PATCH`; type it
  `str | None` for that. In OpenAPI, `title` in the update body is `string`
  instead of `string | null`.
- In a schema that Restly generates, for a view without `schema`, a field
  allows `null` only when its column is nullable. Before,
  `count: Mapped[int] = mapped_column(default=3)` got the field
  `int | None`. Then `POST {"count": null}` stored the default, and
  `PATCH {"count": null}` returned `409` from the database. Both now return
  `422`. The client may still leave the field out, and then the default
  applies. In OpenAPI, the field is `integer` instead of `integer | null`.
  `mapped_column(nullable=...)` now wins over the annotation, as in
  SQLAlchemy: `Mapped[int]` with `nullable=True` allows `null`.
- The create body of a generated schema follows the model's constructor, as
  SQLAlchemy does. The client may leave out a field when its column is
  nullable, or when the constructor does not require it and the model then
  still gives the column a value. On a dataclass model, a column with only
  an `insert_default` or a `server_default` is a required argument of the
  constructor, so its field is now required too. Add `default=None` to let
  the client leave it out: `mapped_column(server_default="x", default=None)`.
  A `default_factory`, or a `default` other than `None`, also lets the client
  leave the field out. On a JSON column, use `default_factory=list` or
  `JSON(none_as_null=True)` instead: SQLAlchemy 2.0 stores a `None` default
  as JSON `null`, so Restly keeps that field required. A `Computed` column
  needs `init=False`, which makes its field `ReadOnly`.
- The `400` answers of a list request say what is wrong. Before, the
  generated list params said "Invalid attribute in URL query: age" both for a
  sort or filter field that does not exist and for a bad filter value. Now
  it is `Unknown sort key: 'nope'`, `Unknown filter key: 'nope'`, or
  `Invalid filter value for 'age': 'old'.` followed by Pydantic's reason,
  such as `Input should be a valid integer, unable to parse string as an
  integer`. An exception from a field validator that is not a validation
  error is now a `500`, as for a request body, instead of a `400`. Code or
  tests that compare the old texts must change. The texts for a bad `page`
  or `page_size` changed too.
- A react-admin view answers `422` instead of `400` for JSON in `sort`,
  `range` or `filter` that does not have their shape. So does a `range` with
  a negative start, an end before its start, an end past the largest offset
  that the database takes (before, a `500`), or more rows than
  `max_page_size`. The error names the place, such as
  `["query", "sort", 1]`, as for a bad value of other list params. A sort
  key or a filter that the view cannot apply is still a `400`. It is now a
  `fr.exc.BadQueryParam`. Before, it was a plain `HTTPException`, so a
  handler for `fr.exc.RestlyHTTPError` did not shape it. An unknown sort key
  now says `Unknown sort key` instead of `Unknown filter field`.
- A react-admin view checks a filter value against the column's type with
  Pydantic, as the generated list params do. Before, it called the type on the
  value. So `{"active": "false"}` matched the rows where `active` is true,
  a date such as `"2020-01-01"` returned `400`, and a bad decimal or UUID
  returned `500`. A bad value now returns `400` with Pydantic's reason, and
  names the field as the client wrote it, also with an alias.

### Removed

- `fr.RefExists`, and with it the `scope=` argument of a reference check.
  Write a reference as `fr.MustExist[int, Post]`. It finds the rows of the
  model's `default_scope`, under the rules of the session. A tenant rule goes
  on the session, as the multi-tenancy guide shows. A rule for one caller or
  one action goes in `authorize`. `fr.RefExists` now raises `AttributeError`
  with this advice.
- `fr.MustExist[int]` without a model. It took the model from the foreign
  key of the column on the model that is written. But a schema can also
  write another model, as the `data=` of `write_action` can, and then the
  check used the wrong table. Name the model: `fr.MustExist[int, Project]`.
  The one-argument form raises `TypeError`, and Pyright and mypy report it
  too.
- `fr.DataclassBase` and `fr.IDBase`. Restly no longer owns a declarative
  base. Every app that used them shared one registry and one `MetaData`
  inside Restly. So the tables of one package ended up in the migrations of
  another, and to add a naming convention you had to change Restly's object.
  Declare one base of your own for the whole app instead. Put it in one
  module, and import it from there in every module with models. The tables,
  their names and their column types stay the same, so Alembic finds no
  difference:

  ```python
  from sqlalchemy.orm import DeclarativeBase

  class Base(fr.ModelDefaults, DeclarativeBase):
      pass

  class Post(fr.IDMixin, Base):  # was: class Post(fr.IDBase)
      title: Mapped[str]
  ```

  Then use `Base` everywhere you used `fr.DataclassBase` or `fr.IDBase`:

  - `class Tag(fr.DataclassBase)` becomes `class Tag(Base)`.
  - `sa.Table("post_tag", fr.DataclassBase.metadata, ...)` becomes
    `sa.Table("post_tag", Base.metadata, ...)`.
  - `configure_tests(base=Base)`, `create_all(Base)` and
    `async_create_all(Base)`.
  - `target_metadata = Base.metadata` in `alembic/env.py`.
- The `install_default_exception_handlers` argument of `fr.configure`, and the
  `IntegrityError` handler that `fr.configure(app)` and `fr.include_view`
  installed on the app. The argument did not work: `include_view` installed
  the handler anyway. Restly no longer installs an exception handler, so
  there is nothing to turn off. To give conflicts your own body, register a
  handler for `fr.exc.RestlyHTTPError` or `fr.exc.Conflict`. A handler for
  `IntegrityError` of your own, as the 0.11 docs showed, no longer runs for a
  conflict in a CRUD write or a `write_action` block, because the block raises
  `fr.exc.Conflict` first. Register it for `fr.exc.Conflict` instead; the
  original error is `exc.__cause__`.
- `fr.utils.CurrentSettingsMixin`, and the `fr.utils` module with it. An app
  reads its settings in only two places, the application factory and
  `alembic/env.py`, so it does not need a shared instance. Use a plain
  `pydantic-settings` class with a `get_settings()` function, and give the
  factory an optional `settings` argument:

  ```python
  class Settings(BaseSettings):  # was: (fr.utils.CurrentSettingsMixin, BaseSettings)
      database_url: str


  def get_settings() -> Settings:
      return Settings()  # type: ignore[call-arg]


  def create_app(settings: Settings | None = None) -> FastAPI:
      if settings is None:
          settings = get_settings()  # was: settings = Settings.current
      ...
  ```

  Pyright and mypy report `Settings()` as a call with a missing argument,
  because they do not know that pydantic-settings reads `database_url` from
  the environment. `get_settings()` keeps that `# type: ignore` in one place,
  as in the project that `restly new` makes. A test passes its own settings
  to the factory: `app = create_app(Settings(database_url=..., _env_file=None))`,
  in place of `Settings.use(...)` and then `create_app()`. In `alembic/env.py`,
  use `get_settings().database_url` in place of `Settings.current.database_url`.
- `route_options` on a view. Override the endpoint method instead, and pass
  the route arguments to its decorator, as in FastAPI. The method calls the
  method of the base class, so the route works as before, and Restly still
  fills in the response model and the list params:

  ```python
  class InvoiceView(fr.AsyncRestView):
      # was: route_options = {fr.ViewRoute.GET_MANY: {"summary": "List invoices"}}
      @fr.get("/", summary="List invoices", operation_id="billing_list")
      async def get_many_endpoint(self, list_params):
          return await super().get_many_endpoint(list_params)
  ```

  `route_options` was a second place for the route arguments, and a type
  checker could not check its keys and values. A subclass that set it
  replaced the whole mapping of its base, so a `dependencies` entry for one
  route could disappear without an error. A view that still sets
  `route_options` raises `RestlyConfigurationError`.
- `extra_query_params` on a view. The unknown-key check accepts every query
  key that the route declares. So declare a key of your own where your code
  uses it. Then it has a type, and OpenAPI shows it. Use the place that fits
  what the key does:

  - A key that filters the rows: `@fr.list_filter("key")` on a view method.
  - A key that changes the response: a parameter of the endpoint method, as
    in `async def get_many_endpoint(self, list_params, as_text: bool = False)`.
  - A value that other methods or the scope need: a dependency, in a class
    attribute with `Depends(...)`, or `Current.depends(...)`.
  - Keys that are only known at run time, such as a family `extra.<name>`
    that your code reads from `self.request`: `unknown_query_params = "ignore"`.

  React-admin views follow the same rules. A view that still sets
  `extra_query_params` raises `RestlyConfigurationError`.
- `react_admin_query_params` on the react-admin views. The three keys are the
  fields of the view's `schema_list_params`.

### Fixed

- Pyright and mypy report an unknown name on `fr`, such as `fr.NotAThing`.
  Before, the package's module `__getattr__`, which loads `fr.testing` when
  it is first used, made both accept every name.
- An expression in place of an id that names a column of another table,
  such as `get_one(Shelf.id == 2)` in a view of books, raises `TypeError`.
  Before, SQL joined every row of that table, so `get_one`, `handle_update`
  and `handle_delete` could load, change or delete a wrong row. The only
  sign was a SQLAlchemy warning. For a condition on a related row, use
  `.has()` or `.any()`.
- On PostgreSQL, a paginated list of a model with a `json` column works.
  Before, the count of the list failed with "could not identify an equality
  operator for type json". This happened with every `sqlalchemy.JSON`
  column. A `JSONB` column was not affected. The count ran `DISTINCT` over
  every column of the model, and PostgreSQL cannot compare `json` values.
  Now the count selects only the primary key of the model that the list
  selects. So the count also no longer runs the subquery of a
  `column_property` for every row. For a model with concrete table
  inheritance, the count also selects the discriminator, because each table
  numbers its own rows.
- On FastAPI 0.115, a module with `from __future__ import annotations` can
  now declare a route on a mixin or a parent view, and a custom list route
  that takes `list_params`. Before, when the annotations of such a route
  named a schema, an enum or a type alias from that module, the view failed
  when it registered, or a valid request returned 422. FastAPI 0.128 and
  later were not affected.
- On Python 3.14, a `get_many_endpoint` keeps the return type that its
  author wrote when the view inherits it from a mixin, or from a base view
  that is not registered. Before, registration replaced it with the list
  response that Restly generates. OpenAPI showed the wrong response, and a
  request failed with a `ResponseValidationError`.
- Restly calls the model's constructor with the fields that the client sent,
  as code that builds the object itself would. A field that the client
  leaves out, with the value `None`, is no longer passed, unless the
  constructor requires it. So the model's default applies. Before, Restly
  passed `None`: most columns still got their column default, but a JSON
  column stored JSON `null`, and a `default_factory` of the constructor was
  lost. This also holds for a reference field, such as
  `author_id: fr.IDRef[User] | None = None`, on a foreign key column.
- A validator or serializer for a `ReadOnly` field, or for a primary key that
  the server generates, no longer makes the view fail when it registers.
  Before, it needed `check_fields=False`. The request bodies do not have the
  field, so they do not run the validator. The response class has the field
  and runs it.
- A field type with `Annotated` inside `Optional`, such as
  `Annotated[fr.IDRef[User], Field(...)] | None`, `constr(...) | None` or
  `fr.MustExist[UUID, Team] | None`, now works like the plain type. Before,
  Restly did not look inside the `Annotated`:
  - The list filters gave such a field the range filters (`__gte` and the
    others) for every type. On a bool they returned `500`. A UUID, a plain
    `Enum` or an `IDRef` no longer gets them, as with the plain type, so a
    client that sends one now gets `422`. Such a string now gets `__contains`
    and `__icontains`. Such a nested schema now gets the dotted filters and
    sort keys, such as `?author.name=Alice`, when the view turns them on.
  - Restly did not see such an `IDRef` or `IDSchema` as a reference. A field
    named after a foreign key column returned `500` on every create or update
    that sent it. A field named after a relationship returned `500` on create
    when the foreign key is a required argument of the model. A foreign key
    and its relationship that name different rows were saved, instead of
    returning `422` ("Conflicting references"). OpenAPI did not name the
    referenced resource of a field named after a relationship, and
    `warn_on_misuse` did not warn about an `IDRef` on a foreign key column.
  - An async view returned `500` when such a nested schema had a nested
    schema of its own, because Restly did not load the second level.
- A schema that names itself in a to-one field, such as
  `parent: CategorySchema | None` inside `CategorySchema`, no longer makes
  the view fail with `RecursionError` when it registers. The list filters
  follow it one step: with dotted keys on, `?parent.name=` works, and
  `?parent.parent.name=` does not.
- OpenAPI shows the `sort`, `range` and `filter` parameters of the list
  route of a react-admin view, each with a description of its JSON. Before,
  it showed no query parameters for this route.
- A subclass of a react-admin view keeps the `schema_list_params` that its
  base view declares, such as list params with a search key. Before, it got
  the three keys of react-admin again.

### Security

- A schema that Restly generates, for a view without `schema`, now marks
  each column with `init=False` as `ReadOnly`. Before, the client could write
  such a column. For a tenant column with `insert_default=Current.org_id`, a
  create could put the row in another organization, and a PATCH could move
  it there. A column that the constructor does not take is set by the model
  or by the server: with a column default, a mapper event or the view's
  `create`. So the request bodies no longer have these columns.

  This also applies to a foreign key with `init=False`, such as `author_id`
  next to an `author` relationship. A generated schema no longer lets the
  client set it. A client that still sends it gets no error: Restly ignores
  the value, as for every `ReadOnly` field. To let the client write such a
  column, remove `init=False`, so that the constructor takes it.

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
