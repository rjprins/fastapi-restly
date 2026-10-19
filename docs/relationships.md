# Relationships

A field can refer to another row. Restly handles such a field in both
directions:

- In a request, the client sends the id of the other row. On create and
  update, Restly looks the id up. If the row does not exist, the request
  returns `404`. Otherwise Restly writes the foreign key or the relationship.
- In a response, the field shows the other row as an id or as a full object.
  Restly loads every relationship that the response class names before it
  serializes the response, so this also works on an async view.

Restly reads the SQLAlchemy mapper to decide what a field writes. A field
named after a foreign key column writes that column. A field named after a
relationship writes the relationship, and Restly sets the foreign key column
of that relationship to match.

The type of the field says what kind of reference it is:
{class}`fr.MustExist <fastapi_restly.schemas.MustExist>` for a foreign key
column, and {class}`fr.IDRef <fastapi_restly.schemas.IDRef>` or
{class}`fr.IDSchema <fastapi_restly.schemas.IDSchema>` for a relationship.

:::{note}
FastAPI-Restly uses the word **schema** for a Pydantic request or response
model, and **model** for a SQLAlchemy ORM model.
:::

(reference-styles)=
## Choose a reference style

Most APIs send ids. Forms and dropdowns submit them, and a client such as
[React Admin](react_admin.md) can fetch the row behind an id to show it.
Show the whole object only when one larger response is better for the client
than a second request.

| Declaration | JSON value | What the field is |
|---|---|---|
| {class}`author_id: fr.MustExist[int, User] <fastapi_restly.schemas.MustExist>` | `1` | a foreign key column, checked |
| {class}`author: fr.IDRef[User] <fastapi_restly.schemas.IDRef>` | `1` | a relationship, checked |
| {class}`author: fr.IDSchema[User] <fastapi_restly.schemas.IDSchema>` | `{"id": 1}` | a relationship, checked |
| `author: fr.ReadOnly[UserSchema]` | the full object | a relationship, in responses only |
| `author_id: int` | `1` | a foreign key column, not checked |

- Use `MustExist` for the common case: a `*_id` column that must point to a
  row that exists.
- Use `IDRef` or `IDSchema` when the field is a relationship. The two differ
  only in the JSON: a flat id for `IDRef`, an object with an `id` for
  `IDSchema`.
