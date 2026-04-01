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
- Missing related IDs return `404`.
