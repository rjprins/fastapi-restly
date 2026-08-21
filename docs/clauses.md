# Query Clauses

A clause is a named, reusable fragment of a query: a WHERE predicate, a
statement transform such as a join or an ordering, or a bundle of both.
A fourth kind carries no SQL at all: a value slot shared between clauses.
Clauses are declared once at module level, composed with boolean
functions, and applied to plain SQLAlchemy statements. The values a
clause needs per request, a tenant id or a search term, are not fixed at
declaration; they are bound where they become known and routed to the
clause that needs them.

A plain SQLAlchemy expression cannot do this: `Item.tenant_id == tenant_id`
requires a `tenant_id` at construction, so it cannot be a module-level
constant shared across endpoints. A clause defers that value, which makes
"which conditions apply here" a declaration instead of code that runs in
every endpoint.

The examples on this page share three models:

```python
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import ForeignKey, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Collection(Base):
    __tablename__ = "collection"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID]
    archived_at: Mapped[datetime | None]


class Item(Base):
    __tablename__ = "item"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID]
    collection_id: Mapped[int] = mapped_column(ForeignKey("collection.id"))
    name: Mapped[str]
    created_at: Mapped[datetime]
    deleted_at: Mapped[datetime | None]

    subscriptions: Mapped[list["Subscription"]] = relationship()


class Subscription(Base):
    __tablename__ = "subscription"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("item.id"))
    status: Mapped[str]
```

(declaring-clauses)=
## Declaring clauses

{func}`fr.where_clause <fastapi_restly.clauses.where_clause>` builds a
predicate clause. A ready-made SQLAlchemy condition needs no function:

```python
import fastapi_restly as fr

is_deleted = fr.where_clause(Item.deleted_at.is_not(None))
```

When the predicate needs a per-request value, declare a function. Its
parameters are not passed by callers; they are filled from values bound
via {meth}`Clause.bind <fastapi_restly.clauses.Clause.bind>` each time
the clause resolves:

```python
from sqlalchemy import ColumnElement


@fr.where_clause
def owned_by_tenant(tenant_id: UUID) -> ColumnElement[bool]:
    return Item.tenant_id == tenant_id


@fr.where_clause
def name_matches(term: str) -> ColumnElement[bool]:
    return Item.name.ilike(f"%{term}%")


@fr.where_clause
def recently_created() -> ColumnElement[bool]:
    return Item.created_at > func.now() - timedelta(days=7)
```

{func}`fr.transform_clause <fastapi_restly.clauses.transform_clause>`
builds a clause that reshapes the statement instead of filtering it: a
join, an ordering, a limit. The first parameter receives the statement;
further parameters are filled from bound values like a where's:

```python
from sqlalchemy import Select


@fr.transform_clause
def newest_first(stmt: Select) -> Select:
    return stmt.order_by(Item.created_at.desc())


@fr.transform_clause
def paged(stmt: Select, limit: int, offset: int) -> Select:
    return stmt.limit(limit).offset(offset)
```

Declare a transform only when the statement itself must change. A check
that a related row exists is a predicate, not a join: express it with
the relationship's `any()` or `has()`, which generate EXISTS. A join
used for an existence check multiplies rows on one-to-many
relationships; EXISTS cannot.

```python
has_active_subscription = fr.where_clause(
    Item.subscriptions.any(Subscription.status == "active")
)
```

(clause-namespaces)=
## Grouping clauses per model

A model's clauses live in one
{class}`fr.ClauseNamespace <fastapi_restly.clauses.ClauseNamespace>`
subclass. Declare `model` in the class body; on definition the namespace
attaches itself to the model as `Model.C`:

```python
class ItemClauses(fr.ClauseNamespace):
    model = Item

    is_deleted = fr.where_clause(Item.deleted_at.is_not(None))

    @fr.where_clause
    def owned_by_tenant(tenant_id: UUID) -> ColumnElement[bool]:
        return Item.tenant_id == tenant_id

    visible = fr.all_of(owned_by_tenant, fr.none_of(is_deleted))
    trashed = fr.all_of(owned_by_tenant, is_deleted)
```

Earlier names in the class body are in scope for later compositions, as
`visible` shows. Usage reads model-first: `Item.C.visible`,
`Item.C.trashed`. The capital `C` holds a class; lowercase `.c` is
SQLAlchemy's column namespace on `Table`.

The namespace validates itself at definition time. A class without
`model` raises. A public attribute that is not a clause raises, which
catches the bare expression that forgot its wrapper:

```python
class ItemClauses(fr.ClauseNamespace):
    model = Item
    is_deleted = Item.deleted_at.is_not(None)   # TypeError at import
```

Helpers and constants are allowed with a leading underscore. A second
namespace for the same model raises.

