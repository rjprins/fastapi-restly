# Type Annotations

Restly is typed for Pyright, VS Code with Pylance, and mypy. Its views and
its reference types are generic classes, and they take type parameters in two
places:

- **Reference fields** name the model they point to, as in
  `fr.MustExist[int, User]`, `fr.IDRef[User]` and `fr.IDSchema[User]`. Restly
  also reads this parameter at runtime, to find the row. A type checker sees
  the type of the value that your code gets.
- **Views** take optional type parameters, as in
  `fr.AsyncRestView[User, UserSchema, UserCreate, UserUpdate, int]`. They
  change nothing at runtime. They give exact types to the methods that you
  override and call.

A view without type parameters works, and it is the best place to start. Add
parameters where you want your editor to know the model and the schemas.

Restly also rewrites the signatures of the endpoint methods when it registers
a view. Type checkers do not see that step; see
[What a type checker cannot see](#typing-limits).

## Start without type parameters

For normal CRUD, a bare view class is enough:

```python
import fastapi_restly as fr
from fastapi import FastAPI
from sqlalchemy.orm import DeclarativeBase, Mapped

app = FastAPI()


class Base(fr.ModelDefaults, DeclarativeBase):
    pass


class User(fr.IDMixin, Base):
    name: Mapped[str]
    email: Mapped[str]


@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
```

(typing-schemas)=
## Type the schema and its reference fields

The view's schema usually subclasses
{class}`fr.IDSchema <fastapi_restly.schemas.IDSchema>`; see
[Choose a base class](#schema-base-classes). You can also give the base class
the model, as in `fr.IDSchema[User]`:

```python
class UserSchema(fr.IDSchema[User]):
    name: str
    email: str
```

This form records the model for type checkers. It has no effect at runtime,
and the bare `fr.IDSchema` works just as well.

A reference field has the model as a type parameter. The type checker then
knows what your code receives in `data.<field>`, for example in a business
method:

| Field | What the type checker sees |
|---|---|
| `author_id: fr.MustExist[int, User]` | `int`: the plain id |
| `author: fr.IDRef[User]` | `IDRef[User]`: a reference; read `.id` |
| `author: fr.IDSchema[User]` | `IDSchema[User]`: a reference; read `.id` |
| `products: list[fr.IDRef[Product]]` | a list of `IDRef[Product]` |

{class}`fr.MustExist <fastapi_restly.schemas.MustExist>` takes the type of
the primary key first, then the model: `fr.MustExist[UUID, Account]` for a
UUID key. When to use which form, and what each one does at runtime, is on
[Relationships](howto_relationship_idschema.md).

(view-generics)=
## Type the methods that you override

{class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` and
{class}`RestView <fastapi_restly.views.RestView>` take five type parameters,
in this order:

| Parameter | What it types | Default |
|---|---|---|
| the model | the objects that the methods load, build and return | `Any` |
| the response type | the return value of {meth}`to_single_response <fastapi_restly.views.BaseRestView.to_single_response>` | `BaseSchema` |
| the create body | `schema_obj` in {meth}`create <fastapi_restly.views.RestView.create>` | `pydantic.BaseModel` |
| the update body | `schema_obj` in {meth}`update <fastapi_restly.views.RestView.update>` | `pydantic.BaseModel` |
| the id type | `id` in {meth}`get_one <fastapi_restly.views.RestView.get_one>` and the handlers | `int` |

Each parameter has a default, so you can stop after the ones you need. The
starter project names the model and the schema
([`app/users/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/starter/app/users/views.py)):

```python
class UserView(fr.AsyncRestView[User, UserSchema]):
    prefix = "/users"
    model = User
    schema = UserSchema
```

The parameters help most where you override
[a view method](#view-request-steps), usually a business method:
`get_many`, `get_one`, `create`, `update` or `delete`. Here is a view with all
five parameters and typed overrides:

```python
from sqlalchemy import ColumnElement


class UserSchema(fr.IDSchema[User]):
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
    fr.AsyncRestView[User, UserSchema, UserCreate, UserUpdate, int]
):
    prefix = "/users"
    model = User
    schema = UserSchema
    schema_create = UserCreate
    schema_update = UserUpdate

    async def get_one(
        self, id: int | ColumnElement[bool], *, scope: fr.views.ReadScope = None
    ) -> User:
        return await super().get_one(id, scope=scope)

    async def create(self, schema_obj: UserCreate) -> User:
        return await super().create(schema_obj)

    async def update(self, obj: User, schema_obj: UserUpdate) -> User:
        return await super().update(obj, schema_obj)
```

With these parameters, `schema_obj` in `create` is a `UserCreate`, `obj` in
`update` is a `User`, and the type checker checks the return types too. Note
these points:

- `create` takes the create body and returns the model. `update` takes the
  loaded `obj` and the update body. The handler
  {meth}`handle_update <fastapi_restly.views.RestView.handle_update>` loads
  the row, and returns 404 if it does not exist, before `update` runs.
- The `id` of `get_one` is `int | ColumnElement[bool]`, because a SQLAlchemy
  expression can take the place of the primary key, as in
  [Look a row up by another key](#natural-key-route). If you annotate it as
  `id: int`, the type checker rejects the override, because it accepts less
  than the base method.
- `get_one` and `get_many` take a keyword argument `scope`, which the
  handlers always pass. An override must declare it and pass it on.
- For the response type, give the view's schema: the response class that
  Restly builds is a subclass of it. A view that sets its own
  {attr}`schema_response <fastapi_restly.views.BaseRestView.schema_response>`
  gives that class instead, as in `fr.AsyncRestView[User, UserResponse]`.

A project base view can stay generic in the model, so each view that
subclasses it names its own model. The SaaS example does this
([`app/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/views.py)):

```python
from typing import TypeVar

ModelT = TypeVar("ModelT")


class AuthenticatedView(fr.AsyncRestView[ModelT]):
    ...


class ProjectView(AuthenticatedView[Project]):
    prefix = "/projects"
    model = Project
```

## Type a custom route

A custom route needs no view type parameters. Normal Python annotations are
enough:

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User

    @fr.get("/health")
    async def health(self) -> dict[str, str]:
        return {"status": "ok"}
```

When a custom route calls the view's methods, the parameters type those
calls too: {meth}`get_one(id) <fastapi_restly.views.RestView.get_one>`
returns the model, and
{meth}`handle_update(id, schema_obj) <fastapi_restly.views.RestView.handle_update>`
takes the update body.

The classes that
{func}`derive_schema_response() <fastapi_restly.schemas.derive_schema_response>`
returns cannot be used in an annotation; see
[Name the view's classes in a custom route](#name-the-response-classes).

(typing-limits)=
## What a type checker cannot see

- Type checkers work well on the public API: models, schemas, reference
  types, class attributes, and the view methods such as
  {meth}`get_one <fastapi_restly.views.RestView.get_one>`,
  {meth}`create <fastapi_restly.views.RestView.create>` and
  {meth}`handle_update <fastapi_restly.views.RestView.handle_update>`.
- They help less with the
  [endpoint methods](#replace-endpoint-method) (`*_endpoint`), whose
  signatures Restly fills in at registration.


## Check your project

To check your use of Restly, run Pyright or mypy on your project. Restly
keeps a set of small example apps in
[`tests/typing/`](https://github.com/rjprins/fastapi-restly/tree/main/tests/typing).
CI checks them with Pyright and with mypy, both in strict mode. They show each typed form on this page, and they are a good
place to look when your type checker complains.

## Internals

Restly does some work at runtime when it registers a view. It copies the
inherited endpoint methods, and it changes their signatures for FastAPI; see
[view registration](#view-registration-internals). Static type checkers do
not see this.

## See also

- [View Reference](#view-method-surface): the methods that the type
  parameters type, sorted by kind.
- [Request Lifecycle & Hooks](lifecycle.md): which method to override for
  each change.
- [Relationships](howto_relationship_idschema.md): what
  {class}`fr.MustExist <fastapi_restly.schemas.MustExist>`,
  {class}`fr.IDRef <fastapi_restly.schemas.IDRef>` and
  {class}`fr.IDSchema[Model] <fastapi_restly.schemas.IDSchema>` do at
  runtime.
- [Schemas & Validation](howto_custom_schema.md): the schema base classes and
  the response class.
