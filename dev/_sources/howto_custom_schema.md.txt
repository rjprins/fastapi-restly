# Custom Schemas and Field Types

:::{note}
FastAPI-Restly uses **schema** for Pydantic request/response models and
**model** for SQLAlchemy ORM models. A `User` model is the database object; a
`UserRead` schema is the public API shape.
:::

Use explicit schemas when you need a stable public contract: aliases, hidden
fields, computed read-only fields, or relationship IDs. If you omit {attr}`schema <fastapi_restly.views.BaseRestView.schema>` on
a view, Restly can auto-generate one from the SQLAlchemy model instead.

## BaseSchema

{class}`fr.BaseSchema <fastapi_restly.schemas.BaseSchema>` is Restly's Pydantic base class. It enables Pydantic's
`from_attributes=True`, which lets response schemas validate SQLAlchemy ORM
objects directly.

In code, it is intentionally this small:

```python
class BaseSchema(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(from_attributes=True)
```

Generated Restly routes still serialize ORM objects through
{meth}`self.to_response_schema(obj) <fastapi_restly.views.BaseRestView.to_response_schema>`. That is where Restly applies response-specific
behavior such as `WriteOnly` filtering and relationship-id normalization.

Use `BaseSchema` when you want to declare every field yourself, including `id`:

```python
class UserRead(fr.BaseSchema):
    id: int
    name: str
    email: str
```

This keeps the `id` field explicit and visible in the schema definition. You are
then responsible for marking it read-only if you do not want it accepted in
create/update payloads.

## IDSchema

Most response schemas inherit from {class}`fr.IDSchema <fastapi_restly.schemas.IDSchema>`. It is essentially
{class}`BaseSchema <fastapi_restly.schemas.BaseSchema>` with a read-only `id` field added:

```python
class IDSchema(fr.BaseSchema):
    id: fr.ReadOnly[Any]
```

That is why examples usually look like this:

```python
class UserRead(fr.IDSchema):
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
class UserRead(fr.TimestampsSchemaMixin, fr.IDSchema):
    name: str
```

## ReadOnly and WriteOnly

`fr.ReadOnly[T]` marks a field as response-only. It is removed from create and
update inputs:

```python
class UserRead(fr.IDSchema):
    name: str
    created_by_id: fr.ReadOnly[int]
```

`fr.WriteOnly[T]` marks a field as request-only. It is accepted in create/update
payloads. Restly strips it only when an object is serialized through
{meth}`self.to_response_schema(obj) <fastapi_restly.views.BaseRestView.to_response_schema>`, which the generated CRUD and ReactAdmin routes
use:

```python
class UserRead(fr.IDSchema):
    email: str
    password: fr.WriteOnly[str]
```

Restly applies `ReadOnly` when it generates {attr}`schema_create <fastapi_restly.views.BaseRestView.schema_create>` and
{attr}`schema_update <fastapi_restly.views.BaseRestView.schema_update>`, and when its object helpers construct or update ORM objects.
`WriteOnly` is removed from responses by `to_response_schema()`. If you return
a schema object directly to FastAPI or call Pydantic serialization yourself,
`WriteOnly` is schema metadata only and is not removed automatically.

## Aliases

Use normal Pydantic aliases when the API field name differs from the Python or
database attribute:

```python
from pydantic import Field


class UserRead(fr.IDSchema):
    first_name: str = Field(alias="firstName")
    email: str
```

Incoming payloads can use `firstName`, and Restly responses use the alias on
Restly routes.

## IDRef

Use {class}`fr.IDRef[Model] <fastapi_restly.schemas.IDRef>` for foreign-key and identifier-reference fields:

```python
class ArticleRead(fr.IDSchema):
    title: str
    author_id: fr.IDRef[Author]
```

The wire format is a scalar id:

```json
{
  "title": "Intro",
  "author_id": 1
}
```

Restly validates that the referenced `Author` exists and resolves the id before
creating or updating the ORM object. See [Foreign Keys with IDRef](howto_relationship_idschema.md)
for the full model and view setup.

## Nested relationship objects

If a client expects a nested relationship object, use {class}`fr.IDSchema[Model] <fastapi_restly.schemas.IDSchema>` as a
field type:

```python
class ArticleRead(fr.IDSchema):
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
For ordinary foreign-key fields, use {class}`IDRef <fastapi_restly.schemas.IDRef>`.

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
    schema = UserRead
```

## See also

- [Auto-generated schemas](technical_details.md#auto-generated-schemas) — how
  the derived schemas are built when you don't declare one.
- [Patterns: a different schema for the list
  endpoint](patterns.md#a-different-schema-for-the-list-endpoint) — when the
  list and detail routes need different shapes.
