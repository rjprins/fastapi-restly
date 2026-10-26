# Schemas & Validation

:::{note}
Restly says **schema** for a Pydantic class and **model** for a SQLAlchemy
class. A `User` model is the database table. `UserSchema`, the view's schema,
is the public API.
:::

A view has several schemas. The main one is the view's
{attr}`schema <fastapi_restly.views.BaseRestView.schema>`, a Pydantic class
that lists the fields of the resource. When you register the view,
Restly builds the other schemas from it, one for each use:

| Class | Used for | What Restly leaves out of the view's schema |
|---|---|---|
| response class, `UserResponse` | everything that goes out | the `WriteOnly` fields |
| create body, `UserCreate` | the body of `POST` | the `ReadOnly` fields |
| update body, `UserUpdate` | the body of `PATCH` | the `ReadOnly` fields; every other field is optional |

Each of these classes is a subclass of the view's schema. So each one keeps
the field types, the aliases and the validators of the view's schema. Two
markers decide which class keeps a field: {data}`fr.ReadOnly
<fastapi_restly.schemas.ReadOnly>` keeps it out of the request bodies, and
{data}`fr.WriteOnly <fastapi_restly.schemas.WriteOnly>` keeps it out of the
responses. The filters and sort keys of the list route come from the
response class too; [Filtering, Sorting & Pagination](filtering.md)
covers them.

You can set each class on the view yourself, as
{attr}`schema_response <fastapi_restly.views.BaseRestView.schema_response>`,
{attr}`schema_create <fastapi_restly.views.BaseRestView.schema_create>` or
{attr}`schema_update <fastapi_restly.views.BaseRestView.schema_update>`.
Restly then uses your class as you wrote it. A view without a
{attr}`schema <fastapi_restly.views.BaseRestView.schema>` gets one that
Restly generates from the model.

Find the section for your goal:

