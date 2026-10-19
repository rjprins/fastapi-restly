# Scopes

A scope decides which rows of a model exist for a request. Restly applies it
to every read of the model and to every reference to the model. A row outside
the scope is not found: the list does not show it, the total does not count
it, and `GET /{id}` returns 404.

A scope is a [query clause](clauses.md) in a special role. Three words say
who decides which rows a request sees:

| Word | Who decides | What it is |
|---|---|---|
| **clause** | your code | a named query rule that you can reuse: `ItemClauses.visible` |
| **scope** | the server | the clause that every read and every reference check applies |
| **filter** | the client | what the request asks for in [query parameters](filtering.md) |

A filter narrows the rows inside the scope. It can never show a row that the
scope hides.

You declare a scope in one of three places. The most specific one wins:

- The model's `default_scope`, for every read and every reference check:
  [Hide rows from every read of a model](#default-scope).
- A view's `scope`, for the reads of that view:
  [Give one view other rows](#view-scope).
- The `scope=` argument of a read, for that one read:
  [Read other rows on one route](#per-read-scope).

A scope lower in this list replaces the one above it. It does not add to it.
So a rule that must hold under every scope, such as the tenant rule, is a
[session rule](#tenant-row-scoping), not a scope.
`fr.clauses.UNSCOPED` is the explicit way to read without a scope, in each of
the three places.

A scope decides which rows exist, so a hidden row is a 404. Whether the caller
may do an action on a row is a question for
[`authorize`](#authorize-gate), and a refusal there is a 403.

The examples on this page use an `Item` model and an `ItemClauses` namespace
for soft deletion:

```python
class ItemClauses(fr.ClauseNamespace):
    model = Item

    is_deleted = fr.where_clause(Item.deleted_at.is_not(None))
    visible = fr.none_of(is_deleted)
    trashed = is_deleted

    default_scope = visible
```

(default-scope)=
## Hide rows from every read of a model

`default_scope` is a reserved name on
{class}`fr.ClauseNamespace <fastapi_restly.clauses.ClauseNamespace>`. The
clause with that name is the scope of the model. The one line
`default_scope = visible` covers two things:

- **Every view read of the model.** The list, the count and `GET /{id}` apply
  the scope. So a row outside it is missing from pages and totals, and
  `GET /{id}` returns 404. Update and delete load the row through the same
  read, so a deleted row cannot be changed either.
- **Every reference to the model.** A
  {class}`fr.MustExist <fastapi_restly.schemas.MustExist>`,
  {class}`fr.IDRef <fastapi_restly.schemas.IDRef>` or
  {class}`fr.IDSchema <fastapi_restly.schemas.IDSchema>` field that points
  at the model checks that the row exists inside the scope. So a write that
  references a deleted row returns 404.

A model without a `default_scope` has
{data}`fr.clauses.UNSCOPED <fastapi_restly.clauses.UNSCOPED>`. Its views read
every row, and its reference checks only look up the primary key.

A subclass of a model takes the nearest `default_scope` that its parent
models declare. A namespace on the subclass that declares no
`default_scope` keeps that scope. A namespace that declares one replaces it
for the subclass. To read without a scope, the subclass declares
`default_scope = fr.clauses.UNSCOPED`. So a subclass never loses the scope of
its base by accident. `default_scope = None` raises `TypeError`, because it
does not say what it means.

`UNSCOPED` is the only way to say "no scope", here and everywhere else. Search
for `UNSCOPED` to find where code reads past a scope. A variable or a
composed clause can also pass `UNSCOPED` on, so follow those too.
[Combine with `UNSCOPED`](#unscoped-composition) shows what `fr.all_of`,
`fr.any_of` and `fr.none_of` do with it.

:::{admonition} Details
:class: details

`default_scope` is a {class}`WhereClause <fastapi_restly.clauses.WhereClause>`.
A rule that depends on a related table uses `EXISTS`, with `.any()` or
`.has()`. A raw SQLAlchemy expression raises `TypeError` when the class is
defined.
:::

### Rows that a scope does not reach

A scope covers reads *of* its model and references *to* it. It does not
reach through relationships. A response that embeds a related model, such as
the comments of an item, loads those rows through the relationship, without
their scope. A filter or sort on a relationship field, such as
`?items.name=x`, also joins the related table without its scope. Where
embedded rows must be filtered, shape the view's schema, or override
{meth}`get_loader_options <fastapi_restly.views.BaseRestView.get_loader_options>`.

For the same reason, a model does not follow the scope of a related model.
To make it follow, give the model its own `default_scope` that names the
other scope. Here a comment is visible when its post is visible:

```python
class CommentClauses(fr.ClauseNamespace):
    model = Comment

    default_scope = fr.where_clause(Comment.post.has(PostClauses.default_scope))
```

[Use a clause inside another condition](#clause-in-expression) explains how the
inner clause gets its values.

(view-scope)=
## Give one view other rows

A view that must see other rows than the default declares its own
{attr}`scope <fastapi_restly.views.BaseRestView.scope>`:

```python
@fr.include_view(app)
class ItemView(fr.AsyncRestView):
    prefix = "/items"
    model = Item
    schema = ItemSchema
    # no scope declared: reads apply ItemClauses.default_scope


@fr.include_view(app)
class TrashView(fr.AsyncRestView):
    prefix = "/trash"
    model = Item
    schema = ItemSchema
    scope = ItemClauses.trashed
```

A view's scope replaces the default scope. It does not add to it. So
`ItemClauses.trashed` must describe all the rows that the trash shows.

A view that needs every row declares `scope = fr.clauses.UNSCOPED`.
`scope = None`, the default, does not mean "no scope". It means "use the
model's default scope".

```python
@fr.include_view(admin_router)      # a router with admin authentication
class AdminItemView(fr.AsyncRestView):
    prefix = "/admin/items"
    model = Item
    schema = ItemSchema
    scope = fr.clauses.UNSCOPED
```

`UNSCOPED` removes only the scope. A session rule, such as the tenant rule,
still applies. [Let an admin read every tenant](#tenant-admin-bypass) shows
how an admin can read every tenant.

:::{admonition} Details
:class: details

A `scope` that is not a clause raises
{class}`fr.exc.RestlyConfigurationError <fastapi_restly.exc.RestlyConfigurationError>`
when the view class is defined. Restly applies the scope to every read itself,
and a view has no method to change how it is applied. A route that builds
its own query gets the scope from
[`fr.resolve_scope`](#reading-the-scope).
:::

(read-scope)=
## Show each caller only their own rows

A scope can read a [`Current`](context.md) member. This view shows each
user only the documents that the user owns:

```python
class Current(fr.ContextNamespace):
    user_id: fr.ContextParam[int]


@fr.include_view(app)
class DocumentView(fr.AsyncRestView):
    prefix = "/documents"
    model = Document
    schema = DocumentSchema
    scope = fr.where_clause(Document.owner_id == Current.user_id)
```

Like every scope, it covers every read of the view: the list, the total and
`GET /{id}`. The update and delete routes load the row through it too, and so
does a custom route that calls `get_one(id)` or starts from `self.select()`. Declare the rule as the model's
`default_scope` instead when every view and every reference check must
follow it.

:::{admonition} Details
:class: details

A view has no hook that picks a scope per request, and it needs none. To
choose per request, write a clause function that reads a bound value:

```python
from sqlalchemy import ColumnElement, true


class RoleContext(fr.ContextNamespace):
    include_deleted: fr.ContextParam[bool]


@fr.where_clause
def role_visibility() -> ColumnElement[bool]:
    if RoleContext.include_deleted():
        return true()
    return Item.deleted_at.is_(None)


class ItemView(fr.AsyncRestView):
    ...
    scope = role_visibility  # a dependency binds RoleContext.include_deleted
```

A rule like this one belongs to the view that uses it, so declare it next to
that view. The model's namespace holds the rules for every read of the model:
facts such as `is_deleted`, and rules such as "a draft is visible only to its
author". A rule in the namespace may read a request value, such as
`Current.user_id`.
:::

(binding-scope-values)=
### Bind the values that a scope reads

A scope is a clause, so its values come from context members, as
[Give a clause its values](#binding-values) explains. Bind the values for each
request with a generated dependency, as
[Bind from a FastAPI dependency](#current-request-binding) shows:

```python
app = FastAPI(dependencies=[Current.depends(user_id=get_user_id)])
```

A read without a bound value fails, and never returns the rows without the
filter; see [Give a clause its values](#binding-values). Restly makes sure
that the clause is applied and that its values are bound. The source
dependency must make sure that the value is the right user. When a query
returns other rows than you expect,
[`Current.explain()`](#current-explain) names the `depends()` line that bound
each value.

(per-read-scope)=
## Read other rows on one route

A custom route can pass a clause as `scope=` to the handler. That clause
replaces the view's scope for this one read. So the trash list and the
restore action can be routes on the same view, not a second view class:

```python
from fastapi_restly.views import ViewID, ViewListResponse, ViewResponse


@fr.include_view(app)
class ItemView(fr.AsyncRestView):
    prefix = "/items"
    model = Item
    schema = ItemSchema
    # no scope declared: reads apply ItemClauses.default_scope

    @fr.get("/trash")
    async def trash(self, list_params) -> ViewListResponse:
        return await self.handle_get_many(list_params, scope=ItemClauses.trashed)

    @fr.post("/{id}/restore", status_code=200)
    async def restore(self, id: ViewID) -> ViewResponse:
        item = await self.get_one(id, scope=ItemClauses.trashed)
        async with self.write_action("restore", obj=item):
            item.deleted_at = None
        return item
```

The `list_params` argument gives the route the same filter, sort and page
parameters as `GET /`, as [Filtering & Pagination](filtering.md) explains.

The argument replaces the scope. It does not narrow it. So a rule that must
never be replaced belongs on the session, like the
[tenant rule](#tenant-row-scoping).

:::{admonition} Details
:class: details

`handle_get_many` calls `authorize` and passes `scope=` on to `get_many`.

The restore route loads the row with `get_one(id, scope=...)`, not with
`handle_get_one`, because
{meth}`write_action <fastapi_restly.views.RestView.write_action>` authorizes
the action itself; see [Add a custom action route](#custom-action-route). The
route reads only the trash, so a row that is not deleted returns 404 here. To
read without any scope on one route, pass `scope=fr.clauses.UNSCOPED`.

The markers `ViewListResponse`, `ViewResponse` and `ViewID` give the two
routes the view's classes, `ItemListResponse` and `ItemResponse`, and its id
type. So OpenAPI shows the same types as for the CRUD routes, and Restly
sends the list result and the row through the view's response step.
[Name the view's classes in a custom route](#name-the-response-classes)
explains the markers.

`get_one`, `get_many` and `select()` take the same `scope=` argument. A
custom read that starts from `self.select(scope=ItemClauses.trashed)` sees
the same rows as the trash route. The SaaS example puts a trash and a restore route on a base view,
`TrashView`, in
[`app/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/views.py).
:::

(per-read-where)=
### List a subset inside the scope

{meth}`handle_get_many <fastapi_restly.views.RestView.handle_get_many>`
also takes `where=`. It narrows the read and keeps the scope. Here a list of
the items in one collection still hides deleted items:

```python
@fr.get("/in-collection/{collection_id}")
async def in_collection(self, collection_id: int, list_params) -> ViewListResponse:
    return await self.handle_get_many(
        list_params, where=Item.collection_id == collection_id
    )
```

If you pass the same expression as `scope=fr.where_clause(...)`, it replaces
the scope, so the list would include deleted items.

:::{admonition} Details
:class: details

`where=` takes a SQLAlchemy boolean expression or a clause. Restly combines
it with the scope, as {func}`fr.all_of <fastapi_restly.clauses.all_of>`
would: with the view's scope, or with the `scope=` argument when you pass
both. The page and `total_count` both apply it, and the client's filters,
sort and page apply on top.

{meth}`get_many <fastapi_restly.views.RestView.get_many>` takes the same
`where=`, for a route that lists without `authorize`. A Python `bool`, such
as a comparison on a loaded object, raises `TypeError`.
:::

(reading-the-scope)=
## Use the scope in your own query

A route that builds its own query of the view's model starts from
{meth}`self.select() <fastapi_restly.views.BaseRestView.select>`. That is
`select(model)` with the view's scope, so the route sees the same rows as
`GET /` and `GET /{id}`. This count route applies the client's filters to
the scoped statement:

```python
@fr.get("/count")
async def total(self, list_params) -> int:
    await self.authorize(fr.Action.GET_MANY)
    return await self.count(await self.apply_filters(self.select(), list_params))
```

{meth}`apply_filters <fastapi_restly.views.RestView.apply_filters>` applies
the filters without the sort and the page; see
[Use the filters in a route of your own](#filters-in-own-route).

Do not name that route method `count`. It would hide the
{meth}`count <fastapi_restly.views.RestView.count>` method that it calls.

For a statement that `self.select()` cannot start, such as a select of some
columns or of another model,
{func}`fr.resolve_scope <fastapi_restly.views.resolve_scope>` returns the
clause that a view's reads apply. Apply it with
{func}`fr.apply_clauses <fastapi_restly.clauses.apply_clauses>`, as in
`fr.apply_clauses(sa.select(Task.status), fr.resolve_scope(self))`.

Pass another view, as in `fr.resolve_scope(ProjectView)`, to follow the rows
that view shows. The parent lookup of a nested view does this, so a parent
that its own view hides is also a 404 under the child's path; see
[Which parents a client can reach](#nested-parent-access).

Pass a model class, as in `fr.resolve_scope(Task)`, for the model's
`default_scope` only. That is the scope that every reference check applies,
and that every view without its own scope uses. The two results differ
wherever a view declares a scope. So code outside a request that must see
what the API shows passes the view.

:::{admonition} Details
:class: details

The result is a clause or `fr.clauses.UNSCOPED`, never `None`. So it goes
straight into `fr.apply_clauses` or into a composition, without an `if`.
:::

(soft-delete-mixin)=

## Soft-delete rows

A soft delete marks a row as deleted and keeps it in the table. It has three
parts:

- The model has a `deleted_at` column.
- The model's [default scope](#default-scope) hides deleted rows from every
  read and every reference check, as `default_scope = visible` does in the
  `ItemClauses` at the top of this page.
- A view mixin overrides `delete`, so `DELETE` sets `deleted_at` instead of
  removing the row.

The column comes from a model mixin:

```python
from datetime import datetime

from sqlalchemy import orm


class SoftDeletable(orm.MappedAsDataclass, kw_only=True, eq=False, repr=False):
    deleted_at: orm.Mapped[datetime | None] = orm.mapped_column(default=None)
```

(soft-delete-recipe)=

### Make `DELETE` set a timestamp

Override the {meth}`delete <fastapi_restly.views.RestView.delete>` business
method. It receives the loaded row. Set the timestamp, flush, and do not call
`super()`, because that would remove the row:

```python
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


class SoftDeleteMixin:
    """``delete`` sets ``deleted_at`` instead of removing the row."""

    session: AsyncSession

    async def delete(self, obj: Any) -> None:
        obj.deleted_at = datetime.now(timezone.utc)
```

The handler saves the change after `delete` returns, as after every business
method, and the hooks get the row as `new`. If only one view needs it, put the same `delete` method on that view. The
mixin has no `hasattr` check and no fallback to `super()`. It goes on views of
`SoftDeletable` models, so the column is always there.

{meth}`handle_delete <fastapi_restly.views.RestView.handle_delete>` still
loads the row through the scope, calls `authorize` and commits. The mixin only
changes what "delete" does. A query parameter can never widen a scope, so a
client cannot ask for deleted rows. To list the deleted rows or restore one,
add a route that reads other rows.
[Read other rows on one route](#per-read-scope) shows a trash list and a
restore route on the same view.

(coming-from-rails)=
## Coming from Rails, Laravel or Phoenix

The names map like this:

| In Rails, Laravel or Phoenix | In Restly |
|---|---|
| A Rails scope, or a Laravel local scope | A [clause](clauses.md) |
| Rails `default_scope`, or a Laravel global scope | A scope: the model's [`default_scope`](#default-scope) |
| A Phoenix `Scope`, which holds the caller | [`Current`](context.md) |

Restly's `default_scope` leaves out the parts of Rails' `default_scope` that
cause problems. It decides only which rows reads and reference checks see. It
does not set column values when you create a row. And nothing reads past it
without saying so. A view declares a replacement scope or
`fr.clauses.UNSCOPED` in its class body, so a search finds every place.

Phoenix passes its `Scope` as an argument to each context function. Restly
binds `Current` once per request, and code reads it where it needs it.

A tenant rule that is a scope in your old framework is a
[session rule](#tenant-row-scoping) in Restly.

(migrating-build-query)=
## Migrating from `build_query`

Restly's earlier way to change reads, an override of `build_query()`, is
removed. A view class that still defines it, itself or through a mixin,
raises an error when the class is defined, and the error points here. All
that `build_query` did is still possible. One thing works differently:
`build_query` overrides added up through `super()` calls, but scopes replace
each other. So a chain of filters becomes one combined clause:

```python
# before
class ItemView(fr.AsyncRestView):
    def build_query(self):
        return super().build_query().where(Item.deleted_at.is_(None))

# after: the rule becomes a clause on the model, and the override disappears
class ItemClauses(fr.ClauseNamespace):
    model = Item
    default_scope = fr.where_clause(Item.deleted_at.is_(None))
```

Relationship loading that was in `build_query` goes in
{meth}`get_loader_options <fastapi_restly.views.BaseRestView.get_loader_options>`.
Other changes to the list query, such as a join or a default order, go in an
{meth}`apply_list_params <fastapi_restly.views.RestView.apply_list_params>`
override:

```python
class ItemView(fr.AsyncRestView):
    ...

    async def apply_list_params(self, query, list_params):
        query = query.order_by(Item.created_at.desc())
        return await super().apply_list_params(query, list_params)
```

An order that you add before `super()` comes first, so a client's `?sort=`
orders the rows inside it.

For a condition on a to-many relationship, use `EXISTS`, such as
`query.where(Item.comments.any(Comment.author_id == user_id))`. A join to a
to-many relationship repeats the item for each comment, as in SQLAlchemy. A
page would then count comments, not items.

## See also

- [Query Clauses](clauses.md): declare, compose and apply the clauses that a
  scope is made of.
- [Multi-Tenancy](tenancy.md): the tenant rule, which holds under every
  scope.
- [Authorization](#authorize-gate): decide what the caller may do with the
  rows that the scope shows.
