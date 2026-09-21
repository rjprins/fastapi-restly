# Query Clauses

A query clause is a named, reusable SQLAlchemy predicate: a
{class}`WhereClause <fastapi_restly.clauses.WhereClause>`. Its parameters
can be bound after the clause is declared.

For example, you could reuse the same rules to list and count a user's
non-deleted tasks:

```python
import fastapi_restly as fr
from sqlalchemy import ColumnElement, func, select


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
    listing = fr.apply_clauses(select(Task), TaskClauses.visible)
    count = fr.apply_clauses(select(func.count(Task.id)), TaskClauses.visible)
```

Both statements select non-deleted tasks belonging to user 42.
`bind()` supplies the value
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
Its parameters are filled from `bind()`:

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

A clause is a predicate and never joins. Express a condition on a
related table with the relationship's `any()` or `has()`, which generate
EXISTS. A join used for an existence check multiplies rows on
one-to-many relationships; EXISTS cannot.

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
{func}`fr.none_of <fastapi_restly.clauses.none_of>` compose predicates:

```python
visible = fr.all_of(owned_by_user, fr.none_of(is_deleted))
active_or_new = fr.any_of(has_active_subscription, recently_created)
```

`none_of(a, b)` is NOT over the OR of its operands: true when none
hold.

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

`all_of()`, `any_of()`, and `none_of()` with no arguments raise.
Passing `UNSCOPED` explicitly counts as an argument.

Other operands are validated even when `UNSCOPED` determines the whole
result. A raw expression or a `ContextParam` in a boolean composition
still raises. Once validated, clauses discarded by OR or NOT
simplification are not evaluated and need no bound values.

`apply_clauses(stmt, UNSCOPED)` adds no restriction. Existing filters
on `stmt` stay in place, and other supplied clauses still apply.
`UNSCOPED` does not clear a statement that was already filtered.

(applying-clauses)=
## Applying clauses to a statement

Statement construction stays plain SQLAlchemy.
{func}`fr.apply_clauses <fastapi_restly.clauses.apply_clauses>` is the
bridge: it adds the clauses' predicates to the statement's WHERE:

```python
from sqlalchemy import select

stmt = fr.apply_clauses(select(Item), ItemClauses.visible, has_active_subscription)
```

`update()` and `delete()` statements take clauses the same way.
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

The result is a normal `Select`, `Update` or `Delete` of the exact type
the statement had, so chain onto it freely:

```python
from sqlalchemy import update

with ItemClauses.owned_by_user.bind(user_id=42):
    stmt = fr.apply_clauses(select(Item), ItemClauses.visible).where(Item.id == item_id)

    restore = (
        fr.apply_clauses(update(Item), ItemClauses.trashed)
        .where(Item.id == item_id)
        .values(deleted_at=None)
    )
```

(binding-values)=
## Binding values

`bind()` binds values
for the duration of a `with` block. Each value is routed to the leaf
clause whose function accepts its name, so binding on a composite is
equivalent to binding on the leaf itself:

```python
with ItemClauses.visible.bind(user_id=42):
    stmt = fr.apply_clauses(select(Item), ItemClauses.visible)
```

Because routing targets the leaf, one bind reaches every composite that
shares it: binding `user_id` on `owned_by_user` serves `visible`,
`trashed`, and anything declared later, without registration.

For values shared throughout a request, use
[Current's dependency binding](#current-request-binding) and
[reference those members from clauses](#shared-params).

The system verifies that bindings exist and route to the right slot.
The application must validate the value before binding it.

Binding fails loud in each direction. Resolving a clause whose value is
not bound raises `LookupError` while the statement is built, never a
silently unfiltered query. Binding a name no clause in the tree accepts
raises `TypeError: no clause accepts: ...`. Binding a name that two
distinct leaves accept raises, with the fix: bind it on each leaf
directly.

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

This path skips the table validation that `apply_clauses` performs. A
clause is never a boolean:
`if ItemClauses.is_deleted:` raises `TypeError` instead of always passing.

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
    items = fr.apply_clauses(select(Item), owned_item)
    collections = fr.apply_clauses(select(Collection), owned_collection)
```

Both clauses use the same member, so one binding supplies both.
`Current.user_id` without parentheses is the placeholder.
`Current.user_id()` reads the value immediately, which would require
an active binding at declaration time.

Put the column first. `Item.user_id == Current.user_id` builds SQL because
the column builds the comparison. `Current.role != "member"` raises
`TypeError`: Python would compare the member object, and the rule would
compile to `WHERE true`. A rule that compares a bound value with a constant
is a Python branch, so write it as a clause function that reads
`Current.role()`.

A clause function reads `Current` in its body:

```python
@fr.where_clause
def owned_by_current_user() -> ColumnElement[bool]:
    return Item.user_id == Current.user_id()
```

Clause binding cannot discover reads inside a function body.
`owned_by_current_user.bind(user_id=42)` therefore raises
`no clause accepts`. Bind through `Current` when using this form. A
missing value still raises when the function runs. A placeholder inside
an expression returned by a function is also invisible to binding
discovery.

Use the embedded form when the clause must accept `user_id=` in its own
`bind()`.
[Bind Current from FastAPI dependencies](#current-request-binding) for
values supplied per request.

A {class}`ContextParam <fastapi_restly.clauses.ContextParam>` supplies a
value, not a predicate. Boolean composition rejects it. Two distinct
members under the same name in one expression also raise.

```{seealso}
{doc}`api/clauses` lists every symbol with its signature.
```
