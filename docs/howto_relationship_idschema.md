# Work with Foreign Keys Using IDRef

Use {class}`fr.IDRef[Model] <fastapi_restly.schemas.IDRef>` for foreign-key fields. The API stays in the common
scalar-id shape while FastAPI-Restly still validates that the referenced row
exists.

:::{note}
FastAPI-Restly uses **schema** for Pydantic request/response models and
**model** for SQLAlchemy ORM models.
:::

## Model Setup

```python
import fastapi_restly as fr
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship


class Author(fr.IDBase):
    name: Mapped[str]


class Article(fr.IDBase):
    title: Mapped[str]
    author_id: Mapped[int] = mapped_column(ForeignKey("author.id"))
    author: Mapped["Author"] = relationship(default=None, init=False)
```

{class}`fr.IDBase <fastapi_restly.models.IDBase>` auto-generates the table name from the class name (`Author` →
`author`, `Article` → `article`). That is why `ForeignKey("author.id")` is
correct here.

## Schema Setup

```python
class AuthorRead(fr.IDSchema):
    name: str


class ArticleRead(fr.IDSchema):
    title: str
    author_id: fr.IDRef[Author]
```

{class}`fr.IDRef[Author] <fastapi_restly.schemas.IDRef>` means "this field references an `Author` row by id." The
wire format is a plain scalar:

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

