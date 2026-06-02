# How-To: Work with Foreign Keys Using IDRef

Use `fr.IDRef[Model]` for foreign-key fields. The API stays in the common
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

`fr.IDBase` auto-generates the table name from the class name (`Author` →
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

`fr.IDRef[Author]` means "this field references an `Author` row by id." The
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

## Visibility and Multi-Tenancy

Reference resolution is an **unscoped existence check**. Restly fetches the
referenced row by primary key only (`session.get(Author, id)`). View
`build_query` scoping is not applied, so tenant, soft-delete, and row-level
visibility checks are your responsibility.

The resolver only knows the referenced *model* from `IDRef[Model]`, not which
view governs it. References are a **policy** concern.

Gate in **`authorize`**, where `data` carries the *unresolved* reference, so
`data.<field>.id` is the requested id (before the row is fetched). A list field
is a list of references:

```python
@fr.include_view(app)
class ArticleView(fr.AsyncRestView):
    prefix = "/articles"
    model = Article
    schema = ArticleSchema

    async def authorize(self, action, obj=None, data=None):
        if data is not None and data.author_id is not None:
            if not await self.author_visible(data.author_id.id):
                # 404 (not 403) so you don't leak that the id exists elsewhere.
                raise fr.exc.NotFound("author not found")
```

The resolved ORM object is not available in `authorize`; resolution runs later
in the business verb. If you need the resolved row, check in `before_commit`,
where the built object carries it (for example `new.author.org_id`). Prefer
`authorize` when the requested id is enough: it rejects before the unscoped
fetch and is the standard policy seam.

A future release may add an opt-in scoped-resolution hook; until then, references
are gated in `authorize` / `before_commit` like any other write-path
authorization.

## Naming Convention

Automatic FK resolution needs the schema field name to end in `_id`:

```python
author_id: fr.IDRef[Author]
```

If the SQLAlchemy model also has a relationship with the same name minus
`_id`, Restly keeps the FK column and relationship in sync:

| Schema field | FK column | Relationship |
|---|---|---|
| `author_id` | `Article.author_id` | `Article.author` |

If the relationship attribute is absent, Restly still sets the FK column.

## Dataclass Relationship Setup

`fr.IDBase` uses SQLAlchemy's `MappedAsDataclass`, which generates an `__init__`
from the model fields. Restly's create/update helpers are aware of that
constructor shape when an `IDRef` / `IDSchema` field has been resolved to an ORM
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

## Input Compatibility

`IDRef` accepts both scalar ids and `{"id": ...}` dictionaries on input:

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

Most examples inherit from `fr.IDSchema`:

```python
class ArticleRead(fr.IDSchema):
    title: str
    author_id: fr.IDRef[Author]
```

As a base class, `IDSchema` is essentially `BaseSchema` with a read-only `id`
field:

```python
class IDSchema(fr.BaseSchema):
    id: fr.ReadOnly[Any]
```

You can inherit from `fr.BaseSchema` instead if you want every field, including
`id`, to be explicit in the schema definition:

```python
class ArticleRead(fr.BaseSchema):
    id: fr.ReadOnly[int]
    title: str
    author_id: fr.IDRef[Author]
```

## Nested Relationship Objects

Some clients model relationships as objects. For that shape, annotate the
relationship field with `fr.IDSchema[Model]`:

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

`IDRef` and `IDSchema[Model]` both validate the referenced row and use the same
resolver. The difference is the API shape.

## Behavior Summary

- `IDRef[Model]` uses scalar id wire format on request and response.
- Missing related IDs return `404`.
- Reference resolution is an **unscoped existence check** (bare PK lookup, no
  `build_query` scoping). Gate cross-tenant / visibility references in
  `authorize` or `before_commit` — see [Visibility and Multi-Tenancy](#visibility-and-multi-tenancy).
- The `_id` field name triggers FK resolution.
- A matching SQLAlchemy relationship lets Restly keep the FK column and
  relationship attribute in sync.
- Dataclass models can be FK-first or relationship-first; Restly supplies the
  constructor values the model requires from the same resolved row.
- If both `author_id` and `author` are explicitly provided, they must refer to
  the same row or the request returns `422`.
- `IDSchema` as a base class adds the resource's own read-only `id` field.
