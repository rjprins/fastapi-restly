# Custom Schemas and Field Types

:::{note}
FastAPI-Restly uses **schema** for Pydantic request/response models and
**model** for SQLAlchemy ORM models. A `User` model is the database object;
`UserSchema`, the view's schema, defines the public API.
:::

Use explicit schemas when you need a stable public contract: aliases, hidden
fields, computed read-only fields, or relationship IDs. If you omit {attr}`schema <fastapi_restly.views.BaseRestView.schema>` on
a view, Restly can
[auto-generate one](technical_details.md#auto-generated-schemas) from the
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

What comes in still follows `schema_create` and `schema_update`, which Restly
derives from `schema`:

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

The wire format is a scalar id:

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

The wire format is:

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

## See also

- [Auto-generated schemas](technical_details.md#auto-generated-schemas): how
  the derived schemas are built when you do not declare one.
- [Patterns: a different schema for the list
  endpoint](patterns.md#a-different-schema-for-the-list-endpoint): when the
  list and detail routes need different shapes.
