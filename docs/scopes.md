# Scopes

A scope is the clause the server imposes on every read of a model and on
every reference to it: the row-visibility rule. Scope is a *role* a
clause plays, not a kind of clause; the material is a plain
[query clause](clauses.md). The word completes a three-way vocabulary:

| Word | Who decides | What it is |
|---|---|---|
| **clause** | the declaration | a named, reusable query fragment: `Item.C.visible` |
| **scope** | the server | the clause every read and every reference check applies |
| **filter** | the client | what the request asks for through [query parameters](howto_query_modifiers.md) |

A filter narrows within the scope; it can never widen past it.

The examples below extend the models and the `ItemClauses` namespace from
[Query Clauses](clauses.md), with a `tenant_id` bound per request:

```python
class Current(fr.ContextNamespace):
    tenant_id: fr.ContextParam[UUID]


class ItemClauses(fr.ClauseNamespace):
    model = Item

    is_deleted = fr.where_clause(Item.deleted_at.is_not(None))
    owned_by_tenant = fr.where_clause(Item.tenant_id == Current.tenant_id)
    visible = fr.all_of(owned_by_tenant, fr.none_of(is_deleted))
    trashed = fr.all_of(owned_by_tenant, is_deleted)

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
  retrieve, so a cross-tenant `PATCH` is a 404, not a write.
- **Every reference to the model.** A
  {class}`fr.MustExist <fastapi_restly.schemas.MustExist>` /
  {class}`fr.IDRef <fastapi_restly.schemas.IDRef>` /
  {class}`fr.IDSchema <fastapi_restly.schemas.IDSchema>` field targeting
  the model checks existence inside the scope, so referencing another
  tenant's row on a write is "does not exist" (404), which closes the
  reference-IDOR hole without leaking that the id exists.

Every model namespace has a `default_scope`, and its default is
`fr.clauses.UNSCOPED`: a model that declares none reads unscoped, and
its reference checks are bare primary-key lookups. `UNSCOPED` is the one
explicit unscoped spelling everywhere a scope can appear, so
`grep -rn UNSCOPED` hands a security review every escape, view,
namespace, and reference alike, in one command.

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

A namespace declared on a model *subclass* shadows the base model's
namespace. If the base declares a `default_scope`, the subclass
namespace must say what happens: reuse it (`default_scope =
BaseClauses.default_scope`), declare its own, or opt out with
`default_scope = fr.clauses.UNSCOPED`. Silence is an error at
definition, not a silent unscope, and so is `default_scope = None`,
which says nothing.

The tenant value is bound per request, by a generated dependency shared
by the whole app; see [Binding scope values](#binding-scope-values):

```python
app = FastAPI(dependencies=[Current.depends(tenant_id=get_tenant_id)])
```

An unbound scope raises at request time, naming the missing value: a
scoped model cannot be read silently unfiltered.

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
    # no scope declared: reads apply Item.C.default_scope


@fr.include_view(app)
class TrashView(fr.AsyncRestView):
    prefix = "/trash"
    model = Item
    schema = ItemRead
    scope = Item.C.trashed          # the deviating view declares
```

Declaring a scope **replaces** the default; it does not stack on it.
`Item.C.trashed` is safe as a whole scope because it is composed from
the same `owned_by_tenant` leaf that `visible` contains; build every
scope from the namespace's leaves and the tenant rule cannot fall out
of a view by omission.

A view scope is the query basis of its endpoints, so transforms are
welcome there, unlike in `default_scope`:

```python
class ItemView(fr.AsyncRestView):
    ...
    scope = fr.combine(Item.C.visible, Item.C.newest_first)
```

The explicit opt-out is `fr.clauses.UNSCOPED`: it reads past the model's
`default_scope`, where `None` would fall back to it. Reserve it for a
genuinely all-seeing view behind its own authorization; an "admin"
view usually stays tenant-bound and widens (`scope =
Item.C.owned_by_tenant` sees the trash too):

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
class RoleContext(fr.ContextNamespace):
    is_admin: fr.ContextParam[bool]

@fr.where_clause
def role_visibility(admin: Annotated[bool, RoleContext.is_admin]) -> ColumnElement[bool]:
    return Item.C.owned_by_tenant() if admin else Item.C.visible()

class ItemView(fr.AsyncRestView):
    ...
    scope = role_visibility         # a dependency binds RoleContext.is_admin
```

A policy clause like this names a request-boundary value, so it lives
beside the view that applies it, not in the model's namespace; the
namespace holds model facts (`is_deleted`, `owned_by_tenant`), views
compose policy from them. The framework applies the declared clause
itself, on every read; there is no apply-side override point, and a
non-Clause `scope` is rejected as the view class is defined.

(reference-scopes)=
## References: overriding per field

A reference field checks against the target model's `default_scope` by
default. {class}`fr.RefExists <fastapi_restly.schemas.RefExists>` names
the target explicitly and carries the per-field override in its `scope`
argument, with three states:

```python
class OrderCreate(fr.BaseSchema):
    item_id: fr.MustExist[int]                # default_scope of Item (FK-inferred)

    restore_id: Annotated[int, fr.RefExists(Item, scope=Item.C.trashed)]

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
        user = self.request.state.user
        return super().build_query().where(Item.tenant_id == user.tenant_id)

# after: the rule becomes a clause on the model, every view and every
# reference to Item inherits it, and the override disappears
class ItemClauses(fr.ClauseNamespace):
    model = Item
    default_scope = fr.where_clause(Item.tenant_id == current_tenant)
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
app = FastAPI(dependencies=[
    Current.depends(tenant_id=get_tenant_id, is_admin=get_is_admin),
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
