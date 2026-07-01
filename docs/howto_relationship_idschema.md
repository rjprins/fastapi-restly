# Work with Foreign Keys and Relationships

Use {class}`fr.MustExist[int, Model] <fastapi_restly.schemas.MustExist>` for
foreign-key columns. The API will have the common scalar-id shape while
FastAPI-Restly validates that the referenced row exists. Use
{class}`IDRef <fastapi_restly.schemas.IDRef>` or
{class}`IDSchema <fastapi_restly.schemas.IDSchema>` when the schema field names a
relationship instead of a foreign key column.

:::{note}
FastAPI-Restly uses the term **"schema"** for Pydantic request/response models and
**"model"** for SQLAlchemy ORM models.
:::

## Choosing a Reference Style

Most APIs communicate through ids. They are what forms and dropdowns submit,
they stay cacheable, and clients like React Admin can dereference them for
display. Embed the target object only when you deliberately want one larger
response instead of a separate fetch.

| Declaration | JSON value | SQLAlchemy type |
|---|---|---|
| {class}`author_id: fr.MustExist[int, User] <fastapi_restly.schemas.MustExist>` | `1` | scalar foreign key |
| {class}`author: fr.IDRef[User] <fastapi_restly.schemas.IDRef>` | `1` | resolves to `User` |
| {class}`author: fr.IDSchema[User] <fastapi_restly.schemas.IDSchema>` | `{"id": 1}` | resolves to `User` |
| `author: fr.ReadOnly[UserRead]` | the full object | read-only relationship embed |
| `author_id: int` | `1` | scalar foreign key |

- Use {class}`MustExist <fastapi_restly.schemas.MustExist>` for the common case:
  a `*_id` column you want validated. Name the primary-key type first, then the
  target model — `fr.MustExist[int, User]` (`fr.MustExist[UUID, Account]` for a
  UUID key). When the column has a single `ForeignKey`, you can drop the model and
  let Restly infer it — `fr.MustExist[int]`. A plain `int` (or `ReadOnly[int]`
  for a server-stamped column) is the unchecked alternative — no existence check.
- Use {class}`IDRef <fastapi_restly.schemas.IDRef>` /
  {class}`IDSchema <fastapi_restly.schemas.IDSchema>` when the field names a
  relationship. Restly resolves the id to the related object; the difference is
  whether the wire format is flat (`IDRef`) or nested (`IDSchema`).
- In hooks, `data.<field>` is the plain id for
  {class}`MustExist <fastapi_restly.schemas.MustExist>` and `int`; for
  {class}`IDRef <fastapi_restly.schemas.IDRef>` /
  {class}`IDSchema <fastapi_restly.schemas.IDSchema>` it is an unresolved
  reference (read `.id`) that Restly resolves on write.

## Model Setup

```python
import fastapi_restly as fr
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column


class User(fr.IDBase):
    name: Mapped[str]


class Article(fr.IDBase):
    title: Mapped[str]
    author_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
```

{class}`fr.IDBase <fastapi_restly.models.IDBase>` auto-generates the table name from the class name (`User` →
`user`, `Article` → `article`). That is why `ForeignKey("user.id")` is
correct here.

## Schema Setup

```python
class UserRead(fr.IDSchema):
    name: str


class ArticleRead(fr.IDSchema):
    title: str
    author_id: fr.MustExist[int, User]
```

{class}`fr.MustExist[int, User] <fastapi_restly.schemas.MustExist>` marks an
integer foreign key to `User` that must exist. The wire format is a plain
scalar:

```json
{
  "title": "Intro",
  "author_id": 1
}
```

Responses use the same shape:

```json
{
  "id": 10,
  "title": "Intro",
  "author_id": 1
}
```

## View Setup

```python
@fr.include_view(app)
class ArticleView(fr.AsyncRestView):
    prefix = "/articles"
    model = Article
    schema = ArticleRead
```

