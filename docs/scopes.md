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
current_tenant = fr.context_param("tenant_id", UUID)


class ItemClauses(fr.ClauseNamespace):
    model = Item

    is_deleted = fr.where_clause(Item.deleted_at.is_not(None))
    owned_by_tenant = fr.where_clause(Item.tenant_id == current_tenant)
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

A model without a `default_scope` behaves as before: unscoped reads,
bare primary-key reference checks. The feature is opt-in per model.

Keep `default_scope` a pure predicate: a
{class}`WhereClause <fastapi_restly.clauses.WhereClause>`, with EXISTS
(`.any()`/`.has()`) instead of joins. Reference checks apply only the
predicate half of the clause (a dropped ordering is harmless, but a
predicate that needs a dropped join is rejected loudly), and a
visibility rule that multiplies rows would distort every list it
guards.

The tenant value is bound per request, in a dependency shared by the
whole app; see [Binding scope values](#binding-scope-values):

```python
async def bind_tenant(user: CurrentUserDep):
    with current_tenant.bind(tenant_id=user.tenant_id):
        yield
```

An unbound scope raises at request time, naming the missing value: a
scoped model can not be read silently unfiltered.

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

Declaring a scope **replaces** the default, it does not stack on it.
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

For a per-request *choice* of clause, override
{meth}`get_scope <fastapi_restly.views.BaseRestView.get_scope>`, whose
default reads the attribute and falls back to the model's
`default_scope`:

```python
class AdminItemView(fr.AsyncRestView):
    ...
    def get_scope(self):
        if self.requester_is_admin():
            return None             # None reads unscoped
        return super().get_scope()
```

The hook chooses which clause applies; a *value* that varies per request
(the tenant id) is bound, not chosen. The framework applies the chosen
clause itself, on every read; there is no apply-side override point.

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

    audit_item_id: Annotated[int, fr.RefExists(Item, scope=None)]
```

- **Not given**: the target's `default_scope` applies.
- **`scope=<Clause>`**: that clause applies instead; the restore
  endpoint above accepts exactly the ids the trash view shows.
- **`scope=None`**: the check is explicitly unscoped, and
  `grep -r "scope=None"` hands a security review every exception in one
  command.

`IDRef` / `IDSchema` relationship references always use the target's
`default_scope`; the per-field override exists on the scalar marker
only.

## Coming from Rails

This is `default_scope` without the parts that earned Rails'
`default_scope` its reputation. It does not default attribute *values*
on create; it only filters reads and reference checks. A view escapes it
by declaring a replacement or returning `None` from `get_scope()`, both
visible in the class body, and the reference escape is the greppable
`scope=None`. Nothing escapes it implicitly.

(migrating-build-query)=
## Migrating from `build_query`

Overriding {meth}`build_query <fastapi_restly.views.RestView.build_query>`
for visibility is deprecated in favor of scopes. The mechanics differ in
one point: `build_query` overrides compose through `super()` chains,
scopes replace. A `build_query` override keeps working, and the scope is
applied on top of whatever it returns, so migration can proceed one rule
at a time:

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
[Query Clauses, Binding values](#binding-values): bind in a
FastAPI dependency for request-wide values, and read
`Clause.explain()` when a query filters unexpectedly. The scope's
promise is structural: the framework guarantees the clause is applied
and its values are bound, or the request fails loudly. That the bound
value is the *right* tenant is the dependency's job; assert it there.
