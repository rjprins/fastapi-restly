# Foreign keys and relationships

Reference another row from a schema with one of a small set of field types. The
default is a checked scalar foreign key, {class}`fr.MustExist[Model] <fastapi_restly.schemas.MustExist>`:
the API stays in the common scalar-id shape while FastAPI-Restly validates that
the referenced row exists. Relationship-shaped variants cover the rest.

:::{note}
FastAPI-Restly uses **schema** for Pydantic request/response models and
**model** for SQLAlchemy ORM models.
:::

## Choosing a reference style

A foreign key is a pointer. On the wire you can send the id or embed the target.
Restly sends the id by default: it is what forms and dropdowns submit, it stays
cacheable, and clients like React Admin dereference it for display. Embed the
target only to spare a read-heavy client a round trip — one larger response
traded for a separate, cacheable fetch.

| Declaration | Wire format | In Python | Use it for |
|---|---|---|---|
| `author_id: fr.MustExist[Author]` | `1` | the scalar id | **the default** — a checked FK column |
| `author: fr.IDRef[Author]` | `1` | resolves to the `Author` | a relationship, flat-id wire |
| `author: fr.IDSchema[Author]` | `{"id": 1}` | resolves to the `Author` | a relationship, nested-id wire (JSON-API / React Admin) |
| `author: AuthorRead` | the full object | resolves to the `Author` | embedding the related object to spare a fetch |
| `author_id: int` | `1` | the scalar id | an unchecked FK, e.g. a server-stamped `ReadOnly` column |

- Reach for {class}`MustExist <fastapi_restly.schemas.MustExist>` for the common
  case: a `*_id` column you want validated. For a non-`int` primary key, name the
  type — `fr.MustExist[Account, UUID]`.
- Use {class}`IDRef <fastapi_restly.schemas.IDRef>` /
  {class}`IDSchema <fastapi_restly.schemas.IDSchema>` when the field names a
  **relationship**, where Restly resolves the id to the related object — flat
  ({class}`IDRef <fastapi_restly.schemas.IDRef>`) or nested
  ({class}`IDSchema <fastapi_restly.schemas.IDSchema>`) on the wire.
- In hooks, `data.<field>` is the plain id for
  {class}`MustExist <fastapi_restly.schemas.MustExist>` and `int`; for
  {class}`IDRef <fastapi_restly.schemas.IDRef>` /
  {class}`IDSchema <fastapi_restly.schemas.IDSchema>` it is an unresolved
  reference (read `.id`) that Restly resolves on write.

## A checked foreign key

```python
import fastapi_restly as fr
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column


class Author(fr.IDBase):
    name: Mapped[str]


class Article(fr.IDBase):
    title: Mapped[str]
    author_id: Mapped[int] = mapped_column(ForeignKey("author.id"))
```

{class}`fr.IDBase <fastapi_restly.models.IDBase>` auto-generates the table name from the class name (`Author` →
`author`, `Article` → `article`). That is why `ForeignKey("author.id")` is
correct here.

```python
class AuthorRead(fr.IDSchema):
    name: str


class ArticleRead(fr.IDSchema):
    title: str
    author_id: fr.MustExist[Author]
```

{class}`fr.MustExist[Author] <fastapi_restly.schemas.MustExist>` means "this
field is an `Author` id, checked to exist." The wire format is a plain scalar,
and responses use the same shape:

```json
{
  "id": 10,
  "title": "Intro",
  "author_id": 1
}
```

```python
@fr.include_view(app)
class ArticleView(fr.AsyncRestView):
    prefix = "/articles"
    model = Article
    schema = ArticleRead
```