On create and update, Restly looks up the `Author` with `id=1`. If it does not
exist, the request returns `404`. That lookup is an *unscoped* existence check —
see [Visibility and Multi-Tenancy](#visibility-and-multi-tenancy) below.

## List filtering

The FK field is filterable on the list endpoint by its own public name —
`GET /articles/?author_id=1` (also `author_id__in`, `author_id__ne`,
`author_id__isnull`). An {class}`IDRef <fastapi_restly.schemas.IDRef>` id is treated as opaque, so the range and
substring operators are deliberately not offered. See
[Query Modifiers → Foreign-key filtering](howto_query_modifiers.md#foreign-key-filtering).

## Field Naming

Name the schema reference field after a mapped attribute on the model — either
a foreign-key column or a relationship. Restly inspects the SQLAlchemy mapper
(not the field name) to decide how to apply the reference, so the FK column can
be named anything; the `_id` suffix is a common convention, not a requirement:

```python
author_id: fr.IDRef[Author]   # maps to the Article.author_id FK column
post_fk: fr.IDRef[Post]       # a non-_id column name resolves the same way
```

When the field names a FK column and the model also has a relationship backed
by that column, Restly keeps the column and the relationship in sync:

| Schema field | FK column | Relationship |
|---|---|---|
| `author_id` | `Article.author_id` | `Article.author` |

The relationship is found through the mapper, so this pairing holds whatever the
column is called. If the relationship attribute is absent, Restly still sets the
FK column.

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

## Input Compatibility

{class}`IDRef <fastapi_restly.schemas.IDRef>` accepts both scalar ids and `{"id": ...}` dictionaries on input:

```json
{ "author_id": 1 }
```

```json
{ "author_id": {"id": 1} }
```

The response shape stays scalar. This is useful when clients or migration code
already send the dictionary form, but the public API contract should remain an
identifier field.

## About IDSchema

Most examples inherit from {class}`fr.IDSchema <fastapi_restly.schemas.IDSchema>` — {class}`BaseSchema <fastapi_restly.schemas.BaseSchema>` plus a read-only `id`
field. The schema bases, `ReadOnly` / `WriteOnly` markers, and aliases are
owned by [Custom Schemas and Field Types](howto_custom_schema.md); inherit
from `fr.BaseSchema` instead if you want every field, including `id`,
explicit.

## Nested Relationship Objects

Some clients model relationships as objects. For that shape, annotate the
relationship field with {class}`fr.IDSchema[Model] <fastapi_restly.schemas.IDSchema>`:

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

{class}`IDRef <fastapi_restly.schemas.IDRef>` and `IDSchema[Model]` both validate the referenced row and use the same
resolver. The difference is the API shape.

## Dataclass Relationship Setup

{class}`fr.IDBase <fastapi_restly.models.IDBase>` uses SQLAlchemy's `MappedAsDataclass`, which generates an `__init__`
from the model fields. Restly's create/update helpers are aware of that
constructor shape when an {class}`IDRef <fastapi_restly.schemas.IDRef>` / {class}`IDSchema <fastapi_restly.schemas.IDSchema>` field has been resolved to an ORM
object.

The common FK-first declaration is still the clearest default:

```python
author_id: Mapped[int] = mapped_column(ForeignKey("author.id"))
author: Mapped["Author"] = relationship(default=None, init=False)
```

With that model and `author_id: fr.IDRef[Author]`, Restly passes the scalar FK
and keeps `author` in sync after construction.

If your model is relationship-first, Restly adapts there too:

```python
author_id: Mapped[int] = mapped_column(ForeignKey("author.id"), init=False)
author: Mapped["Author"] = relationship(default=None)
```

In that shape, Restly passes the resolved `Author` object to the constructor and
keeps `author_id` in sync. More generally, Restly supplies the constructor
values your dataclass model requires: FK scalar, relationship object, or both.

If a client supplies both sides independently, Restly validates that they match:

```json
{
  "author_id": 1,
  "author": {"id": 1}
}
```

Conflicting references, such as `"author_id": 1` with `"author": {"id": 2}`,
return `422`. Explicit `null` also participates in this check: `author_id: 1`
with `author: null` is a conflict, while omitting `author` entirely is not.

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
    author_id: Mapped[int] = mapped_column(ForeignKey("author.id"))
    author: Mapped["Author"] = relationship()
```

There is no generated `__init__` contract to satisfy: Restly constructs the
object and applies the resolved reference to the FK column (and the matching
relationship attribute, when one is declared) directly.

(idref-custom-routes)=

## IDRef in Custom Routes

Generated `POST` and `PATCH` routes validate the body before Restly calls `make_new_object()` or `update_object()`, so {class}`IDRef[Model] <fastapi_restly.schemas.IDRef>` fields are already `IDRef` instances.

In a custom route, be careful when you construct a schema yourself. Pydantic's `model_construct()` skips validation, so scalar ids stay plain integers unless you wrap them explicitly:

```python
from fastapi_restly.objects import async_make_new_object


link_schema = TaskLabelRead.model_construct(
    task_id=fr.IDRef[Task](id=request.task_id),
    label_id=fr.IDRef[Label](id=label.id),
)

task_label = await async_make_new_object(
    self.session,
    TaskLabel,
    link_schema,
)
```

This keeps the resolver path active: Restly verifies referenced rows and writes the FK columns. It helps when validated construction would require response-only fields such as `id` or timestamps.

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

The raw ORM object usually has scalar FK columns, while a nested schema expects relationship-shaped data. `IDRef` fields do not need this step because their wire format is already scalar.

The SaaS example's `example-projects/saas/app/views/label.py` shows this in a `create_and_attach` route that creates a sibling row, flushes it to get an id, and then builds a second row with `IDRef` references.

## Visibility and Multi-Tenancy

Reference resolution is an **unscoped existence check**. Restly fetches the
referenced row by primary key only (`session.get(Author, id)`). View
{meth}`build_query <fastapi_restly.views.RestView.build_query>` scoping is not applied, so tenant, soft-delete, and row-level
visibility checks are your responsibility.

The resolver only knows the referenced *model* from `IDRef[Model]`, not which
view governs it. References are a **policy** concern.

Gate in **{meth}`authorize <fastapi_restly.views.RestView.authorize>`**, where `data` carries the *unresolved* reference, so
`data.<field>.id` is the requested id (before the row is fetched). A list field
is a list of references:

```python
@fr.include_view(app)
class ArticleView(fr.AsyncRestView):
    prefix = "/articles"
    model = Article
    schema = ArticleRead

    async def authorize(self, action, obj=None, data=None):
        if data is not None and data.author_id is not None:
            if not await self.author_visible(data.author_id.id):
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

- {class}`IDRef[Model] <fastapi_restly.schemas.IDRef>` uses scalar id wire format on request and response.
- Missing related IDs return `404`.
- Reference resolution is an **unscoped existence check** (bare PK lookup, no
  {meth}`build_query <fastapi_restly.views.RestView.build_query>` scoping). Gate cross-tenant / visibility references in
  {meth}`authorize <fastapi_restly.views.RestView.authorize>` or {meth}`before_commit <fastapi_restly.views.RestView.before_commit>` — see [Visibility and Multi-Tenancy](#visibility-and-multi-tenancy).
- The `_id` field name triggers FK resolution.
- A matching SQLAlchemy relationship lets Restly keep the FK column and
  relationship attribute in sync.
- Dataclass models can be FK-first or relationship-first; Restly supplies the
  constructor values the model requires from the same resolved row.
- If both `author_id` and `author` are explicitly provided, they must refer to
  the same row or the request returns `422`.
- {class}`IDSchema <fastapi_restly.schemas.IDSchema>` as a base class adds the resource's own read-only `id` field.

## See also

- [Tutorial](tutorial.md) — {class}`IDRef <fastapi_restly.schemas.IDRef>` in the blog build, in context.
- [Custom Schemas and Field Types](howto_custom_schema.md) — the schema bases
  and field markers `IDRef` composes with.
- [API Reference](api_reference.md) — `IDRef` / {class}`IDSchema <fastapi_restly.schemas.IDSchema>` signatures and the
  full symbol tables.