The attachment happens at runtime, so a type checker needs an
annotation on the model to see `Item.C`. Put it under
`if TYPE_CHECKING:`:

```python
from typing import TYPE_CHECKING, ClassVar


class Item(Base):
    __tablename__ = "item"

    if TYPE_CHECKING:
        C: ClassVar[type["ItemClauses"]]   # filled by ItemClauses

    id: Mapped[int] = mapped_column(primary_key=True)
    ...
```

The guard is not optional style: on a dataclass base
({class}`fr.IDBase <fastapi_restly.models.IDBase>`,
{class}`fr.DataclassBase <fastapi_restly.models.DataclassBase>`) the
annotation scan de-stringifies the forward reference at class creation
and a bare annotation raises `NameError`.

Name clauses as predicate phrases that read truthfully after WHERE:
`owned_by_tenant`, `is_deleted`, `has_active_subscription`. Name
transforms after the change they make: `newest_first`, `paged`. Skip
mechanism suffixes such as `_filter` or `_clause`; the namespace and the
type already say what the attribute is.

(composing-clauses)=
## Composing

{func}`fr.all_of <fastapi_restly.clauses.all_of>`,
{func}`fr.any_of <fastapi_restly.clauses.any_of>`, and
{func}`fr.none_of <fastapi_restly.clauses.none_of>` compose predicates:

```python
visible = fr.all_of(owned_by_tenant, fr.none_of(is_deleted))
active_or_new = fr.any_of(has_active_subscription, recently_created)
```

`none_of(a, b)` is NOT over the OR of its operands: true when none
hold. All three require every operand to carry a where, and `any_of`
and `none_of` additionally reject operands that carry a transform. An
inner join already removes rows, so OR or NOT over a join-dependent
predicate changes which rows exist at all; the rejection message points
to the EXISTS form instead.

{func}`fr.combine <fastapi_restly.clauses.combine>` is not boolean
logic. It bundles a transform with its related predicates under one
name:

```python
@fr.transform_clause
def join_collection(stmt: Select) -> Select:
    return stmt.join(Collection, Collection.id == Item.collection_id)


with_live_collection = fr.combine(
    join_collection,
    fr.where_clause(Collection.archived_at.is_(None)),
)
```

The join and the predicate on the joined table belong together; the
bundle keeps them inseparable. Give such bundles a `with_` prefix: the
name signals that applying the clause changes the statement, not only
the row filter. `combine` requires at least one transform-carrying
operand; a bundle of only wheres is `all_of`'s job, and `combine`
raises with that message.

(applying-clauses)=
## Applying clauses to a statement

Statement construction stays plain SQLAlchemy.
{func}`fr.apply_clauses <fastapi_restly.clauses.apply_clauses>` is the
bridge: it adds the clauses' predicates to the statement's WHERE and,
for a `Select`, applies their transforms:

```python
from sqlalchemy import select

stmt = fr.apply_clauses(select(Item), Item.C.visible, with_live_collection)
```

Keyword arguments are an ephemeral bind: routed across all the given
clauses, the values live only while the statement is built and layer
over any ambient `bind()`. The statement is positional-only, so every
keyword name stays free for binding:

```python
stmt = fr.apply_clauses(select(Item), Item.C.visible, tenant_id=tenant_id)
```

Transforms are collected from the whole clause tree and each distinct
transform is applied once, so a join carried inside an `all_of` or a
`combine` is never lost, and a join shared by two bundles is never
applied twice. `update()` and `delete()` statements accept where-only
clauses; a transform there raises, because UPDATE and DELETE cannot
join.

`apply_clauses` also rejects a predicate that references a table the
statement does not select from:

```python
fr.apply_clauses(select(Item), fr.where_clause(Collection.archived_at.is_(None)))
# TypeError: clause references table(s) not in the statement: collection;
#   add the join via a transform_clause, or use EXISTS (.any()/.has())
```

Without this check SQLAlchemy adds the missing table to the FROM clause
and the query becomes a cartesian product that filters almost nothing.
Predicates inside EXISTS and `IN (SELECT ...)` subqueries bring their
own FROM and pass the check.

Every clause also carries
{meth}`select() <fastapi_restly.clauses.Clause.select>`, and a
{class}`WhereClause <fastapi_restly.clauses.WhereClause>` carries
{meth}`update() <fastapi_restly.clauses.WhereClause.update>` and
{meth}`delete() <fastapi_restly.clauses.WhereClause.delete>`.
`select()` takes the same entities SQLAlchemy's `select()` takes; all
three are shorthand for `apply_clauses` on a fresh statement, with the
same ephemeral bind as keyword arguments:

