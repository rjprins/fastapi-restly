# How-To: Work with Foreign Keys Using IDSchema

Use `IDSchema[...]` in your schema for relation IDs that should resolve to real SQLAlchemy objects.

## Model Setup

```python
class Author(fr.IDBase):
    name: Mapped[str]

class Article(fr.IDBase):
    title: Mapped[str]
    author_id: Mapped[int] = mapped_column(ForeignKey("author.id"))
```

## Schema Setup

```python
class AuthorSchema(fr.IDSchema[Author]):
    name: str

class ArticleSchema(fr.IDSchema[Article]):
    title: str
    author_id: fr.IDSchema[Author]
```

## Request Example

```json
{
  "title": "Intro",
  "author_id": { "id": 1 }
}
```

## Behavior

- FastAPI-Restly resolves `author_id` to an `Author` instance.
- Missing related IDs return `404`.
- Writes keep FK values and relation assignment in sync.