On create and update, Restly looks up the `Author` with `id=1`. If it does not
exist, the request returns `404` — a clean validation error, not a database
`IntegrityError` at flush. That lookup is an *unscoped* existence check — see
[Visibility and Multi-Tenancy](#visibility-and-multi-tenancy) below. In hooks,
`data.author_id` is the plain integer.

## List filtering

The FK field is filterable on the list endpoint by its own public name —
`GET /articles/?author_id=1` (also `author_id__in`, `author_id__ne`,
`author_id__isnull`). See
[Query Modifiers → Foreign-key filtering](howto_query_modifiers.md#foreign-key-filtering).

## Field naming

The field name decides what a reference writes, matched against the SQLAlchemy
mapper (not the field name, and not the `_id` suffix):

- a name that maps to a **foreign-key column** writes the scalar id — use
  {class}`MustExist <fastapi_restly.schemas.MustExist>`;
- a name that maps to a **relationship** resolves to the related object — use
  {class}`IDRef <fastapi_restly.schemas.IDRef>` /
  {class}`IDSchema <fastapi_restly.schemas.IDSchema>`.

```python
author_id: fr.MustExist[Author]   # the Article.author_id FK column
author:    fr.IDRef[Author]       # the Article.author relationship
```

A relationship reference can sit over any FK column name — including a column
with an explicit DB name (`mapped_column("db_name", ...)`). Restly finds the
column through the mapper and keeps the column and the relationship in sync:

| Schema field | FK column | Relationship |
|---|---|---|
| `author` | `Article.author_id` | `Article.author` |

If the relationship attribute is absent, or more than one relationship shares the
FK column (ambiguous), Restly sets the FK column and leaves the relationship to
you.

## Lists of references

A to-many reference serializes as a plain id array with {class}`list[fr.IDRef[Model]] <fastapi_restly.schemas.IDRef>`:

```python
class OrderRead(fr.IDSchema):
    customer_name: str
    products: list[fr.IDRef[Product]]  # serializes as [1, 2, 3]
```

On input, each element accepts both raw scalars and `{"id": ...}` shapes, so
the same field doubles as a permissive write-side type when paired with a
custom {meth}`create <fastapi_restly.views.RestView.create>` / {meth}`update <fastapi_restly.views.RestView.update>` business verb that resolves the list. For
relationship objects that must stay nested on the wire, use
{class}`fr.IDSchema[Model] <fastapi_restly.schemas.IDSchema>` (below).
{class}`MustExist <fastapi_restly.schemas.MustExist>` is single-valued (a scalar
FK column); a to-many is a relationship, so it uses
{class}`list[fr.IDRef[Model]] <fastapi_restly.schemas.IDRef>`.

## Input compatibility

{class}`IDRef <fastapi_restly.schemas.IDRef>` and `IDSchema[Model]` accept both
scalar ids and `{"id": ...}` dictionaries on input:

```json
{ "author": 1 }
```

```json
{ "author": {"id": 1} }
```

The response shape stays as the type dictates ({class}`IDRef <fastapi_restly.schemas.IDRef>`
scalar, {class}`IDSchema <fastapi_restly.schemas.IDSchema>` nested). This is
useful when clients or migration code already send one form while the public API
contract keeps the other.

## About IDSchema

Most examples inherit from {class}`fr.IDSchema <fastapi_restly.schemas.IDSchema>` — {class}`BaseSchema <fastapi_restly.schemas.BaseSchema>` plus a read-only `id`
field. The schema bases, `ReadOnly` / `WriteOnly` markers, and aliases are
owned by [Custom Schemas and Field Types](howto_custom_schema.md); inherit
from `fr.BaseSchema` instead if you want every field, including `id`,
explicit. Used as a *field* type — `author: fr.IDSchema[Author]` — it is a
nested relationship reference (below), distinct from its use as a base class.

## Nested relationship objects

Some clients model relationships as objects. For that shape, annotate the
relationship field with {class}`fr.IDSchema[Model] <fastapi_restly.schemas.IDSchema>`:

```python
class ArticleRead(fr.IDSchema):
    title: str
    author: fr.IDSchema[Author]
```

The wire format is:

```json
{
  "title": "Intro",
  "author": {"id": 1}
}
```

{class}`IDRef <fastapi_restly.schemas.IDRef>` and `IDSchema[Model]` both name a
relationship, validate the referenced row, and use the same resolver. The
difference is the API shape — flat id versus nested object.

## Relationship references and dataclass models

When a field names a relationship ({class}`IDRef <fastapi_restly.schemas.IDRef>` /
{class}`IDSchema <fastapi_restly.schemas.IDSchema>`), Restly resolves the id to
the related object and assigns it. {class}`fr.IDBase <fastapi_restly.models.IDBase>`
uses SQLAlchemy's `MappedAsDataclass`, which generates an `__init__` from the
model fields, and Restly's create/update helpers are aware of that constructor
shape when the reference has been resolved to an ORM object.

The common FK-first declaration is the clearest default:

```python
author_id: Mapped[int] = mapped_column(ForeignKey("author.id"))
author: Mapped["Author"] = relationship(default=None, init=False)
```

With that model and `author: fr.IDRef[Author]`, Restly passes the scalar FK and
keeps `author` in sync after construction.

If your model is relationship-first, Restly adapts there too:

```python
author_id: Mapped[int] = mapped_column(ForeignKey("author.id"), init=False)
author: Mapped["Author"] = relationship(default=None)
```

In that shape, Restly passes the resolved `Author` object to the constructor and
keeps `author_id` in sync. More generally, Restly supplies the constructor
values your dataclass model requires: FK scalar, relationship object, or both.

If a schema exposes the same link as two reference fields — a FK-named
{class}`IDRef <fastapi_restly.schemas.IDRef>` alongside the relationship — Restly
validates that the supplied ids match:

```json
{
  "author_id": 1,
  "author": {"id": 1}
}
```

Conflicting references, such as `"author_id": 1` with `"author": {"id": 2}`,
return `422`. Explicit `null` also participates: `author_id: 1` with
`author: null` is a conflict, while omitting `author` entirely is not. A plain
{class}`MustExist <fastapi_restly.schemas.MustExist>` scalar does not take part —
it is a column value, not a reference field.

### Standard SQLAlchemy Declarative Models

If you use a normal SQLAlchemy `DeclarativeBase`, the dataclass constructor
rules do not apply:

```python
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Article(Base):
    __tablename__ = "article"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]
    author_id: Mapped[int] = mapped_column(ForeignKey("author.id"))
    author: Mapped["Author"] = relationship()
```

There is no generated `__init__` contract to satisfy: Restly constructs the
object and applies the resolved reference to the FK column (and the matching
relationship attribute, when one is declared) directly.

(idref-custom-routes)=

## References in custom routes

Generated `POST` and `PATCH` routes validate the body before Restly calls `make_new_object()` or `update_object()`, so reference fields are already validated — a {class}`MustExist <fastapi_restly.schemas.MustExist>` field is a plain id, and {class}`IDRef[Model] <fastapi_restly.schemas.IDRef>` fields are `IDRef` instances.

In a custom route, be careful when you construct a schema yourself. Pydantic's `model_construct()` skips validation, but the existence check still runs from `make_new_object()` — a {class}`MustExist <fastapi_restly.schemas.MustExist>` field carries the plain id, so no wrapping is needed:

```python
from fastapi_restly.objects import async_make_new_object


link_schema = TaskLabelRead.model_construct(
    task_id=request.task_id,
    label_id=label.id,
)

task_label = await async_make_new_object(
    self.session,
    TaskLabel,
    link_schema,
)
```

This keeps the resolver path active: Restly verifies the referenced rows exist and writes the FK columns. It helps when validated construction would require response-only fields such as `id` or timestamps.

If you instead use {class}`IDSchema[Model] <fastapi_restly.schemas.IDSchema>` as a nested relationship-object field in a custom response schema, serialize the ORM object through {meth}`self.to_response_schema(obj) <fastapi_restly.views.BaseRestView.to_response_schema>` before returning it:

```python
class TaskLabelNestedRead(fr.IDSchema):
    task: fr.IDSchema[Task]
    label: fr.IDSchema[Label]


@fr.post("/attach", response_model=TaskLabelNestedRead, status_code=201)
async def attach(self, request: AttachRequest):
    obj = await create_task_label(...)
    return self.to_response_schema(obj)
```

The raw ORM object usually has scalar FK columns, while a nested schema expects relationship-shaped data. Scalar fields ({class}`MustExist <fastapi_restly.schemas.MustExist>`, {class}`IDRef <fastapi_restly.schemas.IDRef>`) do not need this step because their wire format is already scalar.

## Visibility and Multi-Tenancy

Reference resolution is an **unscoped existence check**. Restly fetches the
referenced row by primary key only (`session.get(Author, id)`). View
{meth}`build_query <fastapi_restly.views.RestView.build_query>` scoping is not applied, so tenant, soft-delete, and row-level
visibility checks are your responsibility.

The resolver only knows the referenced *model* from the field type, not which
view governs it. References are a **policy** concern.

Gate in **{meth}`authorize <fastapi_restly.views.RestView.authorize>`**, where `data` carries the *unresolved* reference. For a
{class}`MustExist <fastapi_restly.schemas.MustExist>` field `data.author_id` is
the requested id; for {class}`IDRef <fastapi_restly.schemas.IDRef>` /
{class}`IDSchema <fastapi_restly.schemas.IDSchema>` it is a reference, so the id
is `data.<field>.id` (and a list field is a list of references):

```python
@fr.include_view(app)
class ArticleView(fr.AsyncRestView):
    prefix = "/articles"
    model = Article
    schema = ArticleRead

    async def authorize(self, action, obj=None, data=None):
        if data is not None and data.author_id is not None:
            if not await self.author_visible(data.author_id):
                # 404 (not 403) so you don't leak that the id exists elsewhere.
                raise fr.exc.NotFound("author not found")
```

The resolved ORM object is not available in `authorize`; resolution runs later
in the business verb. If you need the resolved row, check in {meth}`before_commit <fastapi_restly.views.RestView.before_commit>`,
where the built object carries it (for example `new.author.org_id`). Prefer
`authorize` when the requested id is enough: it rejects before the unscoped
fetch and is the standard policy seam.

References are gated in `authorize` / `before_commit` like any other
write-path authorization.

## Behavior summary

- {class}`MustExist[Model] <fastapi_restly.schemas.MustExist>` is the default:
  a checked scalar foreign key, plain-id wire on request and response, a plain
  id in Python. {class}`IDRef <fastapi_restly.schemas.IDRef>` (flat) and
  {class}`IDSchema <fastapi_restly.schemas.IDSchema>` (nested) are the
  relationship-shaped variants.
- Missing related ids return `404`.
- Reference resolution is an **unscoped existence check** (bare PK lookup, no
  {meth}`build_query <fastapi_restly.views.RestView.build_query>` scoping). Gate cross-tenant / visibility references in
  {meth}`authorize <fastapi_restly.views.RestView.authorize>` or {meth}`before_commit <fastapi_restly.views.RestView.before_commit>` — see [Visibility and Multi-Tenancy](#visibility-and-multi-tenancy).
- Restly matches the field name to a mapped column or relationship via the
  model's mapper, not by an `_id` suffix.
- A matching SQLAlchemy relationship lets Restly keep the FK column and
  relationship attribute in sync.
- Dataclass models can be FK-first or relationship-first; Restly supplies the
  constructor values the model requires from the same resolved row.
- If both `author_id` and `author` are explicitly provided as references, they
  must refer to the same row or the request returns `422`.
- {class}`IDSchema <fastapi_restly.schemas.IDSchema>` as a base class adds the resource's own read-only `id` field.

## See also

- [Tutorial](tutorial.md) — reference fields in the blog build, in context.
- [Custom Schemas and Field Types](howto_custom_schema.md) — the schema bases
  and field markers these compose with.
- [API Reference](api_reference.md) — {class}`MustExist <fastapi_restly.schemas.MustExist>` / {class}`IDRef <fastapi_restly.schemas.IDRef>` / {class}`IDSchema <fastapi_restly.schemas.IDSchema>` signatures and the
  full symbol tables.