```python
stmt = Item.C.visible.select(Item, tenant_id=tenant_id).where(Item.id == item_id)

count = Item.C.visible.select(func.count(Item.id), tenant_id=tenant_id)

stmt = (
    Item.C.trashed.update(Item, tenant_id=tenant_id)
    .where(Item.id == item_id)
    .values(deleted_at=None)
)
```

The result is a normal `Select` or `Update`; chain onto it freely.
`apply_clauses` accepts the same ephemeral bind as keywords, routed
across all the clauses it is given. The
method form reads clause-first, `apply_clauses` reads statement-first;
both build the same statement. The method form types as `Select[Any]`;
for precise row typing, build the statement with plain `select()` and
pass it through `apply_clauses`, which preserves the statement's exact
type.

(binding-values)=
## Binding values

{meth}`Clause.bind <fastapi_restly.clauses.Clause.bind>` binds values
for the duration of a `with` block. Each value is routed to the leaf
clause whose function accepts its name, so binding on a composite is
equivalent to binding on the leaf itself:

```python
with Item.C.visible.bind(tenant_id=tenant_id):
    stmt = Item.C.visible.select(Item)
```

Because routing targets the leaf, one bind reaches every composite that
shares it: binding `tenant_id` on `owned_by_tenant` serves `visible`,
`trashed`, and anything declared later, without registration.

In a FastAPI application, a dependency binds once for the whole
request (illustrative):

```python
async def bind_tenant(tenant_id: TenantIdFromAuth):
    with Item.C.owned_by_tenant.bind(tenant_id=tenant_id):
        yield


app = FastAPI(dependencies=[Depends(bind_tenant)])


@app.get("/items")
async def list_items(session: SessionDep) -> list[ItemOut]:
    stmt = Item.C.visible.select(Item)   # tenant_id comes from the dependency
    return list((await session.scalars(stmt)).all())
```

The dependency must be `async def`. FastAPI runs a sync generator
dependency in a threadpool, and a ContextVar set there never reaches
the endpoint: every query would raise on the missing `tenant_id`.

Outside a request, in a script or a job, pass the value as an ephemeral
bind instead:

```python
for tenant_id in tenant_ids:
    stmt = Item.C.trashed.delete(Item, tenant_id=tenant_id)
```

Every binding records where it was made.
{meth}`Clause.explain <fastapi_restly.clauses.Clause.explain>` renders
the clause tree: node labels are the nodes' reprs, and under each node
one line per bind name it owns, with the bound value and the file and
line that bound it, or UNBOUND. A subtree reached through several
paths renders once and is marked shared after that.

```python
>>> Item.C.visible.explain()
<WhereClause all_of binds: tenant_id>
├─ <WhereClause owned_by_tenant binds: tenant_id>
│  └─ tenant_id = UUID('7f3a...')   bound at app/deps.py:23 (bind_tenant)
└─ <WhereClause none_of>
   └─ <WhereClause item.deleted_at IS NOT NULL>
```

The system verifies that bindings exist and route to the right slot.
Whether the bound value is the right one, the authenticated user's
tenant and not another, is the application's invariant; assert it where
the value enters, in the binding dependency.

Binding fails loud in each direction. Resolving a clause whose value is
not bound raises `TypeError` while the statement is built, never a
silently unfiltered query. Binding a name no clause in the tree accepts
raises `TypeError: no clause accepts: ...`. Binding a name that two
distinct leaves accept raises and names the cause, aliases or unrelated
clauses, with the fix: bind it on each leaf directly.

(clauses-in-plain-sqlalchemy)=
## Using a clause in plain SQLAlchemy

Calling a {class}`WhereClause <fastapi_restly.clauses.WhereClause>`
returns the raw `ColumnElement`, which drops into any SQLAlchemy
expression position: a hand-built `.where()`, a join condition, a CASE:

```python
from sqlalchemy import and_, case, select

stmt = select(Item).where(Item.C.is_deleted(), Item.deleted_at < cutoff)

collection_is_archived = fr.where_clause(Collection.archived_at.is_not(None))

stmt = stmt.join(
    Collection,
    and_(Collection.id == Item.collection_id, collection_is_archived()),
)

status = case((Item.C.is_deleted(), "trash"), else_="live")
```

Keyword arguments are an ephemeral bind: `Item.C.owned_by_tenant(tenant_id=tid)`.
Only a `WhereClause` is callable; a clause that carries a transform has
no expression form, and this path skips the table validation that
`apply_clauses` performs. A clause is never a boolean:
`if Item.C.is_deleted:` raises `TypeError` instead of always passing.

(clause-aliases)=
## One clause, two values

A clause resolves each bound name to one value, so using the same
clause twice in one query with different values requires an alias: an
independent instance with its own binding namespace.
{meth}`Clause.alias <fastapi_restly.clauses.Clause.alias>` creates one:

