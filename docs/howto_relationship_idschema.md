# How-To: Work with Foreign Keys Using IDSchema

Use `fr.IDSchema[Model]` as a schema field type when you want clients to reference a related object by ID, and have FastAPI-Restly resolve it to a real SQLAlchemy instance automatically.

## Naming Convention

Two requirements must be met for automatic resolution to work:

1. The schema field name must end in `_id` (for example, `author_id`).
2. The SQLAlchemy model must have a relationship attribute with the same name minus the `_id` suffix (for example, `author`).

When both conditions are met, the view sets both the FK column (`author_id`) and the relationship attribute (`author`) on the new or updated object. If the relationship attribute is absent, only the FK column is set.

## Model Setup

```python
import fastapi_restly as fr
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey

class Author(fr.IDBase):
    name: Mapped[str]

class Article(fr.IDBase):
    title: Mapped[str]
    author_id: Mapped[int] = mapped_column(ForeignKey("author.id"))
    author: Mapped["Author"] = relationship(default=None, init=False)
```

`fr.IDBase` is the convenience alias built on `fr.DataclassBase`, so it still auto-generates the table name from the class name (`Author` → `author`, `Article` → `article`). That is why `ForeignKey("author.id")` is correct here.

### Why `init=False` and `default=None` are required

`fr.IDBase` uses SQLAlchemy's `MappedAsDataclass`, which auto-generates an `__init__` from the model's field declarations. Any attribute that is not marked `init=False` becomes a constructor parameter.

Relationship attributes should **not** be constructor parameters — SQLAlchemy loads them lazily from the database via the foreign key column. If you omit `init=False`, SQLAlchemy will expect the related object to be passed directly to `Article(...)`, which is not how FK-based construction works.

`default=None` is the companion requirement: without a default value, the generated `__init__` would require the relationship as a positional argument, making it impossible to construct the object at all.

The correct declaration is always:

```python
author: Mapped["Author"] = relationship(default=None, init=False)
```

### Plain (non-dataclass) models

If you use `fr.PlainBase` / `fr.PlainIDBase` instead of `fr.IDBase`, the dataclass constraint does not apply. Plain models use SQLAlchemy's traditional declarative style and accept any keyword arguments in `__init__`, so `init=False` is not needed:

```python
class Article(fr.PlainIDBase):
    __tablename__ = "article"
    title: Mapped[str]
    author_id: Mapped[int] = mapped_column(ForeignKey("author.id"))
    author: Mapped["Author"] = relationship()  # no init=False needed
```

## Schema Setup

```python
import fastapi_restly as fr

class AuthorSchema(fr.IDSchema):
    name: str

class ArticleSchema(fr.IDSchema):
    title: str
    author_id: fr.IDSchema[Author]
```

## View Setup

```python
@fr.include_view(app)
class ArticleView(fr.AsyncRestView):
    prefix = "/articles"
    model = Article
    schema = ArticleSchema
```

## Request Format

The client sends the related object's primary key wrapped in an object:

```json
{
  "title": "Intro",
  "author_id": {"id": 1}
}
```

The view looks up the `Author` with `id=1` and raises `404` if it does not exist.

## Behavior

- The `id` inside the `{"id": 1}` payload is the foreign key value provided by the client, not the article's own primary key.
- FastAPI-Restly resolves `author_id` to an `Author` ORM instance before creating or updating the object.
- Both the FK column (`author_id`) and the relationship (`author`) are kept in sync on write, provided the `author` relationship exists on the model.
- On dataclass-based models, the framework detects `init=False` on the relationship attribute and skips passing it to `__init__`. The FK column is still set, so SQLAlchemy will populate the relationship on the next access.
- Missing related IDs return `404`.
