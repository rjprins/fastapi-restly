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
class AuthorSchema(fr.IDSchema):
    name: str


class ArticleSchema(fr.IDSchema):
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
    schema = ArticleSchema
```

On create and update, Restly looks up the `Author` with `id=1`. If it does not
exist, the request returns `404`.

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
where the dataclass constructor accepts it and keeps `author` in sync after
construction.

If your model is relationship-first, Restly adapts there too:

```python
author_id: Mapped[int] = mapped_column(ForeignKey("author.id"), init=False)
author: Mapped["Author"] = relationship(default=None)
```

In that shape, Restly passes the resolved `Author` object to the constructor and
keeps `author_id` in sync. More generally, Restly supplies the constructor
values your dataclass model requires. For one resolved reference, it may pass the
FK scalar, the relationship object, or both if both dataclass fields are
required. When both are supplied by Restly, they are derived from the same
database row.

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
class ArticleSchema(fr.IDSchema):
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
class ArticleSchema(fr.BaseSchema):
    id: fr.ReadOnly[int]
    title: str
    author_id: fr.IDRef[Author]
```

## Nested Relationship Objects

Some clients model relationships as objects. For that shape, annotate the
relationship field with `fr.IDSchema[Model]`:

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

`IDRef` and `IDSchema[Model]` both validate the referenced row and use the same
resolver. The difference is the API shape.

## Behavior Summary

- `IDRef[Model]` uses scalar id wire format on request and response.
- Missing related IDs return `404`.
- The `_id` field name triggers FK resolution.
- A matching SQLAlchemy relationship lets Restly keep the FK column and
  relationship attribute in sync.
- Dataclass models can be FK-first or relationship-first; Restly supplies the
  constructor values the model requires from the same resolved row.
- If both `author_id` and `author` are explicitly provided, they must refer to
  the same row or the request returns `422`.
- `IDSchema` as a base class adds the resource's own read-only `id` field.