- Use a nested schema to show the whole object in responses; see
  [Return related objects in a response](#relationship-loading).
- A plain `int` writes the column without a check. If the row is missing,
  the database rejects the foreign key, and the request returns `409` (see
  [database conflicts](#default-exception-handling)).

## Accept the id of another row

The examples on this page use two models. Each `Article` has an author, a
`User`:

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
table name from the class name, so `User` gets the table `user`. That is why
`ForeignKey("user.id")` is correct.

Declare the foreign key column on the schema with `MustExist`. Name the type
of the primary key first, then the model:

```python
class UserSchema(fr.IDSchema):
    name: str


class ArticleSchema(fr.IDSchema):
    title: str
    author_id: fr.MustExist[int, User]


@fr.include_view(app)
class ArticleView(fr.AsyncRestView):
    prefix = "/articles"
    model = Article
    schema = ArticleSchema
```

The view needs nothing else. In JSON, `author_id` is a plain number, in the
request and in the response:

```json
{
  "id": 10,
  "title": "Intro",
  "author_id": 1
}
```

On create and update, Restly checks that the `User` with id `1` exists. In
your own code, such as `authorize`, `data.author_id` is a plain integer.

The blog example runs the same pattern for the comments of a post, in
[`blog/part1.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/blog/blog/part1.py).

(reference-exists)=
## Check that the referenced row exists

`MustExist`, `IDRef` and `IDSchema` each check on create and update that the
referenced row exists. A missing row returns `404`, and the message names the
field and the id:

```json
{"detail": "Id not found for author_id: 99"}
```

:::{admonition} Details
:class: details

- Restly checks the references after `authorize` and before your `create` or
  `update`. So the errors come in this order: the row in the URL (`404`),
  then the permission (`403`), then the references (`404`).
- Restly checks the data of every write: the CRUD routes, and
  `write_action(data=...)`. It also checks the schemas inside the data, such
  as the lines of an order, and each item when the data is a list. The
  message then names the place: `Id not found for lines[1].product_id: 7`.
- One query checks all the ids of one model. Restly checks only the fields
  that the client sent, and it skips a `null` value. In one write, Restly
  does not look up the same row twice.
- The check does not autoflush, so it never writes an object that your code
  is still building. So it also does not find a row that your code added
  and did not flush yet. Call `flush()` first for such a row.
- A list of ids, such as `list[fr.MustExist[int, Tag]]`, checks each id. The
  message names every missing id of the list:
  `Id not found for tag_ids: [7, 9]`. A reference deeper in another type,
  such as in a `dict`, is refused, because the check cannot see it there.
- The builders in {mod}`fr.objects <fastapi_restly.objects>` check too, so
  a script that builds objects with them is safe.
- An id that reaches the database in another way is yours to check, such as
  a value that your code sets in a `write_action` block without `data=`. The
  foreign key in the database still refuses an id that does not exist.
- For another primary key type, name that type first:
  `fr.MustExist[UUID, Account]`. Restly does not take the model from the
  foreign key, because a schema can also write another model, and then the
  check would use the wrong table.
:::

(visibility-and-multi-tenancy)=
## Which rows can a reference reach?

A reference check applies the
[default scope](#default-scope) of the referenced model. A reference to a row
that the scope hides returns `404`, as a missing row does. So if the scope of
`User` hides deleted users, a deleted user cannot become the author of a new
article.

A session rule, such as the [tenant rule](#tenant-row-scoping),
also reaches every reference check.

A model without a default scope is checked by its primary key alone. Then a
reference can reach a soft-deleted row, or a row that the caller cannot read.
To hide such rows, give the model a default scope.

The `scope` of a view does not apply to references. It is a rule for the
reads of that one view. If a rule must also hold for references, make it the
`default_scope` of the model.

:::{admonition} Details
:class: details

A script that calls the {mod}`fr.objects <fastapi_restly.objects>` builders
by itself loads an `IDRef` or `IDSchema` row of a model without a default
scope with `session.get()`. For an object that is already in the session,
`session.get()` returns it without a query, so a session rule does not run
there. Inside a write, Restly always runs the query.

A rule that is neither a scope nor a session rule goes in
{meth}`authorize <fastapi_restly.views.RestView.authorize>` (see
[Authorization](#authorize-gate)). There, `data` holds the requested id,
before Restly looks it up: `data.author_id` for a `MustExist` field, and
`data.author.id` for an `IDRef` or `IDSchema` field. A list field holds a list
of references.

```python
class Current(fr.ContextNamespace):
    user_id: fr.ContextParam[int]
    is_editor: fr.ContextParam[bool]


@fr.include_view(app)
class ArticleView(fr.AsyncRestView):
    prefix = "/articles"
    model = Article
    schema = ArticleSchema

    async def authorize(self, action, obj=None, data=None):
        if action == fr.Action.CREATE and data.author_id != Current.user_id():
            if not Current.is_editor():
                raise fr.exc.Forbidden("Only an editor can name another author")
```

`authorize` does not have the row itself. If a rule needs it, read it in
{meth}`before_action_commit <fastapi_restly.views.RestView.before_action_commit>`,
for example with `await self.session.get(User, new.author_id)`.

The SaaS example lets a member attach labels only to a task that is assigned
to them. That is a permission, so it is in `TaskLabelView.authorize`, in
[`app/labels/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/labels/views.py).
:::

(reference-field-names)=
## Name the field after the model attribute

Restly matches the Python name of the field against the model. A `MustExist`
field names a foreign key column. An `IDRef` or `IDSchema` field names a
relationship. The column can have any name. The `_id` suffix is common, but
Restly does not need it:

```python
author_id: fr.MustExist[int, User]  # the Article.author_id column
post_fk: fr.MustExist[int, Post]    # a column without _id works the same way
author: fr.IDRef[User]              # the Article.author relationship
```

To use another name in the JSON only, for example for a camelCase API, keep
the Python name and add a Pydantic alias:

```python
from pydantic import Field

author_id: fr.MustExist[int, User] = Field(alias="authorId")  # JSON: "authorId"
```

When the field names a to-one relationship, Restly sets the relationship to
the row it looked up, and sets the foreign key column of the relationship to
its id. Restly finds that column through the mapper, so its name does not
matter, also when the column has its own database name
(`mapped_column("db_name", ...)`).

(nested-reference)=
## Reference a relationship: a flat id or an object

Name the relationship on the schema, and choose the JSON shape with the type.
`IDRef` gives a flat id. `IDSchema[Model]` gives an object with an `id`, for
clients that model a relationship as an object:

```python
class ArticleSchema(fr.IDSchema):
    title: str
    author: fr.IDSchema[User]
```

```json
{
  "title": "Intro",
  "author": {"id": 1}
}
```

:::{admonition} Details
:class: details

Both types check the row and look it up in the same way. In a request, each
type accepts both forms: `{"author": 1}` and `{"author": {"id": 1}}`. The
response always uses the declared form. This helps when some clients already
send the other form.

In your code, `data.author` is a reference object, not the row. Read the
requested id as `data.author.id`. Restly looks the row up when it writes.

`IDSchema` is also the usual base class of a schema. As a base class, it only
adds a read-only `id` field; [Schemas & Validation](schemas.md)
covers that use.
:::

(reference-lists)=
## Reference several rows

A to-many relationship takes a list of ids with `list[fr.IDRef[Model]]`. The
shop example uses one for the products of an order, next to a `MustExist`
for its customer:

```{literalinclude} ../example-projects/shop/shop/main.py
:language: python
:start-at: class OrderSchema(fr.TimestampsSchemaMixin, fr.IDSchema)
:end-at: products: list[fr.IDRef[Product]]
```

In JSON the list is an array of ids, such as `"products": [1, 2, 3]`. Each
item of a request can also be an object, such as `{"id": 1}`. On create and update,
Restly looks up all ids in one query, returns `404` if one is missing, and
sets the relationship to those rows. A repeated id counts once. A list
relationship stores no order: responses show the rows in database order,
unless the relationship sets `order_by`. The full file is
[`shop/main.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/shop/shop/main.py).

A plain list of ids that sets no relationship, such as the projects that a
route body names, is `list[fr.MustExist[int, Project]]`. Restly checks each
id in one query, and the list stays a list of ints.

(model-relationship-setup)=
## Declare the foreign key and the relationship on the model

On a base with {class}`fr.ModelDefaults <fastapi_restly.models.ModelDefaults>`,
each model is a dataclass: SQLAlchemy's `MappedAsDataclass` generates an
`__init__` from the fields. Restly passes that constructor the values it
needs, whether the schema writes the foreign key, the relationship, or both.
So you can declare both with any `init` and `default` options:

```python
author_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
author: Mapped["User"] = relationship(default=None)
```

:::{admonition} Details
:class: details

A relationship that the request does not send never replaces a foreign key
that it does send. If that relationship is `None` after the constructor ran,
for example from `default=None`, Restly removes the `None`. Otherwise
SQLAlchemy would write `NULL` over the foreign key when it flushes.

A reference can be optional. A field typed `author: fr.IDRef[User] | None`
accepts an explicit `null`, and the client can leave it out when it defaults
to `None`. Then the row gets a `NULL` foreign key, unless another field sends
it. A `null` for a `NOT NULL` column fails in the database, which returns
`409`.

A schema can name both sides, the foreign key and the relationship, as two
reference fields. Then the two must name the same row:

```json
{
  "author_id": 1,
  "author": {"id": 1}
}
```

Two different rows, such as `"author_id": 1` with `"author": {"id": 2}`,
return `422`. An explicit `null` counts too: `"author_id": 1` with
`"author": null` returns `422`. Leaving `author` out does not. This check needs
both fields to be `IDRef` or `IDSchema`. A `MustExist` field does not take
part: when a request sends it with a relationship value, including `null`,
Restly saves the relationship value.

On a plain `DeclarativeBase` without `fr.ModelDefaults`, the models are not
dataclasses, and there is no generated `__init__` to satisfy:

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

Restly builds the object and sets the foreign key column, and the
relationship when there is one. [Use Your Existing Models](#existing-models)
covers models like these.
:::

## Filter a list by a reference

A `MustExist` field filters the list endpoint by its own name, as in
`GET /articles?author_id=1`. An `IDSchema[Model]` field filters by its id, as
in `GET /articles?author.id=1`. That key has a dot, so the view turns it on
first, for example with `include_filters = ("*", "author.*")`, as
[Choose the keys of a list](#choose-list-keys) shows. A flat `IDRef` on a
relationship gets no filter. [Filter by a foreign key](#foreign-key-filtering) lists the operators
that each one gets.

(idref-custom-routes)=
## Build references in a custom route

The default `POST` and `PATCH` routes validate the body first. So when the
default `create` or `update` builds the object, every reference field has the
right type: a `MustExist` field holds a plain id, and an `IDRef` or `IDSchema`
field holds a reference object.

A [custom route](#custom-action-route) that builds a schema object itself must
give each field that type. Pydantic's `model_construct()` builds a schema
object without validation. It helps when the schema has required fields that
only responses fill, such as `id` or timestamps. For a `MustExist` field, pass
the plain id:

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

`async_make_new_object` still checks that the rows exist, and writes the
foreign key columns. A schema that validates, such as
`self.schema_create(task_id=..., label_id=...)`, needs none of this, and a
handler can take it. The SaaS example's `POST /task-labels/create-and-attach`
route does that, in
[`app/labels/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/labels/views.py).

:::{admonition} Details
:class: details

For an `IDRef` or `IDSchema` field, build the reference object yourself:

```python
link_schema = TaskLabelSchema.model_construct(
    task=fr.IDRef[Task](id=request.task_id),
    label=fr.IDRef[Label](id=label.id),
)
```

A custom route can also return a relationship as an object. Give its
`response_model` an `IDSchema[Model]` field on the relationship, and return
the ORM object. The response then reads that relationship, so on an async
view load it first:

```python
class TaskLabelLinks(fr.IDSchema):
    task: fr.IDSchema[Task]
    label: fr.IDSchema[Label]


@fr.post("/attach", response_model=TaskLabelLinks, status_code=201)
async def attach(self, request: AttachRequest):
    task_label = await create_task_label(...)
    await task_label.awaitable_attrs.task
    await task_label.awaitable_attrs.label
    return task_label
```

{meth}`self.to_single_response(obj) <fastapi_restly.views.BaseRestView.to_single_response>`
returns the response class of the view, not this route's class. Use it only
when the route returns the view's own response class.
:::

(relationship-loading)=
## Return related objects in a response

On an async view, serializing the response cannot use the database. So every
field that a relationship fills must be loaded first. An unloaded
relationship raises `MissingGreenlet`. Restly loads them for you in most
cases: it eager-loads each relationship that the view's response class names,
on reads and on writes. By default the response class is the view's schema
without its `WriteOnly` fields; see [Set your own response class](#own-response-class).

### What Restly loads for you

A field of the response class whose name matches a relationship of the model
is loaded. Take an article with an author and comments:

```python
class Article(fr.IDMixin, Base):
    title: Mapped[str]
    author_id: Mapped[int] = mapped_column(ForeignKey("user.id"), init=False)
    author: Mapped[User] = relationship(init=False)
    comments: Mapped[list["Comment"]] = relationship(default_factory=list, init=False)
```

Name `author` and `comments` on the view's schema, and Restly loads them.
Mark them `fr.ReadOnly`, so they stay out of the create and update bodies:

```python
class ArticleSchema(fr.IDSchema):
    title: str
    author: fr.ReadOnly[UserSchema]
    comments: fr.ReadOnly[list[CommentSchema]]
```

:::{admonition} Details
:class: details

Nested schemas count too: if `UserSchema` names a relationship of `User`, that
relationship loads as well.

`GET /articles` and `GET /articles/{id}` load these relationships with the
row. A create or an update loads them again after the write, because the
refresh after the flush unloads them.

On a **sync** view, the same loads apply. A missing load does not fail there:
the sync session loads the relationship with one more query. On an async view,
the same missing load raises `MissingGreenlet`.
:::

### Embedded objects are read-only

A nested schema shows a related object in responses. It cannot take one in a
create or update body: Restly would pass the nested Pydantic object to the
model as it is, and the model does not accept it. That is why the fields
above are `fr.ReadOnly`.

To let clients choose the related row, send its id with `MustExist`, `IDRef`
or `IDSchema[Model]`. To create related rows from one body, use a flat input
schema, or override the {meth}`create() <fastapi_restly.views.RestView.create>`
and {meth}`update() <fastapi_restly.views.RestView.update>` business methods
to build the related objects yourself.

(eager-load-extra-relationships)=
### Load a relationship that the response does not name

Some code needs a relationship that is not part of the response: a
`@property`, an `after_action_commit` hook, a value in
{meth}`to_single_response <fastapi_restly.views.BaseRestView.to_single_response>`,
or a custom read that would otherwise run one query per row. Override
{meth}`get_loader_options() <fastapi_restly.views.BaseRestView.get_loader_options>`
and add to its result:

```python
from sqlalchemy.orm import selectinload

@fr.include_view(app)
class ArticleView(fr.AsyncRestView):
    prefix = "/articles"
    model = Article
    schema = ArticleSchema

    def get_loader_options(self):
        return super().get_loader_options() + [
            selectinload(Article.comments).selectinload(Comment.reactions)
        ]
```

Every read of the view starts from
{meth}`select() <fastapi_restly.views.BaseRestView.select>`, which applies
these options: `GET /`, `GET /{id}`, the load of `PATCH` and `DELETE`, and
your own routes. The save after a write applies them too, so the create and
update responses have the same loads.

:::{admonition} Details
:class: details

The default returns the loads that the response class needs. Adding to it
keeps those loads. To replace them, return a new list, for example to use
`joinedload` instead of `selectinload`.

The method holds every loader option of a read, not only relationships. A
`query_expression()` on the model gets its value here, with
`with_expression(...)`; see [Add a value to each row](#value-on-each-row).

For a load that only the list needs, add `.options(...)` in
{meth}`apply_list_params <fastapi_restly.views.RestView.apply_list_params>`.
It does not change `GET /{id}`, or the create and update responses. A load
that only one custom route needs goes in that route: await the relationship
after the load, as the next section shows.

Up to 0.11, this method was named `get_relationship_loader_options`. A view
class that still defines that name fails when Python creates the class, with
a message that names the new one.
:::

(read-unnamed-relationship)=
### Read a relationship in a hook or a business method

Code outside the response, such as an `after_action_commit` hook, a business
method or a `@property`, can reach a relationship that nothing loaded. On an
async view that raises `MissingGreenlet`.
{class}`fr.ModelDefaults <fastapi_restly.models.ModelDefaults>` adds
SQLAlchemy's
[`AsyncAttrs`](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#sqlalchemy.ext.asyncio.AsyncAttrs)
to your base, so you can await such an attribute:

```python
async def after_action_commit(self, action, new, old=None):
    for comment in await new.awaitable_attrs.comments:
        notify(comment)
```

Use `awaitable_attrs` for one read. If every request needs the relationship,
add it to the response class or to `get_loader_options`
instead. Then it loads in one query for all rows, not with one query per
attribute.

(missinggreenlet)=
### Fix `MissingGreenlet`

`sqlalchemy.exc.MissingGreenlet` means that code without `await` tried to
use the database through an async session. The message is:

> greenlet_spawn has not been called; can't call await_() here. Was IO
> attempted in an unexpected place?

It links to [sqlalche.me/e/20/xd2s](https://sqlalche.me/e/20/xd2s). In a
Restly view, the cause is almost always a response that reads a relationship
that is not loaded. When it comes from a nested model, Pydantic can wrap it
in a `ValidationError`. Check these in order:

1. **Is the field in the response class?** A field that names a relationship
   is enough to load it. A `WriteOnly` field is not in the response class, so
   its relationship does not load. A field that should load but does not
   usually has the wrong name; see the note below.
2. **Does a hook, a property or `to_single_response` read it?** Load it with
   `await obj.awaitable_attrs.<name>`. If every request needs it, add it to
   `get_loader_options`.
3. **Did you add the load only in `apply_list_params` or in a custom
   route?** The other routes and the create and update responses do not get
   loads from there. Move the load to `get_loader_options`.
4. **Does a route read it after a `write_action` block?** The save after the
   block unloads every relationship that the loader options do not load and
   the model does not load eagerly, also one that the route loaded before
   the block. Read it with
   `await obj.awaitable_attrs.<name>`, or add it to `get_loader_options`.

:::{note}
A relationship field in a response must keep the Python name of the
relationship. Only its JSON name may change. The loads match the Python name
of the field, so an alias for the output is safe:
`Field(serialization_alias="ownerInfo")`, or a camelCase `alias_generator`.
But a field with another Python name that maps back with `alias` or
`validation_alias`, such as `owner_info: OwnerSchema = Field(alias="owner")`,
is not loaded, after a read or after a write. On an async view it raises
`MissingGreenlet`.
:::

:::{admonition} Details
:class: details

Restly's session factories set `expire_on_commit=False`, so an object stays
readable after the commit. Restly warns when the async session factory that
you pass has `expire_on_commit=True`; see
[Session Factory Defaults](#session-factory-defaults).

The same error comes from any database access without `await`, not only from
relationships: a deferred column, an expired attribute that your code reads,
or a sync database driver behind an async engine. For those cases, see
SQLAlchemy's
[Preventing Implicit IO when Using AsyncSession](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#preventing-implicit-io-when-using-asyncsession).
:::

## Internals

(nested-response-internals)=
### How Restly loads the relationships of a response

{meth}`get_loader_options() <fastapi_restly.views.BaseRestView.get_loader_options>`
walks the fields of the response class. For each field whose name is a
relationship of the model, it adds a `selectinload(...)` option. When the
field type is a nested schema, it walks that schema against the related
model in the same way, and adds the result as child options.

{meth}`select() <fastapi_restly.views.BaseRestView.select>` adds these
options to every read. On a write, the save after the business method
flushes and then refreshes the object. The refresh reloads the columns, but
it unloads the relationships that load lazily, so a changed foreign key never
shows an old related row. The save also unloads the relationships of the
other rows of the view's model that the action wrote. Then one query by
primary key reloads those rows with all the loader options, and the object
too when the view has loader options. It does not apply the scope, and it
does not use `populate_existing`, so it fills only what is not loaded. A related object
that is already in the session keeps the values it has.

### How Restly writes a reference

Before it builds or changes the object, Restly looks up the rows behind the
`IDRef` and `IDSchema` fields, and checks the ids of the `MustExist` fields.
It then routes each value by the mapper. A relationship gets the row. A
foreign key column gets the id. When a field has a partner, the relationship
of a column or the column of a relationship, Restly sets that partner too.
On a dataclass model, a value goes to the constructor when `__init__` takes
it, and is set on the object after the constructor otherwise.

## See also

- [Schemas & Validation](schemas.md): the schema bases and field
  markers that these types work with.
- [Filtering & Pagination](filtering.md): filters on references
  and on related fields.
- [Build a Blog API](tutorial.md): `MustExist` in the blog, step by step.
- The [shop example](https://github.com/rjprins/fastapi-restly/tree/main/example-projects/shop):
  to-one and to-many references, and integer and UUID primary keys, in a
  running app.
- {doc}`api/schemas`: the signatures of
  {class}`MustExist <fastapi_restly.schemas.MustExist>`,
  {class}`IDRef <fastapi_restly.schemas.IDRef>` and
  {class}`IDSchema <fastapi_restly.schemas.IDSchema>`.