| Goal | Tool |
|---|---|
| A public API that differs from the table | [Write the schema](#write-or-generate) |
| An input field that responses never show | [`WriteOnly`](#readonly-and-writeonly) |
| A field that the server owns and clients cannot set | [`ReadOnly`](#readonly-and-writeonly) |
| A field set on create and frozen afterwards | [Your own request body](#explicit-write-schemas) |
| A rule that a value must follow | [Validation](#validation) |
| Other field names in the JSON | [Aliases](#field-aliases) |
| A response that differs from the input | [Your own response class](#own-response-class) |
| Clear type names in OpenAPI and generated clients | [Name your schemas](#name-your-schemas) |
| A custom route that returns the view's classes | [Name the classes in a custom route](#name-the-response-classes) |
| A foreign key, or a relationship as an id or an object | [Relationships](relationships.md) |

(write-or-generate)=
## Generate the schema or write it

Without a schema, Restly generates the view's schema from the model. This is
enough when the table is already the API you want:

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
```

The generated schema has one field for each column, and no relationships.
Restly marks `id`, `created_at`, `updated_at` and each column with
`init=False` as `ReadOnly`; see
[How Restly generates a schema](#auto-generated-schemas) for the details.

Write the schema yourself when the API differs from the table: other field
names, fields that only go one way, references to other rows, or rules for
the values. Write it also when the field names must stay the same while the
table changes. Set it on the view:

```python
class UserSchema(fr.IDSchema):
    name: str
    email: str


@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserSchema
```

Restly writes each field of a request body to the model attribute with the
same Python name. So name a field after its column, and use an
[alias](#field-aliases) when the JSON needs another name.

(schema-base-classes)=
## Choose a base class

Restly has three base classes for a schema:

- {class}`fr.IDSchema <fastapi_restly.schemas.IDSchema>` adds a read-only
  `id` field, as `id: fr.ReadOnly[Any]`. Most schemas use it. The `id` is in
  the responses, and the create and update bodies leave it out.
- {class}`fr.BaseSchema <fastapi_restly.schemas.BaseSchema>` is the class
  under `IDSchema`, without the `id`. Use it to declare every field
  yourself, `id` included. Mark the `id` `ReadOnly` if clients must not send
  it.
- {class}`fr.TimestampsSchemaMixin <fastapi_restly.schemas.TimestampsSchemaMixin>`
  adds read-only `created_at` and `updated_at` fields. Put it before the
  base class.

```python
class UserSchema(fr.TimestampsSchemaMixin, fr.IDSchema):
    name: str
    email: str
```

:::{admonition} Details
:class: details

`BaseSchema` is a Pydantic `BaseModel` with `from_attributes=True`, so it can
read the attributes of an ORM object. It also checks that the markers are
used in the right place; see [the next section](#readonly-and-writeonly).

A request body that you set on the view, such as `schema_create`, can also be
a plain `pydantic.BaseModel`. Its fields can still use `ReadOnly`,
`WriteOnly` and {class}`fr.MustExist <fastapi_restly.schemas.MustExist>`.

`fr.IDSchema[User]`, with the model as a type parameter, gives the schema the
model's type; [Type Annotations](typing.md) explains when that helps.
As the type of a field, `fr.IDSchema[User]` is a reference to another row;
see [Relationships](relationships.md).
:::

(readonly-and-writeonly)=
## Hide a field from requests or from responses

{data}`fr.ReadOnly[T] <fastapi_restly.schemas.ReadOnly>` marks a field that
only goes out. The create and update bodies leave it out, so a client cannot
set it. A value that a client sends for it is ignored:

```python
class UserSchema(fr.IDSchema):
    name: str
    created_by_id: fr.ReadOnly[int]
```

The server sets such a value. Set it on the model, with a column default, so
that every write path sets it; see
[Stamp the fields the server owns](#server-stamped-fields).

{data}`fr.WriteOnly[T] <fastapi_restly.schemas.WriteOnly>` marks a field that
only comes in. The create and update bodies accept it, and no response shows
it:

```python
class UserSchema(fr.IDSchema):
    email: str
    password: fr.WriteOnly[str]
```

Pydantic's secret types, such as `SecretStr`, work in a schema too. A write
stores the secret, and a response shows `**********`, as Pydantic does. So
a client sees that the field has a value, but not the value. Like a
`WriteOnly` field, such a field gets no filter and is not a sort key.

:::{admonition} Details
:class: details

The two markers work in different places. This matters when you use a
schema outside a view:

- `WriteOnly` sets Pydantic's `exclude` on the field. So every serialization
  of the schema drops it: the CRUD responses, a FastAPI `response_model`, and
  your own `model_dump()` calls. This also holds in nested schemas, and the
  response schema in OpenAPI does not show the field.
- `ReadOnly` works only where Restly applies it: in the create and update
  bodies that Restly generates, and when Restly's object helpers build or
  update an ORM object. On a schema that you use yourself, a `ReadOnly`
  field validates and serializes like any other field. OpenAPI marks it
  `readOnly`.

A marker must be the outer type of the field. Inside a union or a container,
as in `Optional[WriteOnly[str]]`, `WriteOnly[str] | None` or
`list[WriteOnly[str]]`, the marker would do nothing. The same holds inside a
type alias, such as `type Secret = WriteOnly[str]`. So Restly rejects such a
field with a
{class}`RestlyConfigurationError <fastapi_restly.exc.RestlyConfigurationError>`.
For an optional write-only field, write `WriteOnly[Optional[str]]`.

A JSON column has its own rules for `WriteOnly` fields; see
[Validate the document in a JSON column](#json-document-columns).
:::

(explicit-write-schemas)=
## Write the request body yourself

A {attr}`schema_create <fastapi_restly.views.BaseRestView.schema_create>` or
{attr}`schema_update <fastapi_restly.views.BaseRestView.schema_update>` that
you set on the view is the whole contract for its request. Restly writes every
field that it declares, except the fields that the class itself marks
`ReadOnly`. The markers on the view's schema only shape the bodies that Restly
generates.

So you can set a field on create and freeze it afterwards. Mark it `ReadOnly`
on the view's schema, and declare it in your own create body:

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

`POST /comments` sets `story_id`. The generated update body leaves it out, so
`PATCH` ignores it.

Keep a field that the server stamps, such as a tenant id, out of every
request body that you write. Otherwise a client can set it.

(validation)=
## Validate input

Each kind of rule has one place:

| Rule | Where it goes | What the client gets |
|---|---|---|
| The format or the range of one value | A Pydantic validator or a `Field` constraint on the schema | 422 |
| A field that a client cannot set | [`ReadOnly`](#readonly-and-writeonly), or [your own request body](#explicit-write-schemas) | The value is ignored |
| A referenced row must exist | {class}`fr.MustExist <fastapi_restly.schemas.MustExist>` or a reference field; see [Relationships](#reference-exists) | 404 |
| A value must be unique | A unique constraint on the column; see [Errors](#default-exception-handling) | 409 |
| A rule that needs the database, the stored row or the caller | The `create` or `update` business method; see [Request Lifecycle & Hooks](#business-methods) | The error that you raise |

Whether the caller may run the action at all is not validation. It is a
policy, and it goes in
[`authorize`](#authorize-gate), which answers with 403.

### Value validation

Put a validator or a `Field` constraint on the view's schema. The create
body, the update body and the response class are subclasses of the view's
schema, so all three run it:

```python
import re

import pydantic


class OrganizationSchema(fr.IDSchema):
    name: str = pydantic.Field(min_length=2)
    slug: str

    @pydantic.field_validator("slug")
    @classmethod
    def check_slug(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", value):
            raise ValueError("use lowercase letters, digits and hyphens")
        return value
```

A value that fails returns 422, with Pydantic's list of errors.

A rule that compares two fields, in a `model_validator`, works on the create
body. The update body holds only the fields that the client sends, and the
other fields are `None`. Check such a rule in `update`, where you have the
stored row too. Otherwise a `PATCH` that sends one of the two fields can save
a row that breaks the rule. The response class runs the validator too, so
every read of that row, and every list that holds it, then fails with a
server error.

:::{admonition} Details
:class: details

The validator also runs in these places:

- In the update body, the client may leave out each field. Pydantic does not
  validate a field that the client leaves out, so the validator does not
  run for it. Each field keeps its type, so `null` is valid only where the
  type allows `None`, as in the create body. `{"slug": null}` returns 422
  before the validator runs, so the validator above never gets `None`. A
  validator with `mode="before"` runs before that check, so it still gets
  the client's `null`.
- The response class runs the validator when Restly builds a response from a
  row. A row that breaks the rule, for example one that a script wrote, then
  fails with a server error.
- List filters run it on each filter value; see
  [Filter values go through the field's validation](#filter-value-validation).

A class without the field does not run its validator. The request bodies
have no `ReadOnly` fields and no primary key that the server generates. The
response class has no `WriteOnly` fields.

To check only what comes in, put the validator on a request body that you set
on the view. The SaaS example does this for organizations
([`app/organizations/schemas.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/organizations/schemas.py)).
:::

### Database validation

A schema sees one request. It does not see other rows, the stored row or the
caller:

- A reference to another row: {class}`fr.MustExist
  <fastapi_restly.schemas.MustExist>`,
  {class}`fr.IDRef <fastapi_restly.schemas.IDRef>` and `fr.IDSchema[Model]`
  check that the row exists, and return 404 if it does not. See
  [Relationships](#reference-exists).
- A unique value, such as an email address: put a unique constraint on the
  column. The database rejects a second row with the same value, and Restly
  turns the error into a 409; see [Errors](#default-exception-handling).
- A rule that needs the database or the caller, such as "no new tasks in an
  archived project": check it in the `create` or `update` business method
  and raise an error there; see
  [Request Lifecycle & Hooks](#business-methods). The SaaS example checks
  such rules in
  [`app/tasks/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/tasks/views.py).

(field-aliases)=
## Use other field names in JSON

Use a normal Pydantic alias when the JSON name differs from the Python name:

```python
from pydantic import Field


class UserSchema(fr.IDSchema):
    first_name: str = Field(alias="firstName")
    email: str
```

A request body must use `firstName`. The responses use `firstName` too. The
Python name stays `first_name`, and Restly writes the value to the model
attribute with that name. List filters use the alias as well; see
[Filtering, Sorting & Pagination](filtering.md).

(own-response-class)=
## Set your own response class

By default, the response class is the view's schema without its `WriteOnly`
fields. Restly builds this class even when the schema has no `WriteOnly`
fields, so OpenAPI always shows `UserResponse`.

When what goes out differs more from what comes in, set
{attr}`schema_response <fastapi_restly.views.BaseRestView.schema_response>`
on the view. Everything that goes out follows it:

- every single response, and each item of the list response;
- the fields that the list filters and sort keys use, so a client can filter
  only on the fields that go out;
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
wrote it: it does not remove `WriteOnly` fields from it.

To change how one object becomes a response, or to send another shape, see
[Responses](responses.md).

:::{admonition} Details
:class: details

Name the class `<Resource>Response`, as in `AccountResponse`. Restly names the
list response and the list params after it: `AccountListResponse` and
`AccountListParams`. For type checkers, put the class in the second type
parameter of the view, as in `fr.AsyncRestView[Account, AccountResponse]`; see
[Type Annotations](#view-generics).
:::

(generated-class-names)=
## The names of the generated classes

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

The `404` for a row that does not exist names the resource too, such as
`User with id 5 was not found`. To change the text, override
{meth}`not_found <fastapi_restly.views.BaseRestView.not_found>`.

:::{admonition} Details
:class: details

The list response has no view attribute, because it has nothing of its own
to set. It is the envelope of the view's
{attr}`pagination <fastapi_restly.views.BaseRestView.pagination>`, filled with
the response class. To change it, change one of those two; see
[Change the list envelope](#change-list-envelope). A react-admin view has no
list response class: its list route returns a plain JSON list.

A view without a schema gets a generated one. For a model named `User` it is
`UserSchema`, so the names above stay the same. A schema with another name
keeps its full name as the resource: `UserRead` gives `UserReadCreate`.
:::

(name-your-schemas)=
## Name your schemas

OpenAPI keeps all classes in one list, by name. It has no modules. A
generated client uses these names as type names. So each class in your API
needs its own name, even when Python keeps two classes apart in different
modules.

Restly names the classes that it generates after the view's schema; see
[the names of the generated classes](#generated-class-names). Follow these
rules for the classes that you write:

- Name the view's schema after the resource that the client sees:
  `UserSchema`, or `AdminUserSchema` for an admin view of users. Two schemas
  named `UserSchema` in different modules clash.
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
- Two views that share a schema also share the names of the classes that
  Restly generates. When the classes differ, for example because only one
  view is paginated, give one view a schema of its own:

  ```python
  class AllTasksSchema(TaskSchema):
      pass
  ```

  The list response takes its name from the response class. So when the two
  views set the same `schema_response`, give one view a response class of
  its own in the same way.

:::{admonition} Details
:class: details

Two different classes can still get the same name. For example, two views
share a schema but only one is paginated, or you wrote a `UserCreate` that is
not the same as the generated one. OpenAPI then shows both classes under long
names built from the module path, such as `app__users__views__UserCreate`.
When the module path is the same too, it numbers them, and the numbers follow
the order in which the views are registered. These names can change, and a
generated client then breaks.

When the app builds its OpenAPI spec, Restly checks for this and warns with a
{class}`RestlyDuplicateSchemaNameWarning <fastapi_restly.exc.RestlyDuplicateSchemaNameWarning>`
that names the classes and the views. Restly checks an app that you pass to
{func}`fr.configure(app, ...) <fastapi_restly.db.configure>` or
{func}`configure_tests(app=...) <fastapi_restly.testing.configure_tests>`, or
that you include a view on. Two classes with the same name are not a problem
when their JSON schemas are the same, docstrings and defaults included:
OpenAPI then shows them as one.
:::

(name-the-response-classes)=
## Name the view's classes in a custom route

A custom route can show the same types in OpenAPI as the CRUD routes. A route
decorator runs in the class body, before Restly builds the view's classes.
So a custom route names them with markers from `fastapi_restly.views`. When
the view registers, Restly puts the view's class in place of each marker:

| Marker | The view's class | On `UserView` |
|---|---|---|
| {data}`ViewResponse <fastapi_restly.views.ViewResponse>` | {attr}`schema_response <fastapi_restly.views.BaseRestView.schema_response>` | `UserResponse` |
| {data}`ViewListResponse <fastapi_restly.views.ViewListResponse>` | the list response, with the view's pagination | `UserListResponse` |
| {data}`ViewCreate <fastapi_restly.views.ViewCreate>` | {attr}`schema_create <fastapi_restly.views.BaseRestView.schema_create>` | `UserCreate` |
| {data}`ViewUpdate <fastapi_restly.views.ViewUpdate>` | {attr}`schema_update <fastapi_restly.views.BaseRestView.schema_update>` | `UserUpdate` |
| {data}`ViewID <fastapi_restly.views.ViewID>` | {attr}`id_type <fastapi_restly.views.BaseRestView.id_type>` | `int` |

```python
from fastapi_restly.views import ViewID, ViewListResponse, ViewResponse


@fr.include_view(app)
class UserView(AppView):
    prefix = "/users"
    model = User
    schema = UserSchema

    class Action(fr.Action):
        ACTIVATE = "activate"

    @fr.get("/inactive")
    async def inactive(self, list_params) -> ViewListResponse:
        return await self.handle_get_many(list_params, where=User.active.is_(False))

    @fr.post("/{id}/activate", status_code=200)
    async def activate(self, id: ViewID) -> ViewResponse:
        user = await self.get_one(id)
        async with self.write_action(self.Action.ACTIVATE, obj=user):
            user.active = True
        return user
```

OpenAPI then shows `UserListResponse` and `UserResponse` for these routes,
as for the CRUD routes. A path parameter annotated `ViewID` gets the path
converter of the id type, as the item routes do: here `/{id:int}`; see
[Item paths](#item-route-matching).

The routes return the list result and the row. Because they declare the
view's response, Restly passes these through
{meth}`to_response <fastapi_restly.views.BaseRestView.to_response>`, as the
CRUD routes do; see
[The response of a custom route](#custom-route-response).

A marker stands for the class of the view that registers. So a route on a
base view works for each view that subclasses it, with that view's classes.
The `TrashView` of the SaaS example adds a trash list and a restore route to
two views this way, in
[`app/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/views.py).

:::{admonition} Details
:class: details

A marker works wherever the class could go: in a parameter annotation, in the
return annotation, in `response_model=`, and inside another type, such as
`list[ViewResponse]` or `ViewResponse | None`. `Annotated[ViewID, Path(ge=1)]`
keeps the `Path(ge=1)`. A path that gives a converter, such as `/{id:str}`,
keeps it.

For a type checker, each marker is `Any`, so it accepts a marker in any
annotation. A plain {class}`View <fastapi_restly.views.View>` has no model and
no schema, so a route on it cannot use the markers: registration raises
{class}`RestlyConfigurationError <fastapi_restly.exc.RestlyConfigurationError>`.

To use the classes outside a route, or the classes of another view, build
them with
{func}`derive_schema_response(schema) <fastapi_restly.schemas.derive_schema_response>`
and
{func}`derive_schema_list_response(schema_response, pagination=...) <fastapi_restly.schemas.derive_schema_list_response>`.
They return the same classes that the view uses. Pass the view's pagination
as it is, also when it is `None`. A list response with another envelope than
the view's has another shape, for example `data` without `total_count`.
There are then two classes named `UserListResponse`, and Restly warns; see
[Name your schemas](#name-your-schemas). A function builds these classes, so
type checkers do not accept them in an annotation, as in
`data: UserResponse`. At runtime they are normal classes, so add
`# type: ignore[valid-type]` on such a line.

A custom route that names `UserSchema` or `PaginatedEnvelope[UserSchema]`
sends the same JSON, but shows a type of its own in OpenAPI.
:::

(json-document-columns)=
## Validate the document in a JSON column

A `JSON` column maps to a bare `dict`, which does not check the fields of the
document. To check its shape, write the schema yourself and give the field a
nested Pydantic model as its type. Restly calls `model_dump(mode="json")`
before it assigns the document to the column. The view's schema validates the
stored document when it is read back.

A document with a {data}`WriteOnly <fastapi_restly.schemas.WriteOnly>` or a
`Field(exclude=True)` field raises
{class}`RestlyConfigurationError <fastapi_restly.exc.RestlyConfigurationError>`
before Restly assigns it. This includes such fields in nested models. The dump
would leave those fields out, so they would not be stored. A document with
dataclasses or iterators raises the same error. For such documents, use a
SQLAlchemy {class}`~sqlalchemy.types.TypeDecorator` that defines how the
document is stored. Its bind processor receives the Pydantic model itself, not
a dump.

You can still mark the whole JSON column `WriteOnly`. That hides the column
in responses, and the stored document keeps all its fields.

## Internals

This section explains how Restly builds the classes. You do not need it to
use them.

### How the markers work

`ReadOnly` and `WriteOnly` are `typing.Annotated` aliases. Each one adds a
marker object and a Pydantic `Field` to the type:

- `ReadOnly[T]` is `T` with the read-only marker and a `readOnly` flag for
  the JSON schema.
- `WriteOnly[T]` is `T` with the write-only marker, a `writeOnly` flag for the
  JSON schema, and `exclude=True`. Pydantic's `exclude` removes the field in
  every serialization of the schema. A value can only leak through a response
  that never passes through the schema, such as a raw dict or ORM object that
  a route returns without a `response_model`.

Restly finds a marker in the field's top-level metadata. That is why a marker
inside a union or a container is rejected. `BaseSchema` checks this when the
class is defined. A view checks its own classes again when it registers, which
also covers classes that do not derive from `BaseSchema`.

### How Restly builds the request bodies and the response class

{meth}`before_include_view() <fastapi_restly.views.BaseRestView.before_include_view>`
builds the classes. For the view's schema `UserSchema`:

- {attr}`schema_response <fastapi_restly.views.BaseRestView.schema_response>`
  comes from
  {func}`derive_schema_response() <fastapi_restly.schemas.derive_schema_response>`.
  It is a subclass of `UserSchema` that deletes the `WriteOnly` fields. Each
  call with the same schema returns the same class, so a custom route can
  name it.
- {attr}`schema_create <fastapi_restly.views.BaseRestView.schema_create>` is
  a subclass of `UserSchema` with `OmitReadOnlyMixin` first in the MRO. That
  mixin deletes the `ReadOnly` fields from `cls.model_fields` and rebuilds the
  class with `model_rebuild(force=True)`.
- {attr}`schema_update <fastapi_restly.views.BaseRestView.schema_update>`
  adds `PatchMixin` as well. After the `ReadOnly` fields are gone, it sets the
  default of every field to `None`, so the client may leave out each field.
  The defaults of `UserSchema` are **replaced** by `None`, not kept. Each
  field keeps its type, and Pydantic does not validate the `None` default,
  also when the class sets `validate_default=True` or a validator sets
  `always=True`. A field that the client does not send is not written:
  Restly writes only the fields in `model_fields_set`. In OpenAPI, no field
  of the update body is required.

Both request bodies also leave out a primary key that the server generates,
even when the schema does not mark it `ReadOnly`. Generated means an
autoincrement column, a column with a default, or a dataclass field outside
`__init__`. A primary key without any of these is a natural key, and the
client sends it.

The classes are subclasses, so they keep the validators and serializers of
`UserSchema`. When a class deletes a field, it keeps the validators and
serializers of that field too, and Pydantic does not run them there. So a
validator for a `ReadOnly` field, a `WriteOnly` field or a generated primary
key does not need `check_fields=False`.

Restly stores the classes on the view when it registers the view. Declare
`schema_response`, `schema_create` or `schema_update` on the view class to use
your own class instead.

### When a subclass view gets new classes

A subclass inherits the classes that its base view declares. Registration
builds a class again for the subclass in two cases: Restly generated it for
the parent, or what it is built from changes. A subclass that declares a new
`schema` gets a new `schema_response`, `schema_create`, `schema_update` and
`schema_list_params`. A subclass that declares a new `schema_response` gets
new `schema_list_params`. A view without a schema builds it from `model`, so
there a new `model` counts as a new `schema`. A class that Restly generates
for the subclass is not a change of its own, so it does not replace a class
that the base declares.

The list params are also built from `pagination`, the list settings such as
`include_filters` and `sort_query_param`, and the filter methods. A subclass
that changes one of them gets new `schema_list_params` when the base declares
the list params that Restly would generate for it, made with
{func}`derive_schema_list_params() <fastapi_restly.query.derive_schema_list_params>`.
Other declared list params stay, such as ones from a narrower schema. A
subclass with other settings then fails when it registers, and the message
says what to change. List params that you write by hand take no list
settings and no filter methods. A new `schema` replaces them too, so a
subclass with a new schema declares its list params again.

(auto-generated-schemas)=
### How Restly generates a schema

{func}`derive_schema(model, ...) <fastapi_restly.schemas.derive_schema>`
builds the schema of a view without one. It makes one field for each column
on the model's SQLAlchemy mapper, inherited columns included. A column's type
comes from its `Mapped[T]` annotation, or from the plain annotation on a
SQLModel table. Under `from __future__ import annotations`, Restly evaluates
the annotation string in the model's module. When no annotation resolves, the
column type's `python_type` is used. A column with neither raises `TypeError`
that names the attribute. Synonyms and composites get no field of their own:
the columns behind them already have one. A field allows `null` (`None` in
Python) only when its column is nullable. `mapped_column(nullable=...)`
decides this, as it does for SQLAlchemy, even when the `Mapped[...]`
annotation says otherwise.

The create body follows the model's constructor, as SQLAlchemy does. The
client may leave out a field when its column is nullable, or when the
constructor does not require it and the model then still gives the column a
value. That value comes from the column, such as `insert_default` or
`server_default`, or from the constructor: a `default` other than `None`, or
a `default_factory`. Restly calls the constructor with the fields that the
client sent, so that value applies.

On a dataclass model, a column with only an `insert_default` or a
`server_default` is a required argument of the constructor, so its field is
required. To let the client leave it out, add `default=None`, as SQLAlchemy
does for a value that the database fills:
`mapped_column(server_default="x", default=None)`. On SQLAlchemy 2.0, a JSON
column stores that `None` as JSON `null` instead of its default, so Restly
keeps such a field required. Use `default_factory=list`, or
`JSON(none_as_null=True)`, for a JSON column. For a column that the client
must never write, such as a `Computed` column, use `init=False`, as
SQLAlchemy does. The field is then `ReadOnly`.

Three rules decide the rest:

- **Base classes**: Restly checks whether the model has fields *named*
  `id`, `created_at` and `updated_at`, and picks
  {class}`IDSchema <fastapi_restly.schemas.IDSchema>`,
  {class}`TimestampsSchemaMixin <fastapi_restly.schemas.TimestampsSchemaMixin>`
  and {class}`BaseSchema <fastapi_restly.schemas.BaseSchema>` from that. It
  does **not** look at the model's Python base classes. A model with a field
  named `id` for another reason also gets `IDSchema`.
- **ReadOnly fields**: the fields `id`, `created_at` and `updated_at` are
  always `ReadOnly`. So is a `column_property` over a SQL expression, which
  cannot be written. So is a column that the constructor does not take
  (`init=False`), such as a tenant column with an `insert_default`: the model
  or the server sets it, so the client must not. To let the client write such
  a column, remove `init=False`, so that the constructor takes it. Any other
  column that the server fills, for example with a server default, is
  **not** `ReadOnly`.
- **Relationships**: never included. A foreign key column, such as
  `customer_id`, is an ordinary field. With `init=False`, it is `ReadOnly`,
  as the rule above says. To write a relationship, or to show it in a
  response, write the schema yourself: `customer: fr.IDRef[Customer]` for
  its id, or a nested schema for the whole row. See
  [Choose a reference style](#reference-styles).

A view and a direct call of `derive_schema(model)` give the same schema.

### Column types and field types

`convert_sqlalchemy_type_to_pydantic` maps the Python type of each column,
from its annotation or its column type, to a Pydantic field type. A type that
Pydantic already understands stays as it is:

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

`Any` and other generic types, such as `tuple[int, int]`, also stay as they
are. Any other type raises `TypeError` when Restly generates the schema. For a
custom column type, write the schema yourself.

## See also

- [Relationships](relationships.md): foreign keys and
  relationships as ids or nested objects, and the check that a referenced row
  exists.
- [Responses](responses.md): how one object becomes a response,
  envelopes, and [a different schema for the list route](#list-schema).
- [Filtering, Sorting & Pagination](filtering.md): the filters and sort
  keys that come from the response class.
- [Models](models.md): values that the server sets on every write path.