```python
@fr.where_clause
def in_period(start: datetime, end: datetime) -> ColumnElement[bool]:
    return Item.created_at.between(start, end)


last_month = in_period.alias("last_month")

report = fr.all_of(
    Item.C.owned_by_tenant,
    fr.any_of(in_period, last_month),
)

with (
    in_period.bind(start=aug_1, end=aug_31),
    last_month.bind(start=jul_1, end=jul_31),
):
    stmt = report.select(Item, tenant_id=tenant_id)
```

Aliases bind per leaf; binding `start` on the composite would raise as
ambiguous, since two leaves accept it. Named aliases share one binding
namespace: every `alias("last_month")` call addresses the same binding,
so the name is all that is needed. An anonymous `alias()` is always
fresh. Aliasing a composite raises; which leaves should share bindings
and which should split cannot be decided automatically, so rebuild the
composite from aliased leaves.

(shared-params)=
## Sharing one value between clauses

Clauses declared separately do not share bindings, even when their
functions accept the same parameter name; binding one leaves the other
unbound, and both in one tree raise as ambiguous. When several clauses
must follow one value, a tenant id filtering every model, declare the
value once as a {class}`ContextParam <fastapi_restly.clauses.ContextParam>`:

```python
current_tenant = fr.context_param("tenant_id", UUID)
```

One slot, three positions. Embedded in an expression it becomes a
placeholder, filled with the bound value each time the clause resolves;
{func}`fr.where_clause <fastapi_restly.clauses.where_clause>` detects
the slot and wires it into binding:

```python
def tenant_scoped(model) -> fr.WhereClause:
    return fr.where_clause(model.tenant_id == current_tenant)


class ItemClauses(fr.ClauseNamespace):
    model = Item
    owned_by_tenant = tenant_scoped(Item)


class CollectionClauses(fr.ClauseNamespace):
    model = Collection
    owned_by_tenant = tenant_scoped(Collection)
```

In a clause function, mark a parameter with the slot as `Annotated`
metadata; the parameter is then fed from the slot instead of the
function's own binding namespace, and binds under the slot's name, not
the local one:

```python
from typing import Annotated

from sqlalchemy import and_


@fr.where_clause
def visible_to_tenant(tid: Annotated[UUID, current_tenant]) -> ColumnElement[bool]:
    return and_(Item.tenant_id == tid, Item.deleted_at.is_(None))
```

Called, the slot returns the bound value, for the cases where Python
itself needs it, string formatting or arithmetic:

```python
search_term = fr.context_param("term", str)


@fr.where_clause
def name_matches() -> ColumnElement[bool]:
    return Item.name.ilike(f"%{search_term()}%")
```

The calling form reads the value at that moment, so it requires the
binding to be active while the clause resolves; the embedded and
`Annotated` forms defer to resolve time and are wired into routing, so
prefer them. A slot embedded in the expression a clause function
returns does resolve, but the body is opaque at declaration, so
routing cannot see the slot: binding through the clause or a tree
raises `no clause accepts`, and only binding the slot directly works.
Use the bare-condition or `Annotated` form for those clauses.

Sharing is by identity: every clause that embeds or marks the same slot
is served by a single bind, wherever it happens. One dependency covers
every tenant-scoped model, present and future:

```python
async def bind_tenant(tenant_id: TenantIdFromAuth):
    with current_tenant.bind(tenant_id=tenant_id):
        yield
```

A ContextParam is not a predicate; `all_of` and the other boolean
functions reject it. Two distinct slots under the same name in one
expression raise: one name, one owner.

(clause-kinds)=
## The clause kinds

{class}`Clause <fastapi_restly.clauses.Clause>` is abstract; each
constructor names the kind it builds.

| Kind | Built by | Callable | In `any_of`/`none_of` | On UPDATE/DELETE |
|---|---|---|---|---|
| {class}`WhereClause <fastapi_restly.clauses.WhereClause>` | `where_clause`, `any_of`, `none_of`, all-where `all_of` | yes | yes | yes |
| {class}`TransformClause <fastapi_restly.clauses.TransformClause>` | `transform_clause` | no | no | no |
| {class}`CombinedClause <fastapi_restly.clauses.CombinedClause>` | `combine`, `all_of` with a transform-carrying operand | no | no | no |
| {class}`ContextParam <fastapi_restly.clauses.ContextParam>` | `context_param` | yes, to the bound value | no | no |

A `WhereClause` guarantees no transform anywhere in its tree, which is
what makes the yes-column safe: OR, NOT, UPDATE, and DELETE all break
in the presence of a join. The guarantees are enforced twice, in the
signatures for type checkers and at runtime for everyone else.

```{seealso}
{doc}`api/clauses` lists every symbol with its signature.
```
