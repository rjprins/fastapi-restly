# Scopes

A scope is the clause the server imposes on every read of a model and on
every reference to it: the row-visibility rule. Scope is a *role* a
clause plays, not a kind of clause; the material is a plain
[query clause](clauses.md). The word completes a three-way vocabulary:

| Word | Who decides | What it is |
|---|---|---|
| **clause** | the declaration | a named, reusable query fragment: `ItemClauses.visible` |
| **scope** | the server | the clause every read and every reference check applies |
| **filter** | the client | what the request asks for through [query parameters](howto_query_modifiers.md) |

A filter narrows within the scope; it can never widen past it.

The examples below use an `Item` model and an `ItemClauses` namespace for soft
deletion. [Query Clauses](clauses.md) defines the clause operations:

```python
class ItemClauses(fr.ClauseNamespace):
    model = Item

    is_deleted = fr.where_clause(Item.deleted_at.is_not(None))
    visible = fr.none_of(is_deleted)
    trashed = is_deleted

    default_scope = visible
```

(default-scope)=
## The model's default scope

`default_scope` is a reserved attribute name on
{class}`fr.ClauseNamespace <fastapi_restly.clauses.ClauseNamespace>`: the
clause under that name is the scope for the model. The one line
`default_scope = visible` arms two surfaces at once:

- **Every view read on the model.** List, count, and retrieve apply the
  scope, so a row outside it is absent from pages and totals and returns
  404 from `GET /{id}`. Update and delete load through the same scoped
  retrieve, so a deleted row cannot be changed through the live surface.
- **Every reference to the model.** A
  {class}`fr.MustExist <fastapi_restly.schemas.MustExist>` /
  {class}`fr.IDRef <fastapi_restly.schemas.IDRef>` /
  {class}`fr.IDSchema <fastapi_restly.schemas.IDSchema>` field targeting
  the model checks existence inside the scope, so referencing a deleted
  row on a write returns 404.

Every model namespace has a `default_scope`, and its default is
{data}`fr.clauses.UNSCOPED <fastapi_restly.clauses.UNSCOPED>`:
a model that declares none reads unscoped, and
its reference checks are bare primary-key lookups. `UNSCOPED` is the one
explicit unscoped spelling everywhere a scope can appear. Searching for
`UNSCOPED` finds where unscoping is requested directly. Follow variables
and composed clauses to find the views and references that receive it.
Composition can absorb or propagate the sentinel, so the search does
not identify every resulting unscoped read.

In clause composition, `UNSCOPED` means accepting every row: it is a
no-op in `all_of`, makes `any_of` unscoped, and makes `none_of` match
no rows. It contributes nothing to `apply_clauses` and does not remove
other filters on a statement. See the
{ref}`full composition rules <unscoped-composition>` for object
identity, return types, and validation.

The scope guards reads *of* the model and references *to* it; it does
not reach through relationships. A scoped model serialized inside
another model's response (`ItemRead.comments` embedding a soft-deleted
comment, say) is loaded through the relationship, unscoped, and a
dotted query-parameter filter or sort (`?items.name=x`) joins the
related table without its scope. Shape the response schema, or override
{meth}`get_relationship_loader_options <fastapi_restly.views.BaseRestView.get_relationship_loader_options>`,
where embedded rows must be filtered.

`default_scope` is a
{class}`WhereClause <fastapi_restly.clauses.WhereClause>`: a predicate,
with EXISTS (`.any()`/`.has()`) for a rule that depends on a related
table. A raw expression is rejected at class definition.

A model *subclass* inherits the nearest declared `default_scope` along
its MRO. A namespace on the subclass that says nothing about
`default_scope` leaves the inherited scope in force; declaring one
replaces it for that subclass, and the opt-out is explicit:
`default_scope = fr.clauses.UNSCOPED`. A subclass can never drop the
base scope by omission, and `default_scope = None` is rejected: it
says nothing.

An unbound scope raises at request time, naming the missing value: a
scope with a request-bound value cannot be read silently unfiltered.

(view-scope)=
## Views: replacing the scope

A view that should see something other than the default declares its
own {attr}`scope <fastapi_restly.views.BaseRestView.scope>`:

```python
@fr.include_view(app)
class ItemView(fr.AsyncRestView):
    prefix = "/items"
    model = Item
    schema = ItemRead
    # no scope declared: reads apply ItemClauses.default_scope


@fr.include_view(app)
class TrashView(fr.AsyncRestView):
    prefix = "/trash"
    model = Item
    schema = ItemRead
    scope = ItemClauses.trashed          # the deviating view declares
```

Declaring a scope **replaces** the default; it does not stack on it.
`ItemClauses.trashed` is the complete alternate surface for deleted rows.

