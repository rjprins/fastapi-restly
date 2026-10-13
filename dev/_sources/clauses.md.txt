# Query Clauses

A query clause is a named SQLAlchemy condition that you can reuse: a
{class}`WhereClause <fastapi_restly.clauses.WhereClause>`. Declare it once,
combine it with other clauses, and apply it to any statement. A clause takes
no arguments. A value that changes per request, such as the current user,
comes from a context member, and Restly reads it each time the clause is
applied.

This example uses the same rules to list and to count the tasks of a user
that are not deleted:

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
    tasks = fr.apply_clauses(select(Task), TaskClauses.visible)
    count = fr.apply_clauses(select(func.count(Task.id)), TaskClauses.visible)
```

Both statements select the tasks of user 42 that are not deleted. The parts:

- {func}`fr.where_clause <fastapi_restly.clauses.where_clause>` makes a clause
  from a SQLAlchemy condition, or from a function that returns one. See
  [Declare a clause](#declaring-clauses).
- `Current.user_id` is a member of a
  {class}`ContextNamespace <fastapi_restly.clauses.ContextNamespace>`. In the
  condition it is a placeholder. A binding around the code that applies the
  clause gives it a value. See [Give a clause its values](#binding-values).
- {class}`ClauseNamespace <fastapi_restly.clauses.ClauseNamespace>` groups
  the clauses of the model. `default_scope = visible` makes `visible` the
  scope of every {class}`RestView <fastapi_restly.views.RestView>` read and
  every reference check on `Task`. See [Scopes](#default-scope).
- {func}`fr.all_of <fastapi_restly.clauses.all_of>` and
  {func}`fr.none_of <fastapi_restly.clauses.none_of>`
  [combine clauses](#composing-clauses), and
  {func}`fr.apply_clauses <fastapi_restly.clauses.apply_clauses>`
  [applies them to a statement](#applying-clauses).

(declaring-clauses)=
## Declare a clause

The examples below use three models:

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

    collection: Mapped[Collection] = relationship()
    subscriptions: Mapped[list["Subscription"]] = relationship()


class Subscription(Base):
    __tablename__ = "subscription"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("item.id"))
    status: Mapped[str]
```

{func}`fr.where_clause <fastapi_restly.clauses.where_clause>` makes a clause.
A ready SQLAlchemy condition needs no function:

```python
import fastapi_restly as fr

is_deleted = fr.where_clause(Item.deleted_at.is_not(None))
recently_created = fr.where_clause(
    Item.created_at > func.now() - timedelta(days=7)
)
```

A clause is a condition and never joins. For a condition on a related table,
use the relationship's `any()` or `has()`, which generate `EXISTS`. A join
for this purpose repeats rows on a one-to-many relationship. `EXISTS` cannot.

```python
has_active_subscription = fr.where_clause(
    Item.subscriptions.any(Subscription.status == "active")
)
```

When the condition needs a Python `if`, decorate a function. The function
takes no parameters, and it runs each time the clause is applied:

```python
from sqlalchemy import ColumnElement, true


@fr.where_clause
def owned_unless_admin() -> ColumnElement[bool]:
    if Current.is_admin():
        return true()
    return Item.user_id == Current.user_id()
```

