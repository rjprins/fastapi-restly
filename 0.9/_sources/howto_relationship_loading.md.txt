# Relationship Loading and Async

When an endpoint is async, response serialization cannot use the database. So
any field backed by a relationship must be loaded beforehand; a relationship
that is still unloaded raises `MissingGreenlet`. Restly does this for you in
most cases, through
{meth}`get_relationship_loader_options() <fastapi_restly.views.BaseRestView.get_relationship_loader_options>`:
it eager-loads the relationships your response schema names, on both reads and
writes. This guide covers what loads automatically, how to add more, and what to
do when you still hit `MissingGreenlet`.

:::{note}
FastAPI-Restly uses the term **"schema"** for Pydantic request/response models
and **"model"** for SQLAlchemy ORM models.
:::

## What Restly loads for you

A response-schema field whose name matches a model relationship is loaded
eagerly. Take an article with an author and comments:

```python
class Article(fr.IDBase):
    title: Mapped[str]
    author_id: Mapped[int] = mapped_column(ForeignKey("user.id"), init=False)
    author: Mapped[User] = relationship(default=None, init=False)
    comments: Mapped[list["Comment"]] = relationship(default_factory=list, init=False)
```

Naming `author` and `comments` on the response schema is all it takes. Mark
them `fr.ReadOnly` so they stay out of the generated create and update input,
which does not accept nested objects as write payloads (see
[Custom Schemas and Field Types](howto_custom_schema.md)):

```python
class ArticleRead(fr.IDSchema):
    title: str
    author: fr.ReadOnly[UserRead]
    comments: fr.ReadOnly[list[CommentRead]]
```

Restly inspects the schema, matches `author` and `comments` against the
mapper's relationships, and builds recursive `selectinload(...)` options from
them. It applies those options in
{meth}`get_one <fastapi_restly.views.RestView.get_one>` and
{meth}`get_many <fastapi_restly.views.RestView.get_many>`, and again in
`save_object` after a create or update, because the refresh that follows a
flush leaves relationships unloaded. Nested schemas recurse: if `UserRead` itself names a relationship,
that one loads too.

The reload after a write is skipped when everything the schema names is already
loaded, and it runs without `populate_existing`, so a relationship you assigned
in a hook keeps the value you gave it.

On a **sync** view the same options apply; only the failure mode when something
is missing differs. Sync sessions lazy-load a missing relationship with an
extra query (slower, but it works), while async sessions raise
`MissingGreenlet`.

## Eager-load something the schema does not name

Sometimes you want a relationship loaded that is not part of the response, to
feed a `@property`, an `after_commit` hook, or just to avoid an N+1 in a custom
read. Override
{meth}`get_relationship_loader_options() <fastapi_restly.views.BaseRestView.get_relationship_loader_options>`
and append to it:

```python
from sqlalchemy.orm import selectinload

@fr.include_view(app)
class ArticleView(fr.AsyncRestView):
    prefix = "/articles"
    model = Article
    schema = ArticleRead

    def get_relationship_loader_options(self):
        return super().get_relationship_loader_options() + [
            selectinload(Article.comments).selectinload(Comment.reactions)
        ]
```

The default returns the options derived from the response schema; appending
keeps those schema-driven loads and adds yours. This is the seam that feeds
every path: the extra load applies on reads (`get_one` / `get_many`) and on the
create and update responses alike, because the write path reloads by primary
key through the same options. Return a fresh list to replace the strategy
entirely, for example to swap `selectinload` for `joinedload`.

For a load you only ever read and never serialize, such as a join you filter or
sort on, {meth}`build_query() <fastapi_restly.views.RestView.build_query>` with
`.options(...)` is the lighter place to put it. It shapes the read query only,
so an eager load added there is absent from create and update responses; reach
for it only when that difference does not matter.

## Reach a relationship the schema does not name

Loader options follow the relationships a schema *names*. Code that reaches
past that set runs in plain async context and hits the same wall: an
`after_commit` hook, a custom business method, or a `@property` that walks a
relationship nothing else loads. Restly's declarative base
({class}`fr.DataclassBase <fastapi_restly.models.DataclassBase>`) mixes in
SQLAlchemy's [`AsyncAttrs`](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#sqlalchemy.ext.asyncio.AsyncAttrs),
so any such attribute can be awaited explicitly:

```python
async def after_commit(self, action, new, old=None):
    for comment in await new.awaitable_attrs.comments:
        notify(comment)
```

`awaitable_attrs` is the right tool for a one-off read. When the same
relationship is needed on every request, put it in the response schema or in
`get_relationship_loader_options` instead, so it loads in one batched query
rather than one lazy load at a time.

(missinggreenlet)=

## When you hit `MissingGreenlet`

`sqlalchemy.exc.MissingGreenlet` means SQLAlchemy attempted database IO on an
async session from plain, non-awaited code. Its message is:

> greenlet_spawn has not been called; can't call await_() here. Was IO
> attempted in an unexpected place?

and it links to [sqlalche.me/e/20/xd2s](https://sqlalche.me/e/20/xd2s). Inside a
Restly view this is almost always response serialization reaching an unloaded
relationship, sometimes wrapped in a Pydantic `ValidationError` when it surfaces
in a nested model. For that case, work through these in order:

1. **Is the field in your response schema?** If a serialized field names a
   relationship, that alone loads it. A missing field is usually a name
   mismatch (see the note below).
2. **Is a hook or property reaching it?** Load it explicitly with
   `await obj.awaitable_attrs.<name>`, or add it to
   `get_relationship_loader_options` if every request needs it.
3. **Did you add the load only in `build_query`?** That covers reads but not
   write responses; move it to `get_relationship_loader_options`.

Restly already sets `expire_on_commit=False` on its session factories so a
committed object stays readable during serialization. If you build your own
session maker, keep that default (see
[Session Factory Defaults](technical_details.md#session-factory-defaults)).

The same error covers any implicit async IO, not only relationship
serialization: a lazy-loaded deferred column, an expired attribute read in your
own code, or a synchronous (non-async) driver behind an async engine. When it
fires somewhere other than the response, SQLAlchemy's
[Preventing Implicit IO when Using AsyncSession](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#preventing-implicit-io-when-using-asyncsession)
covers the full set of techniques.

:::{note}
A relationship response field must keep the relationship's own Python name;
only its *wire* name may be aliased. Eager loading matches on the Python field
name, so an outgoing alias is safe (`Field(serialization_alias="ownerInfo")`,
or a camelCase `alias_generator`). But renaming the field itself and bridging
back with `alias` / `validation_alias` (`owner_info: OwnerRead =
Field(alias="owner")`) drops it from both the loader and the write-path reload,
so it lazy-loads and raises `MissingGreenlet` on async.
:::

## See also

- [How Restly serializes nested responses](technical_details.md#nested-response-schemas-vs-write-payloads): the loader mechanism in depth.
- [Work with Foreign Keys and Relationships](howto_relationship_idschema.md): declaring reference fields (`MustExist`, `IDRef`, `IDSchema`).
- [Customize RestView](#eager-load-extra-relationships): read-scoped `build_query` and `get_one` recipes.
- [Session Factory Defaults](technical_details.md#session-factory-defaults): why Restly uses `expire_on_commit=False`.
