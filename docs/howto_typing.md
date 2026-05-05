# How-To: Use Type Annotations with FastAPI-Restly

FastAPI-Restly supports two typing styles:

- **Low-friction mode**: keep your view classes simple and let the framework do the work.
- **Stronger typing mode**: add explicit type parameters when you want better editor help for `perform_*` overrides.

This guide focuses on practical usage with Pyright and VS Code with Pylance.

---

## Start simple

For normal CRUD usage, you do **not** need to parameterize `RestView` or `AsyncRestView`.

```python
import fastapi_restly as fr
from fastapi import FastAPI
from sqlalchemy.orm import Mapped

app = FastAPI()


class User(fr.IDBase):
    name: Mapped[str]
    email: Mapped[str]


@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
```

This is the recommended starting point.

---

## Use `IDSchema` for response schemas

You can subclass `IDSchema` directly:

```python
class UserRead(fr.IDSchema):
    name: str
    email: str
```

`IDSchema` is the usual base for response schemas because it adds the resource's
own read-only `id` field. You can parameterize it when you want the schema class
itself to carry the SQLAlchemy model type:

```python
class UserRead(fr.IDSchema[User]):
    name: str
    email: str
```

For most top-level response schemas, either form is fine. The bare form is the
recommended starting point.

For foreign-key fields, use `IDRef[RelatedModel]`:

```python
class ArticleRead(fr.IDSchema):
    title: str
    author_id: fr.IDRef[User]
```

That tells Restly which model should be resolved from the scalar id payload.

---

## When view generics are useful

`AsyncRestView` and `RestView` can be parameterized, but this is optional.

The generic form is useful when you override handlers such as:

- `perform_get`
- `perform_create`
- `perform_update`
- `perform_delete`
- `perform_listing`

Without view generics, these handlers still work, but their types are broader.
With view generics, your editor can infer the concrete model, schema, and id types.

```python
class UserRead(fr.IDSchema[User]):
    name: str
    email: str


class UserCreate(fr.BaseSchema):
    name: str
    email: str


class UserUpdate(fr.BaseSchema):
    name: str
    email: str


@fr.include_view(app)
class UserView(
    fr.AsyncRestView[User, UserRead, UserCreate, UserUpdate, int]
):
    prefix = "/users"
    model = User
    schema = UserRead
    creation_schema = UserCreate
    update_schema = UserUpdate

    async def perform_get(self, id: int) -> User:
        return await super().perform_get(id)

    async def perform_create(self, schema_obj: UserCreate) -> User:
        return await super().perform_create(schema_obj)

    async def perform_update(self, id: int, schema_obj: UserUpdate) -> User:
        return await super().perform_update(id, schema_obj)
```

This looks heavier because it is more explicit. Use it when that extra precision
is valuable to you.

`fr.BaseSchema` is a convenient default, not a hard requirement for input
schemas. Explicit `creation_schema` and `update_schema` classes may inherit
directly from `pydantic.BaseModel` when you do not need Restly's schema helpers.

---

## A good rule of thumb

Use the simplest form that gives you the typing help you want:

- **No generics at all** for normal CRUD views
- **`IDRef[RelatedModel]`** for foreign-key fields
- **`IDSchema[RelatedModel]`** only when you intentionally want a nested relationship object field
- **View generics** only when you want precise `perform_*` handler typing

That keeps everyday usage clean while still allowing stricter typing for
projects that want it.

---

## Custom routes are straightforward

Custom route methods do not need view generics unless they depend on strongly
typed handler interactions.

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User

    @fr.get("/health")
    async def health(self) -> dict[str, str]:
        return {"status": "ok"}
```

For extra endpoints like this, normal Python return annotations are usually enough.

---

## What the type checker cannot fully model

FastAPI-Restly does some runtime work to register and annotate view methods for FastAPI.
This includes copying inherited endpoints and adjusting signatures during view registration.

That runtime behavior is part of how the framework works, but static type checkers do
not understand every detail of that process.

The practical takeaway is:

- Type checkers are best at the **public contract**: models, schemas, `IDSchema[...]`,
  `IDRef[...]`, class attributes, and `perform_*` handlers.
- Type checkers are less useful for the internal signature-rewriting machinery.

You usually do not need to care about that distinction unless you are modifying
the framework itself.

---

## Recommended target

If typing quality matters in your project, we recommend checking your Restly usage
with Pyright. The repository keeps a dedicated set of consumer typing fixtures under
`tests/typing/`, and those examples are checked in strict mode.

---

## Summary

- Bare `IDSchema` is supported.
- `IDRef[Model]` is preferred for foreign-key fields.
- `IDSchema[Model]` is available for nested relationship-object fields.
- Bare `RestView` / `AsyncRestView` are the default.
- Parameterized views are optional and mainly help with `perform_*` handler typing.
- Custom route methods work well with ordinary Python annotations.