On create and update, Restly looks up the `User` with `id=1`. If it does not
exist, the request returns `404`. That lookup is an *unscoped* existence check;
see [Visibility and Multi-Tenancy](#visibility-and-multi-tenancy) below. In
hooks, `data.author_id` is the plain integer.

## List filtering

The FK field is filterable on the list endpoint by its own public name —
`GET /articles/?author_id=1` (also `author_id__in`, `author_id__ne`,
`author_id__isnull`, and — since a `MustExist[int, ...]` id is a plain integer —
the range family `author_id__gte` / `__lte` / `__gt` / `__lt`). See
[Query Modifiers → Foreign-key filtering](howto_query_modifiers.md#foreign-key-filtering).

## Field Naming

Name the schema field after a mapped attribute on the model: a foreign-key
column for {class}`MustExist <fastapi_restly.schemas.MustExist>`, or a
relationship for {class}`IDRef <fastapi_restly.schemas.IDRef>` /
{class}`IDSchema <fastapi_restly.schemas.IDSchema>`. Restly inspects the
SQLAlchemy mapper to decide how to apply the value, so the FK column can be
named anything; the `_id` suffix is a common convention, not a requirement:

```python
author_id: fr.MustExist[int, User]  # the Article.author_id FK column
post_fk: fr.MustExist[int, Post]    # a non-_id column name works the same way
author: fr.IDRef[User]              # the Article.author relationship
```

The Python field name is what Restly matches against the mapped attribute — it
builds the model with that name as a keyword argument, so the two must match. To
expose a different name only *on the wire* (a camelCase API, say), keep the field
named after the attribute and add a Pydantic alias:

```python
from pydantic import Field

author_id: fr.MustExist[int, User] = Field(alias="authorId")  # wire: "authorId"
```

When the field names a relationship, Restly resolves the id to an ORM object and
keeps the relationship and its backing FK column in sync:

| Schema field | FK column | Relationship |
|---|---|---|
| `author` | `Article.author_id` | `Article.author` |

The relationship is found through the mapper, so this pairing holds whatever the
column is called, including a column with an explicit DB name
(`mapped_column("db_name", ...)`). If an FK-named reference has no partner
relationship, or more than one relationship shares the FK column (ambiguous),
Restly sets the FK column and leaves the relationship to you.

## Lists of References

A to-many reference serializes as a plain id array with {class}`list[fr.IDRef[Model]] <fastapi_restly.schemas.IDRef>`:

```python
class OrderRead(fr.IDSchema):
    customer_name: str
    products: list[fr.IDRef[Product]]  # serializes as [1, 2, 3]
```

On input, each element accepts both raw scalars and `{"id": ...}` shapes, so
the same field doubles as a permissive write-side type when paired with a
custom {meth}`create <fastapi_restly.views.RestView.create>` / {meth}`update <fastapi_restly.views.RestView.update>` business verb that resolves the list. For
relationship objects that must stay nested on the wire, use
{class}`fr.IDSchema[Model] <fastapi_restly.schemas.IDSchema>` (below).
{class}`MustExist <fastapi_restly.schemas.MustExist>` is for a single scalar FK
column; a to-many field is a relationship, so use
{class}`list[fr.IDRef[Model]] <fastapi_restly.schemas.IDRef>`.

## Input Compatibility

{class}`IDRef <fastapi_restly.schemas.IDRef>` and `IDSchema[Model]` accept both
scalar ids and `{"id": ...}` dictionaries on input:

```json
{ "author": 1 }
```

```json
{ "author": {"id": 1} }
```

The response shape stays with the declared type: {class}`IDRef <fastapi_restly.schemas.IDRef>`
serializes as a scalar, and {class}`IDSchema <fastapi_restly.schemas.IDSchema>`
serializes as `{"id": ...}`. This is useful when clients or migration code
already send one form, but the public API contract should keep the other.

## About IDSchema

Most examples inherit from {class}`fr.IDSchema <fastapi_restly.schemas.IDSchema>` — {class}`BaseSchema <fastapi_restly.schemas.BaseSchema>` plus a read-only `id`
field. The schema bases, `ReadOnly` / `WriteOnly` markers, and aliases are
owned by [Custom Schemas and Field Types](howto_custom_schema.md); inherit
from `fr.BaseSchema` instead if you want every field, including `id`,
explicit. When used as a field type (`author: fr.IDSchema[User]`), it is a
nested relationship reference (below), separate from its use as a base class.

## Nested Relationship Objects

Some clients model relationships as objects. For that shape, annotate the
relationship field with {class}`fr.IDSchema[Model] <fastapi_restly.schemas.IDSchema>`:

```python
class ArticleRead(fr.IDSchema):
    title: str
    author: fr.IDSchema[User]
```

The wire format is:

```json
{
  "title": "Intro",
  "author": {"id": 1}
}
```

{class}`IDRef <fastapi_restly.schemas.IDRef>` and `IDSchema[Model]` both name a
relationship, validate the referenced row, and use the same resolver. The
difference is the API shape: flat id versus nested object.

## Dataclass Relationship Setup

{class}`fr.IDBase <fastapi_restly.models.IDBase>` uses SQLAlchemy's
`MappedAsDataclass`, which generates an `__init__` from the model fields.
Restly's create/update helpers are aware of that constructor shape when an
{class}`IDRef <fastapi_restly.schemas.IDRef>` /
{class}`IDSchema <fastapi_restly.schemas.IDSchema>` relationship field has been
resolved to an ORM object.

The common FK-first declaration is still the clearest default:

```python
author_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
author: Mapped["User"] = relationship(default=None, init=False)
```

With that model and `author: fr.IDRef[User]`, Restly passes the scalar FK when
the constructor needs it and keeps `author` in sync after construction.

If your model is relationship-first, Restly adapts there too:

```python
author_id: Mapped[int] = mapped_column(ForeignKey("user.id"), init=False)
author: Mapped["User"] = relationship(default=None)
```

In that shape, Restly passes the resolved `User` object to the constructor and
keeps `author_id` in sync. More generally, Restly supplies the constructor
values your dataclass model requires: FK scalar, relationship object, or both.

If a schema exposes the same link as two reference fields, for example a
FK-named {class}`IDRef <fastapi_restly.schemas.IDRef>` field alongside the
relationship, Restly validates that they match:

```json
{
  "author_id": 1,
  "author": {"id": 1}
}
```

Conflicting references, such as `"author_id": 1` with `"author": {"id": 2}`,
return `422`. Explicit `null` also participates in this check: `author_id: 1`
with `author: null` is a conflict, while omitting `author` entirely is not. A
plain {class}`MustExist <fastapi_restly.schemas.MustExist>` scalar does not take
part in this reference-pair check; it is a checked column value.

### Standard SQLAlchemy Declarative Models

If you use a normal SQLAlchemy `DeclarativeBase`, the dataclass constructor
rules do not apply:

```python
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Article(Base):
    __tablename__ = "article"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]
    author_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    author: Mapped["User"] = relationship()
```

There is no generated `__init__` contract to satisfy: Restly constructs the
object and applies the resolved reference to the FK column (and the matching
relationship attribute, when one is declared) directly.

(idref-custom-routes)=

## Reference Fields in Custom Routes

Generated `POST` and `PATCH` routes validate the body before Restly calls
`make_new_object()` or `update_object()`, so reference fields are already in the
right shape. A {class}`MustExist <fastapi_restly.schemas.MustExist>` field is a
plain id; {class}`IDRef[Model] <fastapi_restly.schemas.IDRef>` and
{class}`IDSchema[Model] <fastapi_restly.schemas.IDSchema>` fields are reference
instances.

In a custom route, be careful when you construct a schema yourself. Pydantic's
`model_construct()` skips validation. For a
{class}`MustExist <fastapi_restly.schemas.MustExist>` field, pass the plain id:

```python
from fastapi_restly.objects import async_make_new_object


link_schema = TaskLabelRead.model_construct(
    task_id=request.task_id,
    label_id=label.id,
)

task_label = await async_make_new_object(
    self.session,
    TaskLabel,
    link_schema,
)
```

This keeps the existence-check path active: Restly verifies the referenced rows
exist and writes the FK columns. It helps when validated construction would
require response-only fields such as `id` or timestamps.

If those fields were {class}`IDRef <fastapi_restly.schemas.IDRef>` /
{class}`IDSchema <fastapi_restly.schemas.IDSchema>` relationship references
instead, wrap them explicitly before calling the object helper:

```python
link_schema = TaskLabelRead.model_construct(
    task=fr.IDRef[Task](id=request.task_id),
    label=fr.IDRef[Label](id=label.id),
)
```

If you instead use {class}`IDSchema[Model] <fastapi_restly.schemas.IDSchema>` as a nested relationship-object field in a custom response schema, serialize the ORM object through {meth}`self.to_response_schema(obj) <fastapi_restly.views.BaseRestView.to_response_schema>` before returning it:

```python
class TaskLabelNestedRead(fr.IDSchema):
    task: fr.IDSchema[Task]
    label: fr.IDSchema[Label]


@fr.post("/attach", response_model=TaskLabelNestedRead, status_code=201)
async def attach(self, request: AttachRequest):
    obj = await create_task_label(...)
    return self.to_response_schema(obj)
```

The raw ORM object usually has scalar FK columns, while a nested schema expects
relationship-shaped data. Scalar fields
({class}`MustExist <fastapi_restly.schemas.MustExist>`,
{class}`IDRef <fastapi_restly.schemas.IDRef>`) do not need this step because
their wire format is already scalar.

## Visibility and Multi-Tenancy

Reference resolution is an **unscoped existence check**. Restly fetches the
referenced row by primary key only (`session.get(User, id)`). View
{meth}`build_query <fastapi_restly.views.RestView.build_query>` scoping is not applied, so tenant, soft-delete, and row-level
visibility checks are your responsibility.

The resolver only knows the referenced *model* from the field type, not which
view governs it. References are a **policy** concern.

Gate in **{meth}`authorize <fastapi_restly.views.RestView.authorize>`**, where
`data` carries the write-side value before resolution. For a
{class}`MustExist <fastapi_restly.schemas.MustExist>` field, `data.author_id` is
the requested id. For {class}`IDRef <fastapi_restly.schemas.IDRef>` /
{class}`IDSchema <fastapi_restly.schemas.IDSchema>`, `data.<field>.id` is the
requested id (and a list field is a list of references):

```python
@fr.include_view(app)
class ArticleView(fr.AsyncRestView):
    prefix = "/articles"
    model = Article
    schema = ArticleRead

    async def authorize(self, action, obj=None, data=None):
        if data is not None and data.author_id is not None:
            if not await self.author_visible(data.author_id):
                # 404 (not 403) so you don't leak that the id exists elsewhere.
                raise fr.exc.NotFound("author not found")
```

The resolved ORM object is not available in `authorize`; resolution runs later
in the business verb. If you need the resolved row, check in {meth}`before_commit <fastapi_restly.views.RestView.before_commit>`,
where the built object carries it (for example `new.author.org_id`). Prefer
`authorize` when the requested id is enough: it rejects before the unscoped
fetch and is the standard policy seam.

References are gated in `authorize` / `before_commit` like any other
write-path authorization.

## Behavior Summary

- {class}`MustExist[int, Model] <fastapi_restly.schemas.MustExist>` uses scalar id
  wire format on request and response. In Python it stays the plain id and adds
  an existence check.
- {class}`IDRef <fastapi_restly.schemas.IDRef>` and
  {class}`IDSchema <fastapi_restly.schemas.IDSchema>` are relationship-field
  variants: flat id for `IDRef`, nested object for `IDSchema`.
- Missing related ids return `404`.
- Reference resolution is an **unscoped existence check** (bare PK lookup, no
  {meth}`build_query <fastapi_restly.views.RestView.build_query>` scoping). Gate cross-tenant / visibility references in
  {meth}`authorize <fastapi_restly.views.RestView.authorize>` or {meth}`before_commit <fastapi_restly.views.RestView.before_commit>` — see [Visibility and Multi-Tenancy](#visibility-and-multi-tenancy).
- Restly matches the field name to a mapped column or relationship via the
  model's mapper, not by an `_id` suffix.
- For {class}`IDRef <fastapi_restly.schemas.IDRef>` /
  {class}`IDSchema <fastapi_restly.schemas.IDSchema>` fields, a matching
  SQLAlchemy relationship lets Restly keep the FK column and relationship
  attribute in sync.
- Dataclass models can be FK-first or relationship-first; Restly supplies the
  constructor values the model requires from the same resolved row.
- If both `author_id` and `author` are explicitly provided as relationship
  references, they must refer to the same row or the request returns `422`.
- {class}`IDSchema <fastapi_restly.schemas.IDSchema>` as a base class adds the resource's own read-only `id` field.

## See also

- [Tutorial](tutorial.md) — reference fields in the blog build, in context.
- [Custom Schemas and Field Types](howto_custom_schema.md) — the schema bases
  and field markers these compose with.
- [API Reference](api_reference.md) — {class}`MustExist <fastapi_restly.schemas.MustExist>` / {class}`IDRef <fastapi_restly.schemas.IDRef>` / {class}`IDSchema <fastapi_restly.schemas.IDSchema>` signatures and the
  full symbol tables.
