# Use Type Annotations

FastAPI-Restly supports two typing styles. In the low-friction style, you keep
your view classes simple and let the framework do the work; in the stronger
typing style, you add explicit type parameters when you want better editor
help for the methods you override. This guide focuses on practical usage with
Pyright and VS Code with Pylance.

## Start simple

For normal CRUD usage, you do not need to parameterize
{class}`RestView <fastapi_restly.views.RestView>` or
{class}`AsyncRestView <fastapi_restly.views.AsyncRestView>`. A bare view class
is the recommended starting point:

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

## Use `IDSchema` for response schemas

{class}`IDSchema <fastapi_restly.schemas.IDSchema>` is the usual base for
response schemas because it adds the resource's own read-only `id` field. You
can subclass it directly:

```python
class UserRead(fr.IDSchema):
    name: str
    email: str
```

You can also parameterize it when you want the schema class itself to carry
the SQLAlchemy model type:

```python
class UserRead(fr.IDSchema[User]):
    name: str
    email: str
```

For most top-level response schemas, either form is fine; the bare form is the
recommended starting point.

For a `*_id` foreign-key column, use
{class}`fr.MustExist[int, Model] <fastapi_restly.schemas.MustExist>`, which
takes the primary-key type first and then the model; it keeps the plain scalar
id and adds an existence check. For a field named after a relationship, use
{class}`IDRef[RelatedModel] <fastapi_restly.schemas.IDRef>`, which tells
Restly (and the type checker) which model resolves from the scalar id payload.
The runtime semantics are covered in
[Work with Foreign Keys and Relationships](howto_relationship_idschema.md).

## When view generics are useful

Schemas are one half of the picture;
{class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` and
{class}`RestView <fastapi_restly.views.RestView>` can be parameterized too,
but this is optional.

The generic form is useful when you override methods on one of the
[three tiers](customize.md#the-three-tiers). Most overrides land on
the business verbs, so those benefit most:

- {meth}`get_one <fastapi_restly.views.RestView.get_one>`
- {meth}`create <fastapi_restly.views.RestView.create>`
- {meth}`update <fastapi_restly.views.RestView.update>`
- {meth}`delete <fastapi_restly.views.RestView.delete>`
- {meth}`get_many <fastapi_restly.views.RestView.get_many>`

It also sharpens the request handlers ({meth}`handle_create <fastapi_restly.views.RestView.handle_create>`,
{meth}`handle_update <fastapi_restly.views.RestView.handle_update>`,
{meth}`handle_get_one <fastapi_restly.views.RestView.handle_get_one>`, and so
on) and the cooperative stamping methods (`make_new_object`, `update_object`)
when you override them.

Without view generics, these methods still work, but their types are broader.
With view generics, your editor can infer the concrete model, schema, and id
types. Here is a fully parameterized view with typed overrides on the business
verbs:

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

The type parameters are, in order: the model, the response schema, the create
schema, the update schema, and the id type. With them in place, `schema_obj`
in `create` is a `UserCreate`, `obj` in `update` is a `User`, and the return
types are checked too.

Note the signatures: `create` takes the create schema and returns the model;
`update` takes the already-loaded `obj` plus the update schema (id resolution
and the 404 happen one tier up, in `handle_update`). If you instead override
at the handler tier, the id-taking signatures live there:

```python
    async def handle_update(self, id: int, schema_obj: UserUpdate) -> User:
        return await super().handle_update(id, schema_obj)
```

This looks heavier because it is more explicit. Use it when that extra
precision is valuable to you.

{class}`fr.BaseSchema <fastapi_restly.schemas.BaseSchema>` is a convenient
default, not a hard requirement for input schemas. Explicit
{attr}`schema_create <fastapi_restly.views.BaseRestView.schema_create>` and
{attr}`schema_update <fastapi_restly.views.BaseRestView.schema_update>`
classes may inherit directly from `pydantic.BaseModel` when you do not need
Restly's schema helpers.

## Do custom routes need generics?

Custom route methods do not need view generics unless they depend on strongly
typed handler interactions. Consider a small extra endpoint:

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User

    @fr.get("/health")
    async def health(self) -> dict[str, str]:
        return {"status": "ok"}
```

For extra endpoints like this, normal Python return annotations are usually
enough.

When a custom action reuses the standard machinery, for example loading the
target with {meth}`get_one(id) <fastapi_restly.views.RestView.get_one>` and
running a write through {meth}`handle_update(id, schema) <fastapi_restly.views.RestView.handle_update>`,
parameterizing the view gives those calls precise types too.

## What the type checker cannot fully model

FastAPI-Restly does some runtime work to register and annotate view methods
for FastAPI. This includes copying inherited endpoints and adjusting
signatures during
[view registration](technical_details.md#view-classes-and-registration).
That runtime behavior is part of how the framework works, but static type
checkers do not understand every detail of that process.

The practical takeaway is:

- Type checkers are best at the public contract: models, schemas,
  {class}`IDSchema[...] <fastapi_restly.schemas.IDSchema>`,
  {class}`IDRef[...] <fastapi_restly.schemas.IDRef>`, class attributes, and
  the methods on the three tiers
  ({meth}`get_one <fastapi_restly.views.RestView.get_one>`,
  {meth}`create <fastapi_restly.views.RestView.create>`,
  {meth}`handle_update <fastapi_restly.views.RestView.handle_update>`, and so
  on).
- Type checkers are less useful for the internal signature-rewriting machinery
  that produces the
  [endpoint methods](customize.md#replace-an-endpoint-method-to-change-the-http-contract)
  (`*_endpoint`).

You usually do not need to care about that distinction unless you are
modifying the framework itself.

## Check your project with Pyright

If typing quality matters in your project, we recommend checking your Restly
usage with Pyright. The repository keeps a dedicated set of consumer typing
fixtures under `tests/typing/`, and those examples are checked in strict mode.

## Which form should I use?

With both styles in hand, the rule of thumb is to use the simplest form that
gives you the typing help you want:

- For normal CRUD views, use no generics at all: bare
  {class}`RestView <fastapi_restly.views.RestView>` and
  {class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` are the default.
- Use {class}`fr.MustExist[int, Model] <fastapi_restly.schemas.MustExist>` for
  `*_id` foreign-key columns and
  {class}`IDRef[RelatedModel] <fastapi_restly.schemas.IDRef>` for
  relationship-named reference fields.
- Use {class}`IDSchema[Model] <fastapi_restly.schemas.IDSchema>` as a field
  annotation only when you intentionally want a nested relationship-object
  field. Parameterizing your top-level schema's *base class*, as in
  `class UserRead(IDSchema[User])`, is a separate, optional choice: the bare
  base is supported, and the parameterized base carries the model type.
- Use view generics only when you want precise typing on the methods you
  override; they mainly help on the three tiers (business verbs, request
  handlers, and stamping methods).
- Custom route methods work well with ordinary Python annotations.

That keeps everyday usage clean while still allowing stricter typing for
projects that want it.

## See also

- [View Method Surface](api_reference.md#view-method-surface): the typed
  methods this page parameterizes, with tier classification.
- [Customize RestView](customize.md): the override recipes
  these signatures apply to.
- [Work with Foreign Keys and Relationships](howto_relationship_idschema.md):
  runtime semantics of
  {class}`fr.MustExist[int, Model] <fastapi_restly.schemas.MustExist>` and
  {class}`IDRef <fastapi_restly.schemas.IDRef>` /
  {class}`IDSchema[Model] <fastapi_restly.schemas.IDSchema>` fields.
