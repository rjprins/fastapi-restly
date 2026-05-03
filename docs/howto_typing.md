# How-To: Use Type Annotations with FastAPI-Restly

FastAPI-Restly supports two typing styles:

- **Low-friction mode**: keep your view classes simple and let the framework do the work.
- **Stronger typing mode**: add explicit type parameters when you want better editor help for `handle_*` overrides.

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

## Use `IDSchema` with or without a model type

You can subclass `IDSchema` directly:

```python
class UserSchema(fr.IDSchema):
    name: str
    email: str
```

When you want the schema to refer to a specific SQLAlchemy model, you can also
parameterize it:

```python
class UserSchema(fr.IDSchema[User]):
    name: str
    email: str
```

For most top-level response schemas, either form is fine.

For relationship ID fields, prefer the model-aware form:

```python
class ArticleSchema(fr.IDSchema):
    title: str
    author_id: fr.IDSchema[User]
```

That tells Restly which model should be resolved from the `{"id": ...}` payload.

---

## When view generics are useful

`AsyncRestView` and `RestView` can be parameterized, but this is optional.

The generic form is useful when you override handlers such as:

- `handle_get`
- `handle_create`
- `handle_update`
- `handle_delete`
- `handle_list`

Without view generics, these handlers still work, but their types are broader.
With view generics, your editor can infer the concrete model, schema, and id types.

```python
class UserSchema(fr.IDSchema[User]):
    name: str
    email: str


class UserCreateSchema(fr.BaseSchema):
    name: str
    email: str


class UserUpdateSchema(fr.BaseSchema):
    name: str
    email: str


@fr.include_view(app)
class UserView(
    fr.AsyncRestView[User, UserSchema, UserCreateSchema, UserUpdateSchema, int]
):
    prefix = "/users"
    model = User
    schema = UserSchema
    creation_schema = UserCreateSchema
    update_schema = UserUpdateSchema

    async def handle_get(self, id: int) -> User:
        return await super().handle_get(id)

    async def handle_create(self, schema_obj: UserCreateSchema) -> User:
        return await super().handle_create(schema_obj)

    async def handle_update(self, id: int, schema_obj: UserUpdateSchema) -> User:
        return await super().handle_update(id, schema_obj)
```

This looks heavier because it is more explicit. Use it when that extra precision
is valuable to you.

---

## A good rule of thumb

Use the simplest form that gives you the typing help you want:

- **No generics at all** for normal CRUD views
- **`IDSchema[RelatedModel]`** for relationship ID fields
- **View generics** only when you want precise `handle_*` handler typing

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
  class attributes, and `handle_*` handlers.
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
- `IDSchema[Model]` is preferred for relationship ID fields.
- Bare `RestView` / `AsyncRestView` are the default.
- Parameterized views are optional and mainly help with `handle_*` handler typing.
- Custom route methods work well with ordinary Python annotations.
