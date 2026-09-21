# Query Clauses

A query {class}`Clause <fastapi_restly.clauses.Clause>` describes a
reusable SQLAlchemy predicate or statement transform. Its parameters
can be bound after the clause is declared.

For example, you could reuse the same rules to list and count a user's
non-deleted tasks:

```python
import fastapi_restly as fr
from sqlalchemy import ColumnElement, func


class TaskClauses(fr.ClauseNamespace):
    model = Task

    is_deleted = fr.where_clause(Task.deleted_at.is_not(None))

    @fr.where_clause
    @staticmethod
    def owned_by_user(user_id: int) -> ColumnElement[bool]:
        return Task.user_id == user_id

    visible = fr.all_of(owned_by_user, fr.none_of(is_deleted))
    default_scope = visible


with TaskClauses.owned_by_user.bind(user_id=42):
    listing = TaskClauses.visible.select(Task)
    count = TaskClauses.visible.select(func.count(Task.id))
```

Both statements select non-deleted tasks belonging to user 42.
{meth}`Clause.bind <fastapi_restly.clauses.Clause.bind>` supplies the value
to every composition that uses `TaskClauses.owned_by_user`, including
`TaskClauses.visible`.
See [Binding values](#binding-values).

{class}`ClauseNamespace <fastapi_restly.clauses.ClauseNamespace>` groups
the model's clauses. `default_scope = visible` makes `visible` the default
scope for {class}`RestView <fastapi_restly.views.RestView>` reads and
reference checks on `Task`. See [Scopes](#default-scope).

{func}`fr.where_clause <fastapi_restly.clauses.where_clause>` creates a
{class}`WhereClause <fastapi_restly.clauses.WhereClause>`, a reusable WHERE
predicate. Pass a SQLAlchemy condition or decorate a function that returns
one. See [Declaring clauses](#declaring-clauses).

{func}`fr.transform_clause <fastapi_restly.clauses.transform_clause>` creates a
{class}`TransformClause <fastapi_restly.clauses.TransformClause>` from a
function that reshapes a SQLAlchemy `Select`, for example by adding a join
or ordering. See [Declaring clauses](#declaring-clauses).

{func}`fr.combine <fastapi_restly.clauses.combine>` bundles statement
transforms with optional predicates into a
{class}`CombinedClause <fastapi_restly.clauses.CombinedClause>`.
See [Composing](#composing-clauses).

[Compose](#composing-clauses) query fragments, then
[apply them to statements](#applying-clauses) with
{func}`fr.apply_clauses <fastapi_restly.clauses.apply_clauses>`.
[Current](howto_current.md) supplies values shared across application
code. [Using Current in clauses](#shared-params) covers their integration.

The remaining examples share three models:

```python
from datetime import datetime, timedelta

from sqlalchemy import ForeignKey, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Collection(Base):
    __tablename__ = "collection"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int]
    archived_at: Mapped[datetime | None]


class Item(Base):
    __tablename__ = "item"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int]
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

When the predicate needs a value supplied later, declare a function.
Its parameters are filled from
{meth}`Clause.bind <fastapi_restly.clauses.Clause.bind>` or keyword
arguments to [statement construction](#applying-clauses):

```python
from sqlalchemy import ColumnElement


@fr.where_clause
def owned_by_user(user_id: int) -> ColumnElement[bool]:
    return Item.user_id == user_id


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
validates itself and registers as the model's clause namespace:

```python
class ItemClauses(fr.ClauseNamespace):
    model = Item

    is_deleted = fr.where_clause(Item.deleted_at.is_not(None))

    @fr.where_clause
    @staticmethod
    def owned_by_user(user_id: int) -> ColumnElement[bool]:
        return Item.user_id == user_id

    visible = fr.all_of(owned_by_user, fr.none_of(is_deleted))
    trashed = fr.all_of(owned_by_user, is_deleted)
```

Earlier names in the class body are in scope for later compositions, as
`visible` shows. The `@staticmethod` under the clause decorator is for
the type checker, which reads a bare `def` in a class body as a method;
`where_clause` unwraps it. Usage is by class name: `ItemClauses.visible`,
`ItemClauses.trashed`, plain attribute access that any type checker
follows. Name the namespace after the model, and define it in the
model's module: importing the model then guarantees the namespace is
registered. `model` is optional: a namespace without one is a plain group
of clauses, or a base class for namespaces that declare one, and declaring
`model` is what registers the namespace for its model.

The namespace validates itself at definition time. A public attribute
that is not a clause raises, which catches the bare expression that
forgot its wrapper:

```python
class ItemClauses(fr.ClauseNamespace):
    model = Item
    is_deleted = Item.deleted_at.is_not(None)   # TypeError at import
```

Helpers and constants are allowed with a leading underscore. A second
namespace for the same model raises.

A tenant predicate that must survive every replacement scope belongs in the
session-level rule documented under [tenant row scoping](#tenant-row-scoping).

Name clauses as predicate phrases that read truthfully after WHERE:
`owned_by_user`, `is_deleted`, `has_active_subscription`. Name
transforms after the change they make: `newest_first`, `paged`. Skip
mechanism suffixes such as `_filter` or `_clause`; the namespace and the
type already say what the attribute is.

One attribute name is reserved: a `WhereClause` declared as
`default_scope` becomes the scope every view read and every reference
check on the model applies, and undeclared means `UNSCOPED`; the
namespace enforces the shape at definition. [Scopes](scopes.md) owns
that topic.

(composing-clauses)=
## Composing

{func}`fr.all_of <fastapi_restly.clauses.all_of>`,
{func}`fr.any_of <fastapi_restly.clauses.any_of>`, and
{func}`fr.none_of <fastapi_restly.clauses.none_of>` compose predicates:

```python
visible = fr.all_of(owned_by_user, fr.none_of(is_deleted))
active_or_new = fr.any_of(has_active_subscription, recently_created)
```

`none_of(a, b)` is NOT over the OR of its operands: true when none
hold. All three require their clause operands to carry a where, and `any_of`
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

(unscoped-composition)=
### Composing with `UNSCOPED`

Use `fr.clauses.UNSCOPED` to express no restriction. In boolean
composition it accepts every row, equivalent to SQL `TRUE`. It remains
a separate sentinel, not a `Clause` or `WhereClause`.

The composition functions accept this sentinel with the following rules.
Here `C` is a predicate clause, and the argument order does not affect
the result:

| Expression | Result |
|---|---|
| `fr.all_of(UNSCOPED, C)` | `C` itself |
| `fr.all_of(UNSCOPED)` | `UNSCOPED` itself |
| `fr.all_of(UNSCOPED, UNSCOPED)` | `UNSCOPED` itself |
| `fr.any_of(UNSCOPED, C)` | `UNSCOPED` itself |
| `fr.any_of(UNSCOPED)` | `UNSCOPED` itself |
| `fr.none_of(UNSCOPED, C)` | A `WhereClause` over `sqlalchemy.false()` |
| `fr.none_of(UNSCOPED)` | A `WhereClause` over `sqlalchemy.false()` |

`UNSCOPED` is a no-op in AND composition. It cannot be dropped from OR
or NOT: every row OR published rows still means every row. NOT over
that result matches no rows. The false clause works as a scope too:
lists and counts are empty, and retrieve and reference checks return 404.

`all_of` removes `UNSCOPED` arguments before composing the remaining
clauses. With one clause left it returns that exact object, preserving
its bindings and name. With none left it returns the sentinel. This
lets a predicate extend a possibly unscoped default without branching:

```python
scope = fr.all_of(ItemClauses.default_scope, has_active_subscription)
```

The return type is `WhereClause` when a definite predicate is the first
or second argument and the other arguments are predicates or `Unscoped`.
Mixed argument lists outside those overloads use a safe union return
type. `any_of` can return `Unscoped` when any argument may be unscoped.
`none_of` always returns a `WhereClause`. Use `apply_clauses` for a
result that may be the sentinel, which has no clause methods.

`combine` ignores `UNSCOPED` and keeps the other predicates and
transforms. At least one remaining operand must still carry a
transform, so `combine(UNSCOPED)` raises. `all_of()`, `any_of()`, and
`none_of()` with no arguments also continue to raise. Passing
`UNSCOPED` explicitly counts as an argument.

Other operands are validated even when `UNSCOPED` determines the whole
result. A transform in `any_of` or `none_of`, or a raw expression or
`ContextParam` in a boolean composition, still raises. Once validated,
clauses discarded by OR or NOT simplification are not evaluated and
need no bound values.

`apply_clauses(stmt, UNSCOPED)` adds no restriction. Existing filters
and transforms on `stmt` stay in place, and other supplied clauses
still apply. `UNSCOPED` does not clear a statement that was already
filtered.

(applying-clauses)=
## Applying clauses to a statement

Statement construction stays plain SQLAlchemy.
{func}`fr.apply_clauses <fastapi_restly.clauses.apply_clauses>` is the
bridge: it adds the clauses' predicates to the statement's WHERE and,
for a `Select`, applies their transforms:

```python
from sqlalchemy import select

stmt = fr.apply_clauses(select(Item), ItemClauses.visible, with_live_collection)
```

Keyword arguments are an ephemeral bind: routed across all the given
clauses, the values live only while the statement is built and layer
over any ambient `bind()`. The statement is positional-only, so every
keyword name stays free for binding:

```python
stmt = fr.apply_clauses(select(Item), ItemClauses.visible, user_id=42)
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
stmt = ItemClauses.visible.select(Item, user_id=42).where(Item.id == item_id)

count = ItemClauses.visible.select(func.count(Item.id), user_id=42)

stmt = (
    ItemClauses.trashed.update(Item, user_id=42)
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
with ItemClauses.visible.bind(user_id=42):
    stmt = ItemClauses.visible.select(Item)
```

Because routing targets the leaf, one bind reaches every composite that
shares it: binding `user_id` on `owned_by_user` serves `visible`,
`trashed`, and anything declared later, without registration.

For one statement, pass the value as a keyword argument instead:

```python
stmt = ItemClauses.trashed.delete(Item, user_id=42)
```

For values shared throughout a request, use
[Current's dependency binding](#current-request-binding) and
[reference those members from clauses](#shared-params).

Every binding records where it was made.
{meth}`Clause.explain <fastapi_restly.clauses.Clause.explain>` renders
the clause tree: node labels are the nodes' reprs, and under each node
one line per bind name it owns, with the bound value and the file and
line that bound it, or UNBOUND. A subtree reached through several
paths renders once and is marked shared after that.

```python
>>> ItemClauses.visible.explain()
<WhereClause all_of binds: user_id>
├─ <WhereClause owned_by_user binds: user_id>
│  └─ user_id = 42   bound at app/reports.py:23 (build_report)
└─ <WhereClause none_of>
   └─ <WhereClause item.deleted_at IS NOT NULL>
```

The system verifies that bindings exist and route to the right slot.
The application must validate the value before binding it.

Binding fails loud in each direction. Resolving a clause whose value is
not bound raises `LookupError` while the statement is built, never a
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

stmt = select(Item).where(ItemClauses.is_deleted(), Item.deleted_at < cutoff)

collection_is_archived = fr.where_clause(Collection.archived_at.is_not(None))

stmt = stmt.join(
    Collection,
    and_(Collection.id == Item.collection_id, collection_is_archived()),
)

status = case((ItemClauses.is_deleted(), "trash"), else_="live")
```

Keyword arguments are an ephemeral bind: `ItemClauses.owned_by_user(user_id=42)`.
Only a `WhereClause` is callable; a clause that carries a transform has
no expression form, and this path skips the table validation that
`apply_clauses` performs. A clause is never a boolean:
`if ItemClauses.is_deleted:` raises `TypeError` instead of always passing.

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
    ItemClauses.owned_by_user,
    fr.any_of(in_period, last_month),
)

with (
    in_period.bind(start=aug_1, end=aug_31),
    last_month.bind(start=jul_1, end=jul_31),
):
    stmt = report.select(Item, user_id=42)
```

Aliases bind per leaf; binding `start` on the composite would raise as
ambiguous, since two leaves accept it. Named aliases share one binding
namespace: every `alias("last_month")` call addresses the same binding,
so the name is all that is needed. An anonymous `alias()` is always
fresh. Aliasing a composite raises; which leaves should share bindings
and which should split cannot be decided automatically, so rebuild the
composite from aliased leaves.

(shared-params)=
## Using Current in clauses

Clauses declared separately do not share bindings, even when their
functions accept the same parameter name. Binding one leaves the other
unbound. Binding that name on a composition containing both raises as
ambiguous. To share a value, use a member of [Current](howto_current.md)
(or another {class}`ContextNamespace <fastapi_restly.clauses.ContextNamespace>`):

```python
class Current(fr.ContextNamespace):
    user_id: fr.ContextParam[int]
```

Embed `Current.user_id` in a condition passed to
{func}`fr.where_clause <fastapi_restly.clauses.where_clause>`.
It becomes a placeholder filled when the clause resolves:

```python
owned_item = fr.where_clause(Item.user_id == Current.user_id)
owned_collection = fr.where_clause(Collection.user_id == Current.user_id)

with Current.bind(user_id=42):
    items = owned_item.select(Item)
    collections = owned_collection.select(Collection)
```

Both clauses use the same member, so one binding supplies both.
`Current.user_id` without parentheses is the placeholder.
`Current.user_id()` reads the value immediately, which would require
an active binding at declaration time.

In a clause function, mark a parameter with the slot as `Annotated`
metadata. The parameter receives that member's value and binds under
`user_id`, even though its local name is `uid`:

```python
from typing import Annotated

from sqlalchemy import and_


@fr.where_clause
def visible_to_user(uid: Annotated[int, Current.user_id]) -> ColumnElement[bool]:
    return and_(Item.user_id == uid, Item.deleted_at.is_(None))
```

Reading `Current` inside the function also works:

```python
@fr.where_clause
def owned_by_current_user() -> ColumnElement[bool]:
    return Item.user_id == Current.user_id()
```

Clause binding cannot discover reads inside a function body.
`owned_by_current_user.bind(user_id=42)` therefore raises
`no clause accepts`, and `explain()` does not list that dependency.
Bind through `Current` when using this form. A missing value still
raises when the function runs. A placeholder inside an expression
returned by a function is also invisible to binding discovery.

Use the embedded or `Annotated` form when the clause must accept
`user_id=` itself or show the dependency in `explain()`.
[Bind Current from FastAPI dependencies](#current-request-binding) for
values supplied per request.

A {class}`ContextParam <fastapi_restly.clauses.ContextParam>` supplies a
value, not a predicate. Boolean composition rejects it. Two distinct
members under the same name in one expression also raise.

(clause-kinds)=
## The clause kinds

{class}`Clause <fastapi_restly.clauses.Clause>` is abstract; each
constructor names the kind it builds.

| Kind | Built by | Callable | In `any_of`/`none_of` | On UPDATE/DELETE |
|---|---|---|---|---|
| {class}`WhereClause <fastapi_restly.clauses.WhereClause>` | `where_clause`, `any_of`, `none_of`, all-where `all_of` | yes | yes | yes |
| {class}`TransformClause <fastapi_restly.clauses.TransformClause>` | `transform_clause` | no | no | no |
| {class}`CombinedClause <fastapi_restly.clauses.CombinedClause>` | `combine`, `all_of` with a transform-carrying operand | no | no | no |

A `WhereClause` guarantees no transform anywhere in its tree, which is
what makes the yes-column safe: OR, NOT, UPDATE, and DELETE all break
in the presence of a join. The guarantees are enforced twice, in the
signatures for type checkers and at runtime for everyone else.

{class}`ContextParam <fastapi_restly.clauses.ContextParam>` shares the
base class's binding machinery but represents a value, not a query
fragment. [Current](howto_current.md) covers its declaration and use.

`UNSCOPED` is outside this hierarchy. `all_of` and `any_of` can also
return that sentinel under the [composition rules](#unscoped-composition).

```{seealso}
{doc}`api/clauses` lists every symbol with its signature.
```
