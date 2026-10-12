# Schemas & Validation

:::{note}
FastAPI-Restly uses **schema** for Pydantic request/response models and
**model** for SQLAlchemy ORM models. A `User` model is the database object;
`UserSchema`, the view's schema, defines the public API.
:::

Use explicit schemas when you need a stable public contract: aliases, hidden
fields, computed read-only fields, or relationship IDs. If you omit {attr}`schema <fastapi_restly.views.BaseRestView.schema>` on
a view, Restly can
[auto-generate one](#auto-generated-schemas) from the
SQLAlchemy model instead.

To choose a schema tool, match your goal in the table below; each entry links
to the section that covers it.

| Goal | Tool |
|---|---|
| Stable public contract | [Explicit schema](#auto-generated-vs-explicit-schemas) |
| Input field hidden from responses | [`WriteOnly`](#readonly-and-writeonly) |
| Server-owned field, ignored on input | [`ReadOnly`](#readonly-and-writeonly) |
| Field set on create, frozen afterwards | [Explicit write schema](#explicit-write-schemas) |
| Response that differs from the input | [Your own response class](#own-response-class) |
| Checked foreign-key column | [`MustExist`](#mustexist) |
| Relationship as a flat id | [`IDRef`](howto_relationship_idschema.md#choosing-a-reference-style) |
| Relationship as a nested object | [`IDSchema[Model]`](#nested-relationship-objects) |
| Clear type names in OpenAPI and generated clients | [Name your schemas](#name-your-schemas) |

## BaseSchema

{class}`fr.BaseSchema <fastapi_restly.schemas.BaseSchema>` is Restly's Pydantic base class. It enables Pydantic's
`from_attributes=True`, which lets a schema validate SQLAlchemy ORM
objects directly.

Behaviorally, it is equivalent to:

```python
class BaseSchema(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(from_attributes=True)
```

(The real class also installs the import-time check that rejects nested
`ReadOnly`/`WriteOnly` markers, described in
[ReadOnly and WriteOnly](#readonly-and-writeonly).)

The inherited CRUD endpoint methods still serialize ORM objects through
{meth}`self.to_single_response(obj) <fastapi_restly.views.BaseRestView.to_single_response>`. That is where Restly applies response-specific
behavior such as `WriteOnly` filtering and relationship-id normalization.

Use `BaseSchema` when you want to declare every field yourself, including `id`:

```python
class UserSchema(fr.BaseSchema):
    id: int
    name: str
    email: str
```

This keeps the `id` field explicit and visible in the schema definition. You are
then responsible for marking it read-only if you do not want it accepted in
create/update payloads.

## IDSchema

The view's schema usually inherits from {class}`fr.IDSchema <fastapi_restly.schemas.IDSchema>`. It is essentially
{class}`BaseSchema <fastapi_restly.schemas.BaseSchema>` with a read-only `id` field added:

```python
class IDSchema(fr.BaseSchema):
    id: fr.ReadOnly[Any]
```

That is why examples usually look like this:

```python
class UserSchema(fr.IDSchema):
    name: str
    email: str
```

The `id` appears in responses but is excluded from generated POST and PATCH
input schemas. You do not need to redeclare it unless you want a different type
or different field metadata.

## Timestamps

Use {class}`fr.TimestampsSchemaMixin <fastapi_restly.schemas.TimestampsSchemaMixin>` when a schema should include read-only
`created_at` and `updated_at` fields:

```python
class UserSchema(fr.TimestampsSchemaMixin, fr.IDSchema):
    name: str
```

## ReadOnly and WriteOnly

`fr.ReadOnly[T]` marks a field as response-only. It is removed from the
generated create and update inputs:

```python
class UserSchema(fr.IDSchema):
    name: str
    created_by_id: fr.ReadOnly[int]
```

`fr.WriteOnly[T]` marks a field as request-only. It is accepted in create and
update payloads but excluded from every serialized response:

```python
class UserSchema(fr.IDSchema):
    email: str
    password: fr.WriteOnly[str]
```

The two markers take effect in different places, which matters when a schema
is used outside a view. `WriteOnly` sets Pydantic's field-level `exclude`, so
every serialization of the schema drops the field: CRUD responses, FastAPI's
`response_model`, and your own `model_dump()` calls all strip it, recursively
through nested schemas, and the documented response schema in OpenAPI omits it.
`ReadOnly` is applied by Restly itself when it generates
{attr}`schema_create <fastapi_restly.views.BaseRestView.schema_create>` and
{attr}`schema_update <fastapi_restly.views.BaseRestView.schema_update>`, and
when its object helpers construct or update ORM objects. On a schema used
directly, a `ReadOnly` field validates and serializes like any other field;
only its OpenAPI schema is marked `readOnly`.

(explicit-write-schemas)=

An explicit
{attr}`schema_create <fastapi_restly.views.BaseRestView.schema_create>` or
{attr}`schema_update <fastapi_restly.views.BaseRestView.schema_update>` is the
whole contract for its request. Restly writes every field it declares, except
those it marks `ReadOnly` itself. The markers on the view's schema only shape
the write schemas Restly generates. So a field that the view's schema marks
`ReadOnly` and an explicit `schema_create` declares is set on create and frozen
afterwards:

```python
class CommentSchema(fr.IDSchema):
    body: str
    story_id: fr.ReadOnly[int]


class CommentCreate(fr.BaseSchema):
    body: str
    story_id: fr.MustExist[int, Story]


class CommentView(fr.AsyncRestView):
    prefix = "/comments"
    model = Comment
    schema = CommentSchema
    schema_create = CommentCreate
```

`POST /comments` sets `story_id`. The generated `schema_update` leaves it out,
so `PATCH` ignores it. Keep a server-stamped field, such as a tenant id, out of
every explicit write schema: a client could set it otherwise.

Either marker must be the field's outer annotation. Nested inside a union or a
container (`Optional[WriteOnly[str]]`, `WriteOnly[str] | None`,
`list[WriteOnly[str]]`) the marker would silently do nothing, so Restly
rejects such a field with a `RestlyConfigurationError`. For an optional
write-only field, write `WriteOnly[Optional[str]]`.

For `WriteOnly` fields inside a JSON document, use an explicit column type.
See [JSON document columns](#json-document-columns).

(own-response-class)=
## Your own response class

By default, a response is the view's schema without its `WriteOnly` fields.
When what goes out differs more from what comes in, set
{attr}`schema_response <fastapi_restly.views.BaseRestView.schema_response>`
on the view. Everything that goes out follows it:

- every single response, and each item of the list response;
- the fields that the list params filter and sort on, so a client can filter
  only on what it can see;
- the relationships that the view loads.

The request bodies still follow `schema_create` and `schema_update`, which
Restly derives from `schema`:

```python
class AccountSchema(fr.IDSchema):
    name: str
    email: str
    team_id: int | None = None


class AccountResponse(fr.IDSchema):
    name: str
    team: TeamSchema | None = None


class AccountView(fr.AsyncRestView):
    prefix = "/accounts"
    model = Account
    schema = AccountSchema
    schema_response = AccountResponse
```

`POST /accounts` takes `name`, `email` and `team_id`. Every response shows
`id`, `name` and the nested `team`, which the view loads. `?email=...` is not
a filter, because a client does not see `email`. Restly uses the class as you
wrote it: it does not remove `WriteOnly` fields from it. If a
`to_single_response` override returns an instance of `AccountSchema`, Restly
validates it into `AccountResponse`, so only the fields of `AccountResponse`
go out.

Name the class `<Resource>Response`, as in `AccountResponse`. Restly names the
list response and the list params after it: `AccountListResponse` and
`AccountListParams`. For type checkers, put the class in the second parameter
of the view, as in `fr.AsyncRestView[Account, AccountResponse]`; see
[Typing](howto_typing.md).

By default the response class is the view's schema without its `WriteOnly`
fields. Restly builds it also when the schema has no `WriteOnly` fields, so
OpenAPI always shows `UserResponse`. A view can also set its own response class; see
[Your own response class](#own-response-class). The list response is the
pagination's envelope, filled with the response class. A react-admin view has
no list response class: its list route returns a plain JSON list.

## Aliases

Use normal Pydantic aliases when the API field name differs from the Python or
database attribute:

```python
from pydantic import Field


class UserSchema(fr.IDSchema):
    first_name: str = Field(alias="firstName")
    email: str
```

Incoming payloads can use `firstName`, and Restly responses use the alias on
Restly routes.

## MustExist

Use {class}`fr.MustExist[int, Model] <fastapi_restly.schemas.MustExist>` for foreign-key columns (primary-key type first, then the target model):

```python
class ArticleSchema(fr.IDSchema):
    title: str
    author_id: fr.MustExist[int, Author]
```

In JSON, the field is a scalar id:

```json
{
  "title": "Intro",
  "author_id": 1
}
```

Restly validates that the referenced `Author` exists and keeps the plain id; in
hooks, `data.author_id` is the plain integer. See [Work with Foreign Keys and
Relationships](howto_relationship_idschema.md) for the full model and view setup.

## Nested relationship objects

If a client expects a nested relationship object, use {class}`fr.IDSchema[Model] <fastapi_restly.schemas.IDSchema>` as a
field type:

```python
class ArticleSchema(fr.IDSchema):
    title: str
    author: fr.IDSchema[Author]
```

In JSON, it looks like this:

```json
{
  "title": "Intro",
  "author": {"id": 1}
}
```

This is useful for clients or integrations that model relationships as objects.
For the same relationship as a flat id, use {class}`fr.IDRef[Model] <fastapi_restly.schemas.IDRef>`;
for a plain foreign-key column, use {class}`fr.MustExist[int, Model] <fastapi_restly.schemas.MustExist>`.
See [Nested Relationship
Objects](howto_relationship_idschema.md#nested-relationship-objects) for how
this shape compares with the flat-id form.

## Auto-Generated vs Explicit Schemas

Auto-generated schemas are useful when your database model is already close to
your API contract:

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
```

Use explicit schemas when you need aliases, read/write field control,
relationship references, or a public API shape that intentionally differs from
the SQLAlchemy model:

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserSchema
```

(name-your-schemas)=
## Name your schemas

OpenAPI keeps all classes in one list, by name. It has no modules. A
generated client uses these names as type names. So each class in your API
needs its own name, also when Python keeps two classes apart in different
modules.

Restly names the classes it generates after the view's schema. For
`UserSchema`, OpenAPI shows `UserResponse`, `UserCreate`, `UserUpdate` and
`UserListResponse`; see [Generated Class Names](#generated-class-names). A
custom route names the same classes; see
[A custom route names the same classes](#name-the-response-classes).
Follow these rules for the classes you write:

- Name the view's schema after the resource that the client sees: `UserSchema`,
  or `AdminUserSchema` for an admin view of users. Two schemas named
  `UserSchema` in different modules clash.
- Use the names `UserResponse`, `UserCreate` and `UserUpdate` only for a
  view's response class and its create and update body, and set the class on
  the view:

  ```python
  class TaskCreate(fr.BaseSchema):
      title: str


  class TaskView(fr.AsyncRestView):
      prefix = "/tasks"
      model = Task
      schema = TaskSchema
      schema_create = TaskCreate
  ```

  A class for another purpose, such as the body of a bulk route, gets a name
  that says what it is: `TaskBulkItem`, `UserSummary`.
- Two views that share a schema also get the same names for the classes that
  Restly generates. When the classes differ, for example because only one view is paginated,
  give one view a schema of its own:

  ```python
  class AllTasksSchema(TaskSchema):
      pass
  ```

  The list response takes its name from the response class. So when the two
  views set the same `schema_response`, give one view a response class of
  its own in the same way.

When two different classes still have the same name, OpenAPI shows both under
long names that can change, such as `app__tasks__views__TaskCreate`. Restly
then warns with a
{class}`RestlyDuplicateSchemaNameWarning <fastapi_restly.exc.RestlyDuplicateSchemaNameWarning>` that
names the classes and the views that use them. It checks an app that you
pass to `fr.configure(app, ...)` or `fr.testing.configure_tests(app=...)`, or
that you include a view on. Two classes with the same name are not a problem
when their JSON schemas are the same, also their docstrings and defaults:
OpenAPI then shows them as one.

Two views with the same schema and the same pagination show one set of names.
Two different classes can still get the same name. For example, two views
share a schema but only one is paginated, or you wrote a `UserCreate` that is
not the same as the generated one. Pydantic then shows both classes under long
names built from the module path, such as `app__users__views__UserCreate`.
When the module path is the same too, it numbers them, and the numbers follow
the order in which the views are registered. When the app builds its OpenAPI
spec, Restly checks for this and warns with a
{class}`RestlyDuplicateSchemaNameWarning <fastapi_restly.exc.RestlyDuplicateSchemaNameWarning>` that
names the classes and the views. Restly checks an app that you pass to
{func}`fr.configure(app, ...) <fastapi_restly.db.configure>` or
{func}`configure_tests(app=...) <fastapi_restly.testing.configure_tests>`, or
that you include a view on. [Name your schemas](#name-your-schemas) shows how
to avoid it.

(generated-class-names)=
## Generated Class Names

A class that Restly generates is named `<Resource><Role>`. Resource is the
class name of the view's schema without a final `Schema` or `Response`. For
the view's schema `UserSchema`, the resource is `User`:

| Role | Class | View attribute |
|---|---|---|
| response | `UserResponse` | {attr}`schema_response <fastapi_restly.views.BaseRestView.schema_response>` |
| create body | `UserCreate` | {attr}`schema_create <fastapi_restly.views.BaseRestView.schema_create>` |
| update body | `UserUpdate` | {attr}`schema_update <fastapi_restly.views.BaseRestView.schema_update>` |
| list response | `UserListResponse` | none |
| list params | `UserListParams` | {attr}`schema_list_params <fastapi_restly.views.BaseRestView.schema_list_params>` |

A client sees these names in OpenAPI. It does not see the view's schema,
unless another schema nests it or a custom route names it. The list params
are not a class in OpenAPI either: OpenAPI shows them as separate query
parameters.

The list response has no view attribute, because it has nothing of its own
to set: it is the envelope of the view's
{attr}`pagination <fastapi_restly.views.BaseRestView.pagination>`, filled with
the response class. To change it, change one of those two.

(name-the-response-classes)=
### A custom route names the same classes

A route decorator runs in the class body, before Restly builds the response
classes. So a custom route gets the classes from two functions, at module
level. Each returns the same class that the view uses:
{func}`derive_schema_response(schema) <fastapi_restly.schemas.derive_schema_response>`
and
{func}`derive_schema_list_response(schema_response, pagination=...) <fastapi_restly.schemas.derive_schema_list_response>`.
Pass the view's pagination as it is, also when it is `None`:

```python
UserResponse = fr.schemas.derive_schema_response(UserSchema)
UserListResponse = fr.schemas.derive_schema_list_response(
    UserResponse, pagination=AppView.pagination
)


@fr.include_view(app)
class UserView(AppView):
    prefix = "/users"
    model = User
    schema = UserSchema

    @fr.get("/inactive", response_model=UserListResponse)
    async def inactive(self, list_params):
        result = await self.handle_get_many(list_params, where=User.active.is_(False))
        return self.to_response(result, fr.ResponseShape.LIST)

    @fr.post("/{id}/activate", response_model=UserResponse)
    async def activate(self, id: int):
        user = await self.get_one(id)
        async with self.write_action("activate", obj=user):
            user.active = True
        return self.to_response(user)
```

OpenAPI then shows `UserListResponse` and `UserResponse` for these routes,
as for the CRUD routes. A function builds these classes, so type checkers do
not accept them in an annotation, as in `data: UserResponse`. mypy also does
not accept them inside a type in other places, as in
`response_model=list[UserResponse]`. At runtime they are normal classes, so
add `# type: ignore[valid-type]` on such a line. A view with its own
`schema_response` passes that class to `derive_schema_list_response` instead.
A custom route that names `UserSchema` or `PaginatedEnvelope[UserSchema]`
sends the same JSON, but shows a type of its own in OpenAPI. If a route passes
a pagination with another envelope than the view's, its list response has
another shape, for example `data` without `total_count`. There are then two
classes named `UserListResponse`, and Restly warns as described below.

A view without a `schema` gets a generated one. For a model named `User` it
is `UserSchema`, so the names above stay the same. A schema with another name
keeps its full name as the resource: `UserRead` gives `UserReadCreate`.


(json-document-columns)=
## JSON document columns

A `JSON` column maps to a bare `dict`, which does not validate the document's
fields. To validate its shape, declare an explicit schema with a nested
Pydantic model. Restly calls `model_dump(mode="json")` before assigning the
document to the column. The view's schema validates the stored document
when it is read back.

Documents containing {data}`WriteOnly <fastapi_restly.schemas.WriteOnly>` or
`Field(exclude=True)` fields raise
{class}`RestlyConfigurationError <fastapi_restly.exc.RestlyConfigurationError>`
before assignment, including exclusions inside nested models. Those fields
would otherwise be omitted from storage. Documents containing dataclasses or
iterators are also unsupported. Use a SQLAlchemy
{class}`~sqlalchemy.types.TypeDecorator` to define their storage representation.
Its bind processor receives the original Pydantic model instead of a dump.

Marking the whole JSON column as `WriteOnly` remains supported. It hides the
column in responses without excluding fields from the stored document.

## Internals


FastAPI-Restly builds request and response schemas from the view's schema,
or auto-generates one from the SQLAlchemy model when {attr}`schema <fastapi_restly.views.BaseRestView.schema>` is omitted on a
view.

### ReadOnly and WriteOnly

`ReadOnly` and `WriteOnly` are field-level markers implemented with
`typing.Annotated` metadata. A schema declares them inline:

```python
class UserSchema(IDSchema):
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

Restly derives two input schemas from the view's schema in
{meth}`before_include_view() <fastapi_restly.views.BaseRestView.before_include_view>`. For the view's schema `UserSchema`:

- {attr}`schema_create <fastapi_restly.views.BaseRestView.schema_create>`: produced by `create_model_without_read_only_fields()`,
  which creates a subclass mixing in `OmitReadOnlyMixin` before `UserSchema` in
  the MRO. `OmitReadOnlyMixin.__pydantic_init_subclass__` directly deletes
  `ReadOnly` entries from `cls.model_fields` and calls `model_rebuild(force=True)`.
  The subclass still inherits validators from `UserSchema` for the fields that
  remain.
- {attr}`schema_update <fastapi_restly.views.BaseRestView.schema_update>`: produced by `create_model_with_optional_fields()`, which
  mixes in both `PatchMixin` and `OmitReadOnlyMixin`. After `OmitReadOnlyMixin`
  strips the read-only fields, `PatchMixin.__pydantic_init_subclass__` sets
  `field.default = None` and wraps every remaining annotation in `Optional[...]`.
  Original field defaults from `UserSchema` are **replaced** by `None`, not
  preserved.

Both derived schemas are stored as class attributes on the view and are frozen
at registration time (see [List Parameters Lifecycle](#list-parameters-lifecycle)).
They can be overridden by declaring `schema_create` or `schema_update` directly
on the view class before {func}`include_view() <fastapi_restly.views.include_view>` is called.

A subclass inherits the schemas its base view declares. Registration rebuilds a
schema for the subclass in two cases: Restly generated it for the parent, or
what it is built from changes. A subclass that declares a new `schema` gets a
new `schema_response`, `schema_create`, `schema_update`, and
`schema_list_params`. A subclass that declares a new `schema_response` gets new
`schema_list_params`. A view without a schema builds it from `model`, so there
a new `model` counts as a new `schema`. A class that Restly generates for the
subclass is not a change of its own, so it does not replace a class that the
base declares.

(auto-generated-schemas)=
### Auto-Generated Schemas

{func}`derive_schema(model, ...) <fastapi_restly.schemas.derive_schema>` builds a Pydantic schema with one
field per column on the model's SQLAlchemy mapper, inherited ones included.
A column's type comes from its `Mapped[T]` annotation, or from
the plain annotation on a SQLModel table. Under
`from __future__ import annotations`, the annotation string is evaluated against
the model's module. When no annotation resolves, the column type's
`python_type` is used. A column with neither raises `TypeError` naming the
attribute. Synonyms and composites get no field of their own; the columns
behind them already have one. Three of its behaviours are worth noting:

- **Base class selection**: The function checks whether the model has fields
  *named* `id`, `created_at`, and `updated_at` to decide which schema base
  classes to mix in ({class}`IDSchema <fastapi_restly.schemas.IDSchema>`, {class}`TimestampsSchemaMixin <fastapi_restly.schemas.TimestampsSchemaMixin>`, {class}`BaseSchema <fastapi_restly.schemas.BaseSchema>`). It does
  **not** inspect the model's Python inheritance hierarchy; a model with a field
  accidentally named `id` will receive `IDSchema` as a base.
- **ReadOnly annotation**: Three field names are always marked
  `ReadOnly`: `"id"`, `"created_at"`, and `"updated_at"`. So is a
  `column_property` over a SQL expression, which cannot be written. Any other server-side default or
  auto-populated column will **not** be marked `ReadOnly` by auto-generation.
- **Relationship fields**: Never included. A foreign key column, such as
  `customer_id`, is an ordinary field. To show a related row in a response,
  write the schema yourself, for example with an `IDRef[Customer]` field; see
  [Relationships](howto_relationship_idschema.md).

When a {class}`RestView <fastapi_restly.views.RestView>` / {class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` omits {attr}`schema <fastapi_restly.views.BaseRestView.schema>`, the view setup calls
`derive_schema(model)`, so the view and a direct call get the same schema.

### SQLAlchemy-to-Pydantic Type Mapping

`convert_sqlalchemy_type_to_pydantic` maps each column's Python type, from its
annotation or its column type, to its Pydantic equivalent. Pass-through types (those
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

## See also

- [Auto-generated schemas](#auto-generated-schemas): how
  the derived schemas are built when you do not declare one.
- [A different schema for the list
  endpoint](#list-schema): when the
  list and detail routes need different shapes.
