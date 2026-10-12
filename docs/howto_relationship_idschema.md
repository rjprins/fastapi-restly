# Relationships

Use {class}`fr.MustExist[int, Model] <fastapi_restly.schemas.MustExist>` for
foreign-key columns. The API will have the common scalar-id shape while
FastAPI-Restly validates that the referenced row exists. Use
{class}`IDRef <fastapi_restly.schemas.IDRef>` or
{class}`IDSchema <fastapi_restly.schemas.IDSchema>` when the schema field names a
relationship instead of a foreign key column.

:::{note}
FastAPI-Restly uses the term **"schema"** for Pydantic request/response models and
**"model"** for SQLAlchemy ORM models.
:::

(reference-exists)=
## Choosing a reference style

Most APIs communicate through ids. They are what forms and dropdowns submit,
they stay cacheable, and clients like [React Admin](howto_react_admin.md) can
dereference them for display. Embed the target object only when you
deliberately want one larger response instead of a separate fetch; see
[Nested Response Schemas vs Write Payloads](#nested-response-internals)
for what embedding supports.

| Declaration | JSON value | SQLAlchemy type |
|---|---|---|
| {class}`author_id: fr.MustExist[int, User] <fastapi_restly.schemas.MustExist>` | `1` | scalar foreign key |
| {class}`author: fr.IDRef[User] <fastapi_restly.schemas.IDRef>` | `1` | resolves to `User` |
| {class}`author: fr.IDSchema[User] <fastapi_restly.schemas.IDSchema>` | `{"id": 1}` | resolves to `User` |
| `author: fr.ReadOnly[UserSchema]` | the full object | read-only relationship embed |
| `author_id: int` | `1` | scalar foreign key |

The three checked forms (`MustExist`, `IDRef`, `IDSchema`) return `404` when
the referenced id does not exist. A schema that exposes the same link through
two relationship references must receive matching ids; conflicting ids return
`422` (see [Dataclass relationship setup](#dataclass-relationship-setup)).

- Use {class}`MustExist <fastapi_restly.schemas.MustExist>` for the common case:
  a `*_id` column you want validated. Name the primary-key type first, then the
  target model, as in `fr.MustExist[int, User]` (`fr.MustExist[UUID, Account]`
  for a UUID key). When the column has a single `ForeignKey`, you can drop the
  model and let Restly infer it, writing just `fr.MustExist[int]`. A plain `int`
  (or `ReadOnly[int]` for a server-stamped column) is the unchecked alternative
  with no existence check.
- Use {class}`IDRef <fastapi_restly.schemas.IDRef>` /
  {class}`IDSchema <fastapi_restly.schemas.IDSchema>` when the field names a
  relationship. Restly resolves the id to the related object; the difference is
  whether the JSON value is flat (`IDRef`) or nested (`IDSchema`).
- In hooks, `data.<field>` is the plain id for
  {class}`MustExist <fastapi_restly.schemas.MustExist>` and `int`; for
  {class}`IDRef <fastapi_restly.schemas.IDRef>` /
  {class}`IDSchema <fastapi_restly.schemas.IDSchema>` it is an unresolved
  reference (read `.id`) that Restly resolves on write.

## Model setup

The examples on this page use two models joined by one foreign key: each
`Article` references its author, a `User`:

```python
import fastapi_restly as fr
from sqlalchemy import ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(fr.ModelDefaults, DeclarativeBase):
    pass


class User(fr.IDMixin, Base):
    name: Mapped[str]


class Article(fr.IDMixin, Base):
    title: Mapped[str]
    author_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
```

{class}`fr.ModelDefaults <fastapi_restly.models.ModelDefaults>` derives the
table name from the class name (`User` becomes `user`, `Article` becomes
`article`). That is why `ForeignKey("user.id")` is correct here.

## Schema setup

With the models in place, declare the foreign-key column on the schema as a
checked reference:

```python
class UserSchema(fr.IDSchema):
    name: str


class ArticleSchema(fr.IDSchema):
    title: str
    author_id: fr.MustExist[int, User]
```

{class}`fr.MustExist[int, User] <fastapi_restly.schemas.MustExist>` marks an
integer foreign key to `User` that must exist. In JSON, it is a plain
scalar:

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

## View setup

The view is registered as usual; the reference behavior comes entirely from
the schema:

```python
@fr.include_view(app)
class ArticleView(fr.AsyncRestView):
    prefix = "/articles"
    model = Article
    schema = ArticleSchema
```

On create and update, Restly looks up the `User` with `id=1`. If it does not
exist, or falls outside the model's `default_scope`, the request returns
`404`; see [Visibility and multi-tenancy](#visibility-and-multi-tenancy)
below. In hooks, `data.author_id` is the plain integer.

## List filtering

The FK field is filterable on the list endpoint by its own public name, as in
`GET /articles?author_id=1`. The suffixed operators `author_id__in`,
`author_id__ne`, and `author_id__isnull` are available as well, and because a
`MustExist[int, ...]` id is a plain integer, so is the range family
`author_id__gte` / `__lte` / `__gt` / `__lt`. See
[Foreign-key filtering](#foreign-key-filtering) for the
full behavior.

## Field naming

Name the schema field after a mapped attribute on the model: a foreign-key
column for {class}`MustExist <fastapi_restly.schemas.MustExist>`, or a
relationship for {class}`IDRef <fastapi_restly.schemas.IDRef>` /
{class}`IDSchema <fastapi_restly.schemas.IDSchema>`. Restly inspects the
SQLAlchemy mapper to decide how to apply the value, so the FK column can be
named anything; the `_id` suffix is a common convention, not a requirement:

```python
author_id: fr.MustExist[int, User]  # the Article.author_id FK column
post_fk: fr.MustExist[int, Post]    # a non-_id column name works the same way
author: fr.IDRef[User]              # the Article.author relationship
```

The Python field name is what Restly matches against the mapped attribute: it
builds the model with that name as a keyword argument, so the two must match. To
use a different name only *in the JSON* (for a camelCase API), keep the field
named after the attribute and add a Pydantic alias:

```python
from pydantic import Field

author_id: fr.MustExist[int, User] = Field(alias="authorId")  # JSON: "authorId"
```

When the field names a relationship, Restly resolves the id to an ORM object and
keeps the relationship and its backing FK column in sync:

| Schema field | FK column | Relationship |
|---|---|---|
| `author` | `Article.author_id` | `Article.author` |

The relationship is found through the mapper, so this pairing holds whatever the
column is called, including a column with an explicit DB name
(`mapped_column("db_name", ...)`). If an FK-named reference has no partner
relationship, or more than one relationship shares the FK column (ambiguous),
Restly sets the FK column and leaves the relationship to you.

## Lists of references

A to-many reference serializes as a plain id array with {class}`list[fr.IDRef[Model]] <fastapi_restly.schemas.IDRef>`:

```python
class OrderSchema(fr.IDSchema):
    customer_name: str
    products: list[fr.IDRef[Product]]  # serializes as [1, 2, 3]
```

On input, each element accepts both raw scalars and `{"id": ...}` shapes, so
the same field doubles as a permissive write-side type when paired with a
custom {meth}`create <fastapi_restly.views.RestView.create>` / {meth}`update <fastapi_restly.views.RestView.update>` business method that resolves the list. For
relationship objects that must stay nested in the JSON, use
{class}`fr.IDSchema[Model] <fastapi_restly.schemas.IDSchema>` (see
[Nested relationship objects](#nested-relationship-objects)).
{class}`MustExist <fastapi_restly.schemas.MustExist>` is for a single scalar FK
column; a to-many field is a relationship, so use
{class}`list[fr.IDRef[Model]] <fastapi_restly.schemas.IDRef>`.

## Input compatibility

{class}`IDRef <fastapi_restly.schemas.IDRef>` and `IDSchema[Model]` accept both
scalar ids and `{"id": ...}` dictionaries on input. The flat form passes the id
directly:

```json
{ "author": 1 }
```

The nested form wraps it in an object:

```json
{ "author": {"id": 1} }
```

The response shape stays with the declared type: {class}`IDRef <fastapi_restly.schemas.IDRef>`
serializes as a scalar, and {class}`IDSchema <fastapi_restly.schemas.IDSchema>`
serializes as `{"id": ...}`. This is useful when clients or migration code
already send one form, but the public API contract should keep the other.

## About IDSchema

Most examples inherit from {class}`fr.IDSchema <fastapi_restly.schemas.IDSchema>`,
which is {class}`BaseSchema <fastapi_restly.schemas.BaseSchema>` plus a
read-only `id` field. The schema bases, `ReadOnly` / `WriteOnly` markers, and
aliases are owned by [Schemas & Validation](howto_custom_schema.md);
inherit from `fr.BaseSchema` instead if you want every field, including `id`,
explicit. When used as a field type (`author: fr.IDSchema[User]`), it is a
nested relationship reference (covered next), separate from its use as a base
class.

## Nested relationship objects

Some clients model relationships as objects. For that shape, annotate the
relationship field with {class}`fr.IDSchema[Model] <fastapi_restly.schemas.IDSchema>`:

```python
class ArticleSchema(fr.IDSchema):
    title: str
    author: fr.IDSchema[User]
```

In JSON, it looks like this:

```json
{
  "title": "Intro",
  "author": {"id": 1}
}
```

{class}`IDRef <fastapi_restly.schemas.IDRef>` and `IDSchema[Model]` both name a
relationship, validate the referenced row, and use the same resolver. The
difference is the API shape: flat id versus nested object.

(relationship-loading)=

## Relationship Loading and Async

When an endpoint is async, response serialization cannot use the database. So
any field backed by a relationship must be loaded beforehand; a relationship
that is still unloaded raises `MissingGreenlet`. Restly does this for you in
most cases, through
{meth}`get_relationship_loader_options() <fastapi_restly.views.BaseRestView.get_relationship_loader_options>`:
it eager-loads the relationships the view's response class names, on both
reads and writes. By default the response class is the view's schema without
its `WriteOnly` fields; see [Your own response class](#own-response-class). This guide covers what loads automatically, how to add more, and what to
do when you still hit `MissingGreenlet`.

:::{note}
FastAPI-Restly uses the term **"schema"** for Pydantic request/response models
and **"model"** for SQLAlchemy ORM models.
:::

### What Restly loads for you

A response-schema field whose name matches a model relationship is loaded
eagerly. Take an article with an author and comments:

```python
class Article(fr.IDMixin, Base):
    title: Mapped[str]
    author_id: Mapped[int] = mapped_column(ForeignKey("user.id"), init=False)
    author: Mapped[User] = relationship(init=False)
    comments: Mapped[list["Comment"]] = relationship(default_factory=list, init=False)
```

Naming `author` and `comments` on the view's schema is all it takes. Mark
them `fr.ReadOnly` so they stay out of the generated create and update input,
which does not accept nested objects as write payloads (see
[Schemas & Validation](howto_custom_schema.md)):

```python
class ArticleSchema(fr.IDSchema):
    title: str
    author: fr.ReadOnly[UserSchema]
    comments: fr.ReadOnly[list[CommentSchema]]
```

Restly inspects the schema, matches `author` and `comments` against the
mapper's relationships, and builds recursive `selectinload(...)` options from
them. It applies those options in
{meth}`get_one <fastapi_restly.views.RestView.get_one>` and
{meth}`get_many <fastapi_restly.views.RestView.get_many>`, and again in
`save_object` after a create or update, because the refresh that follows a
flush leaves relationships unloaded. Nested schemas recurse: if `UserSchema` itself names a relationship,
that one loads too.

The reload after a write is skipped when everything the response class names
is already loaded, and it runs without `populate_existing`, so a relationship you assigned
in a hook keeps the value you gave it.

On a **sync** view the same options apply; only the failure mode when something
is missing differs. Sync sessions lazy-load a missing relationship with an
extra query (slower, but it works), while async sessions raise
`MissingGreenlet`.

### Eager-load something the schema does not name

Sometimes you want a relationship loaded that is not part of the response, to
feed a `@property`, an `after_action_commit` hook, or just to avoid an N+1 in a custom
read. Override
{meth}`get_relationship_loader_options() <fastapi_restly.views.BaseRestView.get_relationship_loader_options>`
and append to it:

```python
from sqlalchemy.orm import selectinload

@fr.include_view(app)
class ArticleView(fr.AsyncRestView):
    prefix = "/articles"
    model = Article
    schema = ArticleSchema

    def get_relationship_loader_options(self):
        return super().get_relationship_loader_options() + [
            selectinload(Article.comments).selectinload(Comment.reactions)
        ]
```

The default returns the options derived from the response class; appending
keeps those loads and adds yours. This is the seam that feeds
every path: the extra load applies on reads (`get_one` / `get_many`) and on the
create and update responses alike, because the write path reloads by primary
key through the same options. Return a fresh list to replace the strategy
entirely, for example to swap `selectinload` for `joinedload`.

For an extra load needed only by the list, add `.options(...)` in
{meth}`apply_list_params <fastapi_restly.views.RestView.apply_list_params>`.
This does not affect `get_one` or create and update responses.

(eager-load-extra-relationships)=

### `get_one`: eager-load extra relationships

The default {meth}`get_one <fastapi_restly.views.RestView.get_one>` loads through the view scope and the loader options derived from the response class. If one endpoint needs an extra relationship, delegate to ``super()`` so the scoped load and its 404 stay intact, then load the extra attribute explicitly. An override declares the ``scope`` parameter and passes it on; the handlers always forward it:

```python
    async def get_one(self, id, *, scope=None):
        obj = await super().get_one(id, scope=scope)   # scoped load + 404
        await obj.awaitable_attrs.audit_log
        return obj
```

Overriding `get_one` this way applies the extra load to reads only. To
eager-load a relationship on the create/update response as well, override
{meth}`get_relationship_loader_options <fastapi_restly.views.BaseRestView.get_relationship_loader_options>`
instead; see [Relationship Loading and Async](#relationship-loading).

### Reach a relationship the schema does not name

Loader options follow the relationships a schema *names*. Code that reaches
past that set runs in plain async context and hits the same wall: an
`after_action_commit` hook, a custom business method, or a `@property` that walks a
relationship nothing else loads.
{class}`fr.ModelDefaults <fastapi_restly.models.ModelDefaults>` mixes
SQLAlchemy's [`AsyncAttrs`](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#sqlalchemy.ext.asyncio.AsyncAttrs)
into your base, so any such attribute can be awaited explicitly:

```python
async def after_action_commit(self, action, new, old=None):
    for comment in await new.awaitable_attrs.comments:
        notify(comment)
```

`awaitable_attrs` is the right tool for a one-off read. When the same
relationship is needed on every request, put it in the response class or in
`get_relationship_loader_options` instead, so it loads in one batched query
rather than one lazy load at a time.

(missinggreenlet)=

### When you hit `MissingGreenlet`

`sqlalchemy.exc.MissingGreenlet` means SQLAlchemy attempted database IO on an
async session from plain, non-awaited code. Its message is:

> greenlet_spawn has not been called; can't call await_() here. Was IO
> attempted in an unexpected place?

and it links to [sqlalche.me/e/20/xd2s](https://sqlalche.me/e/20/xd2s). Inside a
Restly view this is almost always response serialization reaching an unloaded
relationship, sometimes wrapped in a Pydantic `ValidationError` when it surfaces
in a nested model. For that case, work through these in order:

1. **Is the field in the response class?** If a serialized field names a
   relationship, that alone loads it. A `WriteOnly` field is not in the
   response class, so its relationship does not load. A missing field is usually a name
   mismatch (see the note below).
2. **Is a hook or property reaching it?** Load it explicitly with
   `await obj.awaitable_attrs.<name>`, or add it to
   `get_relationship_loader_options` if every request needs it.
3. **Did you add the load only in `get_one`, `get_many`, or `apply_list_params`?**
   Write responses do not call those methods. Move the load to
   `get_relationship_loader_options`.

Restly already sets `expire_on_commit=False` on its session factories so a
committed object stays readable during serialization. If you build your own
session maker, keep that default (see
[Session Factory Defaults](#session-factory-defaults)).

The same error covers any implicit async IO, not only relationship
serialization: a lazy-loaded deferred column, an expired attribute read in your
own code, or a synchronous (non-async) driver behind an async engine. When it
fires somewhere other than the response, SQLAlchemy's
[Preventing Implicit IO when Using AsyncSession](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#preventing-implicit-io-when-using-asyncsession)
covers the full set of techniques.

:::{note}
A relationship response field must keep the relationship's own Python name;
only its *JSON* name may be aliased. Eager loading matches on the Python field
name, so an outgoing alias is safe (`Field(serialization_alias="ownerInfo")`,
or a camelCase `alias_generator`). But renaming the field itself and bridging
back with `alias` / `validation_alias` (`owner_info: OwnerSchema =
Field(alias="owner")`) drops it from both the loader and the write-path reload,
so it lazy-loads and raises `MissingGreenlet` on async.
:::

## Dataclass relationship setup

On a base with {class}`fr.ModelDefaults <fastapi_restly.models.ModelDefaults>`,
the models are dataclasses: SQLAlchemy's `MappedAsDataclass` generates an
`__init__` from the model fields.
Restly passes that constructor the values it requires, whether the schema
writes the foreign key, the relationship, or both. You can declare the foreign
key and the relationship with any `init` and `default` options:

```python
author_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
author: Mapped["User"] = relationship(default=None)
```

A relationship the request does not send never overrides a foreign key it
does send. If such a relationship is `None` after construction, for example
from `default=None`, Restly removes the `None`. Otherwise SQLAlchemy's flush
would write `NULL` over the foreign key.

Optional references work the same way. A field typed
`author: fr.IDRef[User] | None` accepts an explicit `null`, and may be omitted
when it defaults to `None`. Unless another field sends the foreign key, the
row gets a `NULL` foreign key. A `null` for a `NOT NULL` column fails in the
database, and the request returns the standard `409`, not a server error.

If a schema exposes the same link as two reference fields, for example a
FK-named {class}`IDRef <fastapi_restly.schemas.IDRef>` field alongside the
relationship, Restly validates that they match:

```json
{
  "author_id": 1,
  "author": {"id": 1}
}
```

Conflicting references, such as `"author_id": 1` with `"author": {"id": 2}`,
return `422`. Explicit `null` also participates in this check: `author_id: 1`
with `author: null` is a conflict, while omitting `author` entirely is not. A
plain {class}`MustExist <fastapi_restly.schemas.MustExist>` scalar does not take
part in this reference-pair check; it is a checked column value. When a
request sends it together with a different relationship value, including
`null`, Restly saves the relationship value.

### Standard SQLAlchemy declarative models

If your base is a plain `DeclarativeBase`, without `fr.ModelDefaults`, the
dataclass constructor rules do not apply:

```python
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Article(Base):
    __tablename__ = "article"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]
    author_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    author: Mapped["User"] = relationship()
```

There is no generated `__init__` contract to satisfy: Restly constructs the
object and applies the resolved reference to the FK column (and the matching
relationship attribute, when one is declared) directly. Registering such models
with Restly is covered in [Use Your Existing Models](#existing-models).

(idref-custom-routes)=

## Reference fields in custom routes

The default `POST` and `PATCH` routes validate the body before Restly calls
`make_new_object()` or `update_object()`, so reference fields are already in the
right shape. A {class}`MustExist <fastapi_restly.schemas.MustExist>` field is a
plain id; {class}`IDRef[Model] <fastapi_restly.schemas.IDRef>` and
{class}`IDSchema[Model] <fastapi_restly.schemas.IDSchema>` fields are reference
instances.

In a [custom route](customize.md#add-a-custom-action-route), be
careful when you construct a schema yourself. Pydantic's `model_construct()`
skips validation. For a
{class}`MustExist <fastapi_restly.schemas.MustExist>` field, pass the plain id:

```python
from fastapi_restly.objects import async_make_new_object


link_schema = TaskLabelSchema.model_construct(
    task_id=request.task_id,
    label_id=label.id,
)

task_label = await async_make_new_object(
    self.session,
    TaskLabel,
    link_schema,
)
```

This keeps the existence-check path active: Restly verifies the referenced rows
exist and writes the FK columns. It helps when validated construction would
require response-only fields such as `id` or timestamps.

If those fields were {class}`IDRef <fastapi_restly.schemas.IDRef>` /
{class}`IDSchema <fastapi_restly.schemas.IDSchema>` relationship references
instead, wrap them explicitly before calling the object helper:

```python
link_schema = TaskLabelSchema.model_construct(
    task=fr.IDRef[Task](id=request.task_id),
    label=fr.IDRef[Label](id=label.id),
)
```

If you instead use {class}`IDSchema[Model] <fastapi_restly.schemas.IDSchema>` as a nested relationship-object field in a custom response schema, serialize the ORM object through {meth}`self.to_single_response(obj) <fastapi_restly.views.BaseRestView.to_single_response>` before returning it:

```python
class TaskLabelNestedSchema(fr.IDSchema):
    task: fr.IDSchema[Task]
    label: fr.IDSchema[Label]


@fr.post("/attach", response_model=TaskLabelNestedSchema, status_code=201)
async def attach(self, request: AttachRequest):
    obj = await create_task_label(...)
    return self.to_single_response(obj)
```

The raw ORM object usually has scalar FK columns, while a nested schema expects
relationship-shaped data. Scalar fields
({class}`MustExist <fastapi_restly.schemas.MustExist>`,
{class}`IDRef <fastapi_restly.schemas.IDRef>`) do not need this step because
their JSON value is already scalar.

(visibility-and-multi-tenancy)=
## Visibility and multi-tenancy

Reference resolution applies the referenced model's `default_scope`, the
clause declared on its namespace ([Scopes](scopes.md)): a reference to a
row the scope hides returns 404. A session-level tenant rule also applies to
the reference lookup because it is an ORM `SELECT`. See
[tenant row scoping](#tenant-row-scoping) for that invariant. A per-field
override (check against another clause, or none) is spelled with
{class}`RefExists <fastapi_restly.schemas.RefExists>`; see
[References: overriding per field](#reference-scopes).

A model **without** a `default_scope` resolves by bare primary key
(`session.get(User, id)`): tenant, soft-delete, and row-level visibility
checks are then your responsibility. Gate them in
{meth}`authorize <fastapi_restly.views.RestView.authorize>` /
{meth}`before_action_commit <fastapi_restly.views.RestView.before_action_commit>` like any
other write-path authorization; both hooks are described in
[Request Lifecycle & Hooks](#authorize-gate).

Gate in `authorize`, where `data` carries the write-side value before
resolution. For a {class}`MustExist <fastapi_restly.schemas.MustExist>` field,
`data.author_id` is the requested id. For
{class}`IDRef <fastapi_restly.schemas.IDRef>` /
{class}`IDSchema <fastapi_restly.schemas.IDSchema>`, `data.<field>.id` is the
requested id (and a list field is a list of references):

```python
@fr.include_view(app)
class ArticleView(fr.AsyncRestView):
    prefix = "/articles"
    model = Article
    schema = ArticleSchema

    async def authorize(self, action, obj=None, data=None):
        if data is not None and data.author_id is not None:
            if not await self.author_visible(data.author_id):
                # 404 (not 403) so you don't leak that the id exists elsewhere.
                raise fr.exc.NotFound("author not found")
```

The resolved ORM object is not available in `authorize`; resolution runs later
in the business method. If you need the resolved row, check in `before_action_commit`,
where the built object carries it (for example `new.author.org_id`). Prefer
`authorize` when the requested id is enough: it rejects before the unscoped
fetch and is the standard policy seam.

## Internals

(nested-response-internals)=

### Nested Response Schemas vs Write Payloads

Nested schemas serve two different roles in Restly today:

- **Response serialization** is supported. The CRUD views recursively build
  `selectinload(...)` options for nested relationship fields in the response
  schema, so related objects can be serialized efficiently and with aliases.
  Reads apply those options in `get_one` / `get_many`; writes apply the same
  ones in `save_object`, because the refresh that follows a flush leaves
  relationships unloaded. Without that, serializing a create or update response
  would reach them one lazy query at a time, which on an async session raises
  `MissingGreenlet` rather than merely costing queries. The reload is skipped
  when everything the response class names is already loaded, and it runs without
  `populate_existing`, so a relationship the caller has already populated keeps
  its value.

  Loader options follow relationships the response class *names*. Code that reaches
  past that set -- an `after_action_commit` hook, a custom business method, a
  `@property` walking a relationship nothing else loads -- runs in plain async
  context, where a bare attribute access raises `MissingGreenlet`.
  {class}`fr.ModelDefaults <fastapi_restly.models.ModelDefaults>` mixes in
  SQLAlchemy's `AsyncAttrs` for exactly that case, so those reads can be
  spelled `await obj.awaitable_attrs.items`. The
  task-focused guide to all of this, including extending the loaded set and
  resolving `MissingGreenlet`, is
  [Relationship Loading and Async](#relationship-loading); this section
  covers the mechanism behind it.
- **Create/update payloads** are not supported in the general case. The default
  `make_new_object()` / `update_object()` flow expects payload keys to map
  directly to model attributes, with `*_id: fr.MustExist[int, Model]`
  (see [MustExist](howto_custom_schema.md#mustexist)) as the FK
  case. For a relationship-named field, after resolving an {class}`IDRef <fastapi_restly.schemas.IDRef>` / {class}`IDSchema <fastapi_restly.schemas.IDSchema>` to an ORM object, Restly chooses the FK
  scalar, relationship object, or both based on the model constructor. If the
  client supplies both fields, Restly checks they refer to the same row.

If you declare a nested input field like `address: AddressSchema` on a write
schema, the default CRUD implementation will pass that nested Pydantic object
through to the SQLAlchemy model constructor or attribute setter, which usually
does not match the ORM model shape. Use a flattened schema or override the
{meth}`create() <fastapi_restly.views.RestView.create>` / {meth}`update() <fastapi_restly.views.RestView.update>` business methods to transform the payload first.
[Relationships](howto_relationship_idschema.md)
describes the supported reference-field patterns.

## See also

- [Relationship Loading and Async](#relationship-loading): how these
  relationship fields load for responses, and `MissingGreenlet` fixes on async.
- [Build a Blog API](tutorial.md): reference fields in the blog build, in context.
- The [shop example](https://github.com/rjprins/fastapi-restly/tree/main/example-projects/shop): relationships and several primary-key
  styles, in a running app.
- [Schemas & Validation](howto_custom_schema.md): the schema bases
  and field markers these compose with.
- [API Reference](api_reference.md): {class}`MustExist <fastapi_restly.schemas.MustExist>` / {class}`IDRef <fastapi_restly.schemas.IDRef>` / {class}`IDSchema <fastapi_restly.schemas.IDSchema>` signatures and the
  full symbol tables.

- [Session Factory Defaults](#session-factory-defaults): why Restly uses `expire_on_commit=False`.
