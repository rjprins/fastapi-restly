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
`fr.clauses.UNSCOPED`: a model that declares none reads unscoped, and
its reference checks are bare primary-key lookups. `UNSCOPED` is the one
explicit unscoped spelling everywhere a scope can appear. Searching for
`UNSCOPED` finds where unscoping is requested directly. Follow variables
and composed clauses to find the views and references that receive it.
Composition can absorb or propagate the sentinel, so the search does
not identify every resulting unscoped read.

In clause composition, `UNSCOPED` means accepting every row: it is a
no-op in `all_of`, makes `any_of` unscoped, and makes `none_of` match
no rows. It contributes nothing to `combine` or `apply_clauses` and
does not remove other filters on a statement. See the
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

`default_scope` must be a
{class}`WhereClause <fastapi_restly.clauses.WhereClause>`: a pure
predicate, with EXISTS (`.any()`/`.has()`) instead of joins. The rule is
enforced, in the declared attribute type and at class definition,
because a reference check is an existence probe that cannot honor a
transform: a scope carrying one would filter view reads while reference
checks could not see it. Ordering and other reshaping belong on the
[view scope](#view-scope), which takes any clause.

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
`SELECT` that touches the class: view reads under any scope, reference
checks, lazy loads and hand-written selects alike. Restly's reads and
reference checks are ORM statements, so the rule reaches them. The
[tenant row scoping](#tenant-row-scoping) recipe shows the listener and the
column it restricts. The [SaaS example](examples.md#saas) runs that recipe.

A view scope is the query basis of its endpoints, so transforms are
welcome there, unlike in `default_scope`:

```python
class ItemView(fr.AsyncRestView):
    ...
    scope = fr.combine(ItemClauses.visible, newest_first)
```

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
bound value, which fails loudly when the value is missing and shows
its decision in `explain()`:

```python
from typing import Annotated

from sqlalchemy import ColumnElement, true


class RoleContext(fr.ContextNamespace):
    include_deleted: fr.ContextParam[bool]

@fr.where_clause
def role_visibility(
    include_deleted: Annotated[bool, RoleContext.include_deleted],
) -> ColumnElement[bool]:
    return true() if include_deleted else Item.deleted_at.is_(None)

class ItemView(fr.AsyncRestView):
    ...
    scope = role_visibility  # a dependency binds RoleContext.include_deleted
```

A policy clause like this names a request-boundary value, so it lives
beside the view that applies it, not in the model's namespace; the
namespace holds model facts such as `is_deleted`, and views compose policy
from them. The framework applies the declared clause
itself, on every read; there is no apply-side override point, and a
non-Clause `scope` is rejected as the view class is defined.
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
  endpoint above accepts exactly the ids the trash view shows. A
  `WhereClause`, like `default_scope` and for the same reason: an
  existence probe cannot honor a transform.
- **`scope=fr.clauses.UNSCOPED`**: the check is explicitly unscoped, in
  the same loud spelling as everywhere else. `scope=None` is rejected, so
  a variable that happens to be `None` can never silently unscope the
  check or escape the grep.

`IDRef` / `IDSchema` relationship references always use the target's
`default_scope`; the per-field override exists on the scalar marker
only.

## Coming from Rails

This is `default_scope` without the parts that earned Rails'
`default_scope` its reputation. It does not default attribute *values*
on create; it only filters reads and reference checks. A view escapes it
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

Read-wide eager loading and other non-visibility `Select` changes that
lived in `build_query` belong in a transform clause on the view scope,
or in
{meth}`get_relationship_loader_options <fastapi_restly.views.BaseRestView.get_relationship_loader_options>`
for relationship loading.

(binding-scope-values)=
## Binding scope values

Scopes are clauses, so binding works as described in
[Query Clauses, Binding values](#binding-values). For request-wide
values, {meth}`ContextNamespace.depends <fastapi_restly.clauses.ContextNamespace.depends>`
generates the dependency: async underneath, so the bind lands in the
request task, where async and `def` endpoints alike read it, and fed by
your own dependencies, so `app.dependency_overrides` keeps working:

```python
class Current(fr.ContextNamespace):
    tenant_id: fr.ContextParam[UUID]


app = FastAPI(dependencies=[
    Current.depends(tenant_id=get_tenant_id),
    RoleContext.depends(include_deleted=get_include_deleted),
])
```

Attach it at the narrowest level that needs it: the app for values in
every request, a router for a group, a view's `dependencies` list for
one view;
{meth}`ContextParam.depends <fastapi_restly.clauses.ContextParam.depends>`
is the single-slot form. Read `Current.explain()` or
`Clause.explain()` when a query filters unexpectedly; the origin names
your `depends()` line. The scope's promise is structural: the framework
guarantees the clause is applied and its values are bound, or the
request fails loudly. That the bound value is the *right* tenant is the
source dependency's job; assert it there.
