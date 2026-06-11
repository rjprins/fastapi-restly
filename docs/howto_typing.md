# Use Type Annotations

FastAPI-Restly supports two typing styles:

- **Low-friction mode**: keep your view classes simple and let the framework do the work.
- **Stronger typing mode**: add explicit type parameters when you want better editor help for the methods you override.

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

The generic form is useful when you override methods on one of the three tiers.
Most overrides land on the **business verbs**, so those benefit most:

- `get_one`
- `create`
- `update`
- `delete`
- `get_many`

It also sharpens the **request handlers** (`handle_create`, `handle_update`,
`handle_get_one`, …) and the cooperative stamping methods (`make_new_object`,
`update_object`) when you override them.

Without view generics, these methods still work, but their types are broader.
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
    schema_create = UserCreate
    schema_update = UserUpdate

    async def get_one(self, id: int) -> User:
        return await super().get_one(id)

    async def create(self, schema_obj: UserCreate) -> User:
        return await super().create(schema_obj)

    async def update(self, obj: User, schema_obj: UserUpdate) -> User:
        return await super().update(obj, schema_obj)
```

The type parameters are, in order: the **model**, the **response schema**, the
**create schema**, the **update schema**, and the **id** type. With them in place,
`schema_obj` in `create` is a `UserCreate`, `obj` in `update` is a `User`, and the
return types are checked too.

Note the signatures: `create` takes the create schema and returns the model;
`update` takes the already-loaded `obj` plus the update schema (id resolution and
the 404 happen one tier up, in `handle_update`). If you instead override at the
handler tier, the id-taking signatures live there:

```python
    async def handle_update(self, id: int, schema_obj: UserUpdate) -> User:
        return await super().handle_update(id, schema_obj)
```

This looks heavier because it is more explicit. Use it when that extra precision
is valuable to you.

`fr.BaseSchema` is a convenient default, not a hard requirement for input
schemas. Explicit `schema_create` and `schema_update` classes may inherit
directly from `pydantic.BaseModel` when you do not need Restly's schema helpers.

---

## A good rule of thumb

Use the simplest form that gives you the typing help you want:

- **No generics at all** for normal CRUD views
- **`IDRef[RelatedModel]`** for foreign-key fields
- **`IDSchema[RelatedModel]`** only when you intentionally want a nested relationship object field
- **View generics** only when you want precise typing on the methods you override

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

When a custom action reuses the standard machinery — for example loading the
target with `get_one(id)` and running a write through `handle_update(id, schema)`
— parameterizing the view gives those calls precise types too.

---

## What the type checker cannot fully model

FastAPI-Restly does some runtime work to register and annotate view methods for FastAPI.
This includes copying inherited endpoints and adjusting signatures during view registration.

That runtime behavior is part of how the framework works, but static type checkers do
not understand every detail of that process.

The practical takeaway is:

- Type checkers are best at the **public contract**: models, schemas, `IDSchema[...]`,
  `IDRef[...]`, class attributes, and the methods on the three tiers (`get_one`,
  `create`, `handle_update`, and so on).
- Type checkers are less useful for the internal signature-rewriting machinery that
  produces the route shells (`*_endpoint`).

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
- Parameterized views are optional and mainly help when you override methods on
  the three tiers (business verbs, request handlers, stamping methods).
- Custom route methods work well with ordinary Python annotations.
