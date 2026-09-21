# Query Clauses

A query clause is a named, reusable SQLAlchemy predicate: a
{class}`WhereClause <fastapi_restly.clauses.WhereClause>`. Declare it
once, compose it with others, and apply it to any statement.

For example, you could reuse the same rules to list and count a user's
non-deleted tasks:

```python
import fastapi_restly as fr
from sqlalchemy import func, select


class Current(fr.ContextNamespace):
    user_id: fr.ContextParam[int]
    is_admin: fr.ContextParam[bool]


class TaskClauses(fr.ClauseNamespace):
    model = Task

    is_deleted = fr.where_clause(Task.deleted_at.is_not(None))
    owned_by_user = fr.where_clause(Task.user_id == Current.user_id)

    visible = fr.all_of(owned_by_user, fr.none_of(is_deleted))
    default_scope = visible


with Current.bind(user_id=42):
    listing = fr.apply_clauses(select(Task), TaskClauses.visible)
    count = fr.apply_clauses(select(func.count(Task.id)), TaskClauses.visible)
```

Both statements select non-deleted tasks belonging to user 42.

{func}`fr.where_clause <fastapi_restly.clauses.where_clause>` creates a
clause from a SQLAlchemy condition, or from a function that returns one.
See [Declaring clauses](#declaring-clauses).

`Current.user_id` is a member of a
{class}`ContextNamespace <fastapi_restly.clauses.ContextNamespace>`. A
clause takes no arguments: a value that changes per request or per call is
a member, bound around the code that applies the clause. See
[Supplying values](#binding-values).

{class}`ClauseNamespace <fastapi_restly.clauses.ClauseNamespace>` groups
the model's clauses. `default_scope = visible` makes `visible` the default
scope for {class}`RestView <fastapi_restly.views.RestView>` reads and
reference checks on `Task`. See [Scopes](#default-scope).

[Compose](#composing-clauses) clauses, then
[apply them to statements](#applying-clauses) with
{func}`fr.apply_clauses <fastapi_restly.clauses.apply_clauses>`.

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
clause. A ready-made SQLAlchemy condition needs no function:

```python
import fastapi_restly as fr

is_deleted = fr.where_clause(Item.deleted_at.is_not(None))
recently_created = fr.where_clause(
    Item.created_at > func.now() - timedelta(days=7)
)
```

A clause is a predicate and never joins. Express a condition on a
related table with the relationship's `any()` or `has()`, which generate
EXISTS. A join used for an existence check multiplies rows on
one-to-many relationships; EXISTS cannot.

```python
has_active_subscription = fr.where_clause(
    Item.subscriptions.any(Subscription.status == "active")
)
```

Decorate a function when the condition needs a Python branch. The
function takes no parameters and runs each time the clause is applied:

```python
from sqlalchemy import ColumnElement, true


@fr.where_clause
def owned_unless_admin() -> ColumnElement[bool]:
    if Current.is_admin():
        return true()
    return Item.user_id == Current.user_id()
```

A function with parameters raises `TypeError` at declaration. The values
a condition needs come from context members, described under
[Supplying values](#binding-values).

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
    owned_by_user = fr.where_clause(Item.user_id == Current.user_id)

    @fr.where_clause
    @staticmethod
    def owned_unless_admin() -> ColumnElement[bool]:
        if Current.is_admin():
            return true()
        return Item.user_id == Current.user_id()

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
`owned_by_user`, `is_deleted`, `has_active_subscription`. Skip
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
{func}`fr.none_of <fastapi_restly.clauses.none_of>` compose clauses into
a new clause:

```python
live_and_owned = fr.all_of(ItemClauses.owned_by_user, fr.none_of(is_deleted))
active_or_new = fr.any_of(has_active_subscription, recently_created)
```

`none_of(a, b)` is NOT over the OR of its operands: true when none
hold.

(unscoped-composition)=
### Composing with `UNSCOPED`

Use `fr.clauses.UNSCOPED` to express no restriction. In boolean
composition it accepts every row, equivalent to SQL `TRUE`. It remains
a separate sentinel, not a `WhereClause`.

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
clauses. With one clause left it returns that exact object. With none left it returns the sentinel. This
lets a predicate extend a possibly unscoped default without branching:

```python
scope = fr.all_of(ItemClauses.default_scope, has_active_subscription)
```

The return type is `WhereClause` when a definite predicate is the first
or second argument and the other arguments are predicates or `Unscoped`.
Mixed argument lists outside those overloads use a safe union return
type. `any_of` can return `Unscoped` when any argument may be unscoped.
`none_of` always returns a `WhereClause`. Use `apply_clauses` for a
result that may be the sentinel, which is not callable.

`all_of()`, `any_of()`, and `none_of()` with no arguments raise.
Passing `UNSCOPED` explicitly counts as an argument.

Other operands are validated even when `UNSCOPED` determines the whole
result. A raw expression or a `ContextParam` in a boolean composition
still raises. Once validated, clauses discarded by OR or NOT
simplification are not evaluated and need no bound values.

`apply_clauses(stmt, UNSCOPED)` adds no restriction. Existing filters
on `stmt` stay in place, and other supplied clauses still apply.
`UNSCOPED` does not clear a statement that was already filtered.

(binding-values)=
(shared-params)=
## Supplying values

A clause takes no arguments. A value that changes per request or per call
is a member of a
{class}`ContextNamespace <fastapi_restly.clauses.ContextNamespace>`: the
application's [Current](howto_current.md), or a small namespace declared
next to the code that uses it. The examples use the `Current` declared at
the top of this page.

Embed a member in a condition. It becomes a placeholder, filled with the
bound value each time the clause is applied:

```python
owned_item = fr.where_clause(Item.user_id == Current.user_id)
owned_collection = fr.where_clause(Collection.user_id == Current.user_id)

with Current.bind(user_id=42):
    items = fr.apply_clauses(select(Item), owned_item)
    collections = fr.apply_clauses(select(Collection), owned_collection)
```

One binding supplies every clause that uses the member.
`Current.user_id` without parentheses is the placeholder.
`Current.user_id()` reads the value immediately, which would require
an active binding at declaration time.

Put the column first. `Item.user_id == Current.user_id` builds SQL because
the column builds the comparison. `Current.role != "member"` raises
`TypeError`: Python would compare the member object, and the rule would
compile to `WHERE true`. A rule that compares a bound value with a constant
is a Python branch, so write it as a clause function that reads the member
in its body:

```python
@fr.where_clause
def owned_by_current_user() -> ColumnElement[bool]:
    return Item.user_id == Current.user_id()
```

Both forms read the member when the clause is applied: by
`apply_clauses`, by a view read, by a reference check, or when the clause
is called. The statement carries the values with it, so it can run after
the binding has ended:

```python
def owned_items_stmt(user_id: int):
    with Current.bind(user_id=user_id):
        return fr.apply_clauses(select(Item), owned_item)


rows = session.scalars(owned_items_stmt(42))
```

A member without a binding raises `LookupError` when the clause is
applied, never a silently unfiltered query. For values supplied per
request, [bind Current from a FastAPI dependency](#current-request-binding).

A value that one part of the application uses gets its own namespace
there:

```python
class ReportPeriod(fr.ContextNamespace):
    start: fr.ContextParam[datetime]
    end: fr.ContextParam[datetime]


in_period = fr.where_clause(
    Item.created_at.between(ReportPeriod.start, ReportPeriod.end)
)

with Current.bind(user_id=42), ReportPeriod.bind(start=aug_1, end=aug_31):
    stmt = fr.apply_clauses(select(Item), owned_item, in_period)
```

Two distinct members under the same name in one statement raise. Rename
one, or share one member between the namespaces by assignment.

A placeholder is filled only when a clause is applied. An expression that
embeds a member and never passes through a clause fails at execution with
SQLAlchemy's "A value is required for bind parameter". Call the clause for
its expression, or read the value with `Current.user_id()`.

(applying-clauses)=
## Applying clauses to a statement

Statement construction stays plain SQLAlchemy.
{func}`fr.apply_clauses <fastapi_restly.clauses.apply_clauses>` is the
bridge: it adds the clauses' predicates to the statement's WHERE:

```python
from sqlalchemy import select

with Current.bind(user_id=42):
    stmt = fr.apply_clauses(
        select(Item), ItemClauses.visible, has_active_subscription
    )
```

`update()` and `delete()` statements take clauses the same way. The
result is a normal `Select`, `Update` or `Delete` of the exact type the
statement had, so chain onto it freely:

```python
from sqlalchemy import update

with Current.bind(user_id=42):
    stmt = fr.apply_clauses(select(Item), ItemClauses.visible).where(Item.id == item_id)

    restore = (
        fr.apply_clauses(update(Item), ItemClauses.trashed)
        .where(Item.id == item_id)
        .values(deleted_at=None)
    )
```

`apply_clauses` rejects a predicate that references a table the
statement does not select from:

```python
fr.apply_clauses(select(Item), fr.where_clause(Collection.archived_at.is_(None)))
# TypeError: clause references table(s) not in the statement: collection;
#   express the condition as EXISTS (.any()/.has())
```

Without this check SQLAlchemy adds the missing table to the FROM clause
and the query becomes a cartesian product that filters almost nothing.
Predicates inside EXISTS and `IN (SELECT ...)` subqueries bring their
own FROM and pass the check.

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

The call reads context members like any other application of the clause.
This path skips the table validation that `apply_clauses` performs. A
clause is never a boolean: `if ItemClauses.is_deleted:` raises
`TypeError` instead of always passing.

(call-site-values)=
## A value the caller already has

A condition whose value is in hand where the statement is built needs no
clause. Write a plain function that returns the expression:

```python
def name_matches(term: str) -> ColumnElement[bool]:
    return Item.name.ilike(f"%{term}%")


with Current.bind(user_id=42):
    stmt = fr.apply_clauses(select(Item), ItemClauses.visible).where(
        name_matches(term)
    )
```

Wrap the expression where a clause is required, such as a per-read
`scope=`:

```python
scope = fr.all_of(ItemClauses.visible, fr.where_clause(name_matches(term)))
```

A view listing takes the expression directly:
[`handle_get_many(query_params, where=...)`](#per-read-where) narrows
inside the scope.

```{seealso}
{doc}`api/clauses` lists every symbol with its signature.
```