A function with parameters raises `TypeError` when you declare the clause.
The values that a condition needs come from context members; see
[Give a clause its values](#binding-values).

(clause-namespaces)=
## Group the clauses of a model

Put the clauses of a model in one
{class}`fr.ClauseNamespace <fastapi_restly.clauses.ClauseNamespace>`
subclass, and declare `model` in the class body. When Python defines the
class, the namespace checks itself and registers as the clause namespace of
the model:

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

A later line in the class body can use an earlier name, as `visible` shows.
The `@staticmethod` under the decorator is for the type checker, which reads a
plain `def` in a class body as a method; `where_clause` removes it. Use the
clauses through the class name, such as `ItemClauses.visible`. A type checker
follows this plain attribute access.

Name the namespace after the model, and define it in the module of the model.
Then importing the model always registers the namespace.

One name is reserved: the clause named `default_scope` is the scope of every
view read and every reference check on the model. [Scopes](scopes.md) owns
that topic. A tenant rule that must hold under every scope is not a clause;
it is a session rule, described in [Multi-Tenancy](#tenant-row-scoping).

:::{admonition} Details
:class: details

`model` is optional. A namespace without a model is a plain group of clauses,
or a base class for namespaces that declare one. Only a namespace with
`model` registers.

The SaaS example groups the task rules this way, with a clause function for
the member role, in `TaskClauses` in
[`app/tasks/models.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/tasks/models.py).

The namespace checks itself when Python defines it. A public attribute that is
not a clause raises. This catches a condition that you forgot to wrap:

```python
class ItemClauses(fr.ClauseNamespace):
    model = Item
    is_deleted = Item.deleted_at.is_not(None)   # TypeError at import
```

A helper or a constant is allowed when its name starts with an underscore. A
second namespace for the same model raises.

Name a clause so that it reads as true after `WHERE`: `owned_by_user`,
`is_deleted`, `has_active_subscription`. Do not add `_filter` or `_clause`;
the namespace and the type already say what the attribute is.
:::

(composing-clauses)=
## Combine clauses

{func}`fr.all_of <fastapi_restly.clauses.all_of>`,
{func}`fr.any_of <fastapi_restly.clauses.any_of>` and
{func}`fr.none_of <fastapi_restly.clauses.none_of>` combine clauses into a new
clause:

```python
live_and_owned = fr.all_of(ItemClauses.owned_by_user, fr.none_of(is_deleted))
active_or_new = fr.any_of(has_active_subscription, recently_created)
```

`all_of` is AND, and `any_of` is OR. `none_of(a, b)` is NOT over `a OR b`: it
is true when none of the clauses holds. Called with no arguments, each of the
three raises.

(clause-in-expression)=
### Use a clause inside another condition

A clause can go inside the condition of another clause. Write it there
without a call. This is how a rule follows a relationship: an item is live
when its collection is not archived.

```python
class CollectionClauses(fr.ClauseNamespace):
    model = Collection

    is_archived = fr.where_clause(Collection.archived_at.is_not(None))
    owned_by_user = fr.where_clause(Collection.user_id == Current.user_id)


in_live_collection = fr.where_clause(
    Item.collection.has(fr.none_of(CollectionClauses.is_archived))
)
in_own_collection = fr.where_clause(
    Item.collection.has(CollectionClauses.owned_by_user)
)
```

SQLAlchemy builds the outer condition once, when the module runs. A context
member inside it, such as `Current.user_id`, stays a placeholder. The outer
clause reads the member each time it is applied, as it reads a member in its
own condition.

The blog example gives comments the scope of their post this way:
`CommentClauses` in
[`blog/main.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/blog/blog/main.py).

:::{admonition} Details
:class: details

A clause function runs each time its clause is applied, so it has no
condition yet when Python builds the outer condition. A clause function inside
a condition raises `TypeError`, and so does a combined clause that contains
one. Write the outer condition as a clause function too, and call the inner
clause in its body:

```python
from sqlalchemy import ColumnElement


@fr.where_clause
def in_owned_collection() -> ColumnElement[bool]:
    return Item.collection.has(collection_owned_unless_admin())
```

Here `collection_owned_unless_admin` stands for any clause function on
`Collection`.

In plain SQLAlchemy there is no outer clause to read the members. There, call
a clause that contains members, as
[Use a clause in plain SQLAlchemy](#clauses-in-plain-sqlalchemy) shows.
Without the call, SQLAlchemy rejects the statement when it runs, because the
members have no value.
:::

(unscoped-composition)=
### Combine with `UNSCOPED`

`fr.clauses.UNSCOPED` means no limit. It is a `WhereClause` whose condition
is SQL `TRUE`, so it accepts every row. It works everywhere a clause works,
and calling it returns `sqlalchemy.true()`.

The combining functions simplify it with these rules. `C` is a clause, and
the order of the arguments does not change the result:

| Expression | Result |
|---|---|
| `fr.all_of(UNSCOPED, C)` | `C` itself |
| `fr.all_of(UNSCOPED)` | `UNSCOPED` itself |
| `fr.all_of(UNSCOPED, UNSCOPED)` | `UNSCOPED` itself |
| `fr.any_of(UNSCOPED, C)` | `UNSCOPED` itself |
| `fr.any_of(UNSCOPED)` | `UNSCOPED` itself |
| `fr.none_of(UNSCOPED, C)` | A `WhereClause` over `sqlalchemy.false()` |
| `fr.none_of(UNSCOPED)` | A `WhereClause` over `sqlalchemy.false()` |

`all_of` drops its `UNSCOPED` arguments and combines the rest. With one clause
left, it returns that same object. With none left, it returns `UNSCOPED`. So
a condition can extend a default scope that may be `UNSCOPED`, without an
`if`:

```python
scope = fr.all_of(ItemClauses.default_scope, has_active_subscription)
```

:::{admonition} Details
:class: details

In AND, `UNSCOPED` changes nothing. In OR and NOT it decides the result:
every row OR the published rows is still every row, and NOT over that
matches no rows. The false clause works as a scope too: lists and counts are
empty, and single-row reads and reference checks return `404`.

Each combining function returns a `WhereClause`, since `UNSCOPED` is one too.
So the result works everywhere a clause works, also when it is `UNSCOPED`.
Passing `UNSCOPED` counts as an argument for the rule that a call needs at
least one.

Restly still checks the other arguments when `UNSCOPED` decides the result. A
raw expression or a `ContextParam` in `all_of`, `any_of` or `none_of` still
raises. A clause that OR or NOT makes irrelevant is not evaluated, so it
needs no bound values.

`apply_clauses(stmt, UNSCOPED)` adds no condition. Filters already on `stmt`
stay, and the other clauses you pass still apply. `UNSCOPED` does not remove
a filter from a statement.
:::

(binding-values)=
(shared-params)=
## Give a clause its values

A clause takes no arguments. A value that changes per request or per call is
a member of a
{class}`ContextNamespace <fastapi_restly.clauses.ContextNamespace>`: the
[Current](context.md) of the application, or a small namespace next to
the code that uses it. The examples use the `Current` declared at the top of
this page.

Put the member in a condition. It becomes a placeholder, which gets the bound
value each time the clause is applied:

```python
owned_item = fr.where_clause(Item.user_id == Current.user_id)
owned_collection = fr.where_clause(Collection.user_id == Current.user_id)

with Current.bind(user_id=42):
    items = fr.apply_clauses(select(Item), owned_item)
    collections = fr.apply_clauses(select(Collection), owned_collection)
```

One binding gives a value to every clause that uses the member.
`Current.user_id` without parentheses is the placeholder. In a condition that
the module builds, `Current.user_id()` would read the value at once, so it
would need a binding when the module runs.

When the clause is applied, a member without a binding raises a
`LookupError` that names the member. The query never runs without its
filter. For values per request,
[bind Current from a FastAPI dependency](#current-request-binding).

:::{admonition} Details
:class: details

Put the column first. `Item.user_id == Current.user_id` builds SQL, because
the column builds the comparison. `Current.role != "member"` raises
`TypeError`: Python would compare the member object itself, and the rule
would become `WHERE true`. A rule that compares a bound value with a constant
is a Python `if`, so write it as a clause function that reads the member in
its body:

```python
@fr.where_clause
def owned_by_current_user() -> ColumnElement[bool]:
    return Item.user_id == Current.user_id()
```

Arithmetic with the member first, such as `Current.now - Item.created_at`,
raises `TypeError` when the clause is applied, and so does a member inside a
plain tuple. SQLAlchemy would send the member object to the database as a
value. Wrap the member as `sqlalchemy.type_coerce(Current.now, DateTime)`, and
write a tuple as `sqlalchemy.tuple_(...)`.

Both forms read the member when the clause is applied: by `apply_clauses`, by
a view read, by a reference check, or when you call the clause. The statement
keeps the values, so it can run after the binding has ended:

```python
def owned_items_stmt(user_id: int):
    with Current.bind(user_id=user_id):
        return fr.apply_clauses(select(Item), owned_item)


rows = session.scalars(owned_items_stmt(42))
```

A value that one part of the application uses gets its own namespace there:

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

Member names belong to their namespace. `ReportPeriod.start` and a `start` of
another namespace can be in one statement. Neither clashes with a column or a
hand-written `bindparam()` of the same name: each placeholder becomes its own
numbered parameter, such as `:start_1`.

A placeholder gets its value only when a clause is applied. An expression
that has a member but never passes through a clause fails when it runs, with
SQLAlchemy's "A value is required for bind parameter". Call the clause to get
its expression, or read the value with `Current.user_id()`.
:::

(applying-clauses)=
## Apply clauses to a statement

You build the statement with plain SQLAlchemy.
{func}`fr.apply_clauses <fastapi_restly.clauses.apply_clauses>` adds the
conditions of the clauses to its `WHERE`:

```python
from sqlalchemy import select

with Current.bind(user_id=42):
    stmt = fr.apply_clauses(
        select(Item), ItemClauses.visible, has_active_subscription
    )
```

:::{admonition} Details
:class: details

`update()` and `delete()` statements take clauses in the same way. The result
has the type of the statement you passed, a `Select`, an `Update` or a
`Delete`, so you can keep building on it:

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

The SaaS example applies the scope of another view this way, in
[`app/projects/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/projects/views.py).

`apply_clauses` rejects a condition on a table that the statement does not
select from:

```python
fr.apply_clauses(select(Item), fr.where_clause(Collection.archived_at.is_(None)))
# TypeError: clause references table(s) not in the statement: collection;
#   express the condition as EXISTS (.any()/.has())
```

Without this check, SQLAlchemy would add the missing table to the `FROM`, and
the query would combine every item with every collection, so the condition
would filter almost nothing. A condition inside an `EXISTS` or an
`IN (SELECT ...)` subquery has its own `FROM`, and passes the check.
:::

(clauses-in-plain-sqlalchemy)=
## Use a clause in plain SQLAlchemy

Calling a {class}`WhereClause <fastapi_restly.clauses.WhereClause>` returns its
SQLAlchemy condition, a `ColumnElement`. You can use it anywhere SQLAlchemy
takes a condition: a `.where()`, a join condition, a `CASE`:

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

The call reads context members, as any other use of the clause does. It
skips the table check of `apply_clauses`. A clause is never a boolean:
`if ItemClauses.is_deleted:` raises `TypeError`, so it cannot pass by mistake.

(call-site-values)=
## Use a value the caller already has

A condition whose value is known where you build the statement needs no
clause. Write a plain function that returns the expression:

```python
def name_matches(term: str) -> ColumnElement[bool]:
    return Item.name.ilike(f"%{term}%")


with Current.bind(user_id=42):
    stmt = fr.apply_clauses(select(Item), ItemClauses.visible).where(
        name_matches(term)
    )
```

Where Restly needs a clause, such as a per-read `scope=`, wrap the
expression:

```python
scope = fr.all_of(ItemClauses.visible, fr.where_clause(name_matches(term)))
```

A list route on a view takes the expression directly:
[`handle_get_many(list_params, where=...)`](#per-read-where) narrows the read
inside the scope.

## See also

- [Scopes](scopes.md): the clause that every read and every reference check
  of a model applies.
- [Request Context](context.md): declare context members and bind
  them.
- [Multi-Tenancy](tenancy.md): the tenant rule as a session rule.
- {doc}`api/clauses`: every name with its signature.