A tenant rule must hold under every scope a view or a route can name, so it
is not a scope. SQLAlchemy's
[`with_loader_criteria`](https://docs.sqlalchemy.org/en/20/orm/queryguide/api.html#adding-global-where-on-criteria),
added from a `do_orm_execute` listener, puts the predicate on every ORM
`SELECT` that selects or joins the class: view reads under any scope,
reference checks, lazy loads and hand-written selects alike. Restly's reads
and reference checks are ORM statements, so the rule reaches them. The
[tenant row scoping](#tenant-row-scoping) recipe shows the listener, the
column it restricts, and the statements it does not reach. The
[SaaS example](examples.md#saas) runs that recipe.

The explicit opt-out is `fr.clauses.UNSCOPED`: it reads past the model's
`default_scope`, where `None` would fall back to it. Reserve it for a
view that needs every model row. `UNSCOPED` removes only the model's default
scope. It does not disable session-level criteria. The tenant listener decides
separately whether an admin may cross tenants:

```python
@fr.include_view(admin_router)      # router with admin auth
class AdminItemView(fr.AsyncRestView):
    prefix = "/admin/items"
    model = Item
    schema = ItemRead
    scope = fr.clauses.UNSCOPED
```

There is no per-request hook, deliberately: a clause is already a
function. A per-request *value* (the tenant id) is bound around the
request; a per-request *choice* is a clause function branching on a
bound value, which fails loudly when the value is missing:

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

A policy clause like this names a request-boundary value, so it lives
beside the view that applies it, not in the model's namespace; the
namespace holds model facts such as `is_deleted`, and views compose policy
from them. The framework applies the declared clause
itself, on every read; there is no apply-side override point, and a
`scope` that is not a clause is rejected as the view class is defined.
[Reading the resolved scope](#reading-the-scope) hands a route the
clause without opening one.

(per-read-scope)=
## A route names its own scope

A custom route that reads another surface of the same model passes a
clause as `scope=` to the handler, and that clause replaces the resolved
scope for that one read. The trash listing and the restore action are
then routes on the view rather than a second view class:

```python
@fr.include_view(app)
class ItemView(fr.AsyncRestView):
    prefix = "/items"
    model = Item
    schema = ItemRead
    # no scope declared: reads apply ItemClauses.default_scope

    @fr.get("/trash", response_model=fr.views.PaginatedEnvelope[ItemRead])
    async def trash(self, query_params):
        result = await self.handle_get_many(query_params, scope=ItemClauses.trashed)
        return self.to_response(result, fr.ResponseShape.LISTING)

    @fr.post("/{id}/restore", response_model=ItemRead, status_code=200)
    async def restore(self, id: int):
        item = await self.get_one(id, scope=ItemClauses.trashed)
        async with self.write_action("restore", obj=item):
            item.deleted_at = None
        return item
```

`handle_get_many` runs `authorize` and forwards `scope=` to `get_many`;
declaring `query_params` gives the route the listing grammar of `GET /`
([Filter, Sort, and Paginate Lists](howto_query_modifiers.md)). The restore
loads with `get_one(id, scope=...)` rather than `handle_get_one`, because
a write action gates its own action inside
{meth}`write_action <fastapi_restly.views.RestView.write_action>`; a live
item is a 404 here, since the trash is the surface this route reads.
`fr.clauses.UNSCOPED` is the per-read opt-out, in the same spelling as
everywhere else.

The argument replaces the scope, it does not narrow it. Put rules that may
never be replaced, such as tenant isolation, at the session level. A `get_one`
or `get_many` override declares the `scope` parameter and passes it on to
`super()`. The handlers always pass it, so an override without the parameter
fails on its first call instead of serving the wrong rows.

(per-read-where)=
### Narrowing inside the scope

{meth}`handle_get_many <fastapi_restly.views.RestView.handle_get_many>`
also takes `where=`, which narrows the read and keeps the scope. A listing
of one collection's items still hides deleted items:

```python
@fr.get(
    "/in-collection/{collection_id}",
    response_model=fr.views.PaginatedEnvelope[ItemRead],
)
async def in_collection(self, collection_id: int, query_params):
    result = await self.handle_get_many(
        query_params, where=Item.collection_id == collection_id
    )
    return self.to_response(result, fr.ResponseShape.LISTING)
```

`where=` accepts a SQLAlchemy boolean expression or a clause. It is ANDed
into the scope the read would apply, as {func}`fr.all_of <fastapi_restly.clauses.all_of>`
would: the resolved scope by default, or the `scope=` argument when both are
given. The page and `total_count` both apply it, and the client's filters,
sort and page apply on top.

Passing the same expression as `scope=fr.where_clause(...)` replaces the
scope, so that listing would include deleted items.

The handler folds `where=` into the `scope` it forwards. A `get_many`
override receives one combined clause and declares no `where` parameter.
`get_many` itself does not take `where=`; a route that calls it directly
composes `fr.all_of(fr.resolve_scope(self), ...)`. A Python bool, such as a
comparison on a loaded object, raises `TypeError`.

(reading-the-scope)=
## Reading the resolved scope

{func}`fr.resolve_scope <fastapi_restly.views.resolve_scope>` returns the
clause a view's reads apply, so a route that builds its own query sees
the rows `GET /` and `GET /{id}` see. A count route applies the client's
filters to the scoped select:

```python
@fr.get("/count")
async def total(self, query_params) -> int:
    await self.authorize(fr.Action.GET_MANY)
    query = fr.apply_clauses(sa.select(Task), fr.resolve_scope(self))
    return await self.count(self.apply_query_params(query, query_params))
```

Do not name that route method `count`: it would shadow the
{meth}`count <fastapi_restly.views.RestView.count>` seam it calls.

Pass another view to follow that view's visibility from a route on this
one, which is how a nested listing stays in step with the child's own
endpoint:

```python
@fr.get("/{id}/tasks", response_model=list[TaskRead])
async def list_tasks(self, id: int):
    await self.handle_get_one(id)
    query = fr.apply_clauses(
        sa.select(Task).where(Task.project_id == id), fr.resolve_scope(TaskView)
    )
    return list(await self.session.scalars(query))
```

Pass a mapped model class, `fr.resolve_scope(Task)`, for the model rung
alone: the `default_scope` every reference check applies and every view
without its own scope inherits. The two answers differ wherever a view
declares a scope, so code off the request path that wants what the API
shows passes the view.

The result is a clause or `fr.clauses.UNSCOPED`, never `None`, so it
drops into `fr.apply_clauses` or a composition without a branch. The
function resolves the scope; applying it stays with the framework.

(reference-scopes)=
## References: overriding per field

A reference field checks against the target model's `default_scope` by
default. {class}`fr.RefExists <fastapi_restly.schemas.RefExists>` names
the target explicitly and carries the per-field override in its `scope`
argument, with three states:

```python
class OrderCreate(fr.BaseSchema):
    item_id: fr.MustExist[int]                # default_scope of Item (FK-inferred)

    restore_id: Annotated[int, fr.RefExists(Item, scope=ItemClauses.trashed)]

    audit_item_id: Annotated[int, fr.RefExists(Item, scope=fr.clauses.UNSCOPED)]
```

- **Not given**: the target's `default_scope` applies.
- **`scope=<WhereClause>`**: that predicate applies instead; the restore
  endpoint above accepts exactly the ids the trash view shows.
- **`scope=fr.clauses.UNSCOPED`**: the check is explicitly unscoped, in
  the same loud spelling as everywhere else. `scope=None` is rejected, so
  a variable that happens to be `None` can never silently unscope the
  check or escape the grep.

`IDRef` / `IDSchema` relationship references always use the target's
`default_scope`; the per-field override exists on the scalar marker
only.

## Coming from Rails

This is `default_scope` without the parts that earned Rails'
`default_scope` its reputation. `default_scope` controls which rows are
visible to reads and reference checks. It does not set column values
when creating rows. A view escapes it
by declaring a replacement or `fr.clauses.UNSCOPED`, both visible in the
class body, and the reference escape is the same greppable word. Nothing
escapes it implicitly.

(migrating-build-query)=
## Migrating from `build_query`

Restly's earlier read seam, overriding `build_query()`, is removed: a view
class that still defines one, itself or through a mixin, fails at class
definition with a pointer here. Nothing it did is lost, and the mechanics
differ in one point: `build_query` overrides composed through `super()`
chains, scopes replace, so a chain of filters becomes one composed clause:

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

Relationship loading that lived in `build_query` belongs in
{meth}`get_relationship_loader_options <fastapi_restly.views.BaseRestView.get_relationship_loader_options>`.
Other `Select` changes to a listing, such as a join or a default
ordering, belong in an
{meth}`apply_query_params <fastapi_restly.views.RestView.apply_query_params>`
override:

```python
class ItemView(fr.AsyncRestView):
    ...

    def apply_query_params(self, query, query_params):
        query = query.order_by(Item.created_at.desc())
        return super().apply_query_params(query, query_params)
```

An ordering added before `super()` comes first, so a client `?sort=`
orders rows within it. A to-many join added here does not repeat rows or
inflate `total_count`.

(binding-scope-values)=
## Binding scope values

Scopes are clauses, so their values come from context members:
[Query Clauses, Supplying values](#binding-values). Bind request-wide
values with a generated dependency, which
[Current context](#current-request-binding) describes:

```python
app = FastAPI(dependencies=[
    Current.depends(user_id=get_user_id),
    RoleContext.depends(include_deleted=get_include_deleted),
])
```

Read `Current.explain()` when a query filters unexpectedly; the origin
names your `depends()` line. The scope's promise is structural: the
framework guarantees the clause is applied and its values are bound, or
the request fails loudly. That the bound value is the *right* user is the
source dependency's job; assert it there.
