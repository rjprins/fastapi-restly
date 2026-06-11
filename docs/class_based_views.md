# Class-Based Views

Class-based views are the core of FastAPI-Restly. They make REST scaffolding
subclassable, keep shared behavior in one place, and let you override one method
without rewriting the route.

## What is a class-based view?

In plain FastAPI, an endpoint is a function:

```python
@app.get("/users")
async def list_users(session: AsyncSession = Depends(get_session)):
    ...

@app.post("/users")
async def create_user(payload: UserCreate, session: AsyncSession = Depends(get_session)):
    ...
```

A class-based view (CBV) groups related endpoints on a class instead:

```python
import fastapi_restly as fr
from fastapi import Depends
from sqlalchemy import select

@fr.include_view(app)
class UserView(fr.View):
    prefix = "/users"
    dependencies = [Depends(require_logged_in)]
    session: fr.AsyncSessionDep

    @fr.get("")
    async def list_users(self) -> list[UserRead]:
        users = await self.session.scalars(select(User))
        return [UserRead.model_validate(user) for user in users]

    @fr.post("")
    async def create_user(self, payload: UserCreate) -> UserRead:
        user = User(**payload.model_dump())
        self.session.add(user)
        await self.session.flush()
        return UserRead.model_validate(user)
```

Declare dependencies, prefix, tags, and metadata once on the class. The
`session` attribute is a FastAPI dependency too, injected per request and
available as `self.session`. Methods are ordinary Python methods, so helpers,
class config, and `self` all work normally.

## Why CBVs at all?

Function endpoints are fine for a few routes. They get repetitive in larger
codebases:

- **Repetition.** The same `Depends(get_session)`, the same auth dependency,
  the same response config — all duplicated across every related endpoint.
- **Scattering.** Endpoints that conceptually belong together (everything
  about users, everything about invoices) live as separate top-level
  functions. Renames, splits, and shared edits become tedious.
- **No natural place for shared state.** A request scope often has a few
  values that every endpoint in a group needs (the current user, a tenant
  context, a serialised filter). With functions, you pass them through
  parameters or recompute them. With a CBV, they're attributes on `self`.

A CBV solves all three with one tool: the class itself.

## The FastAPI-Restly model

```python
class View:
    prefix: ClassVar[str]
    tags: ClassVar[Iterable[str] | None] = None
    dependencies: ClassVar[Iterable[Any] | None] = None
    responses: ClassVar[dict[int, Any]] = {}

    @classmethod
    def before_include_view(cls): ...
```

That is the base class: FastAPI router metadata plus one pre-registration hook.
Methods on a `View` subclass use `@fr.get(...)`, `@fr.post(...)`, or
`@fr.route(...)`. Those decorators only store route metadata; registration
happens when you call:

```python
fr.include_view(app, UserView)
```

For larger apps, define classes in view modules and include them from the
app/router composition layer. Small apps can use the decorator shortcut:

```python
@fr.include_view(app)
class UserView(fr.View): ...
```

`include_view` walks the class's MRO, collects every method tagged with route
metadata, instantiates a per-request copy of the view, and registers each
route on the parent router or app.

Routes are bound at *include-time* against the class you pass in. They are not
bound at decoration time. This is what makes subclassing work.

## True subclassing

The naive way to add CBV support to FastAPI is a class decorator that mutates
the class on definition:

```python
@cbv(router)
class UserView:
    @router.get("/users")
    async def list_users(self): ...
```

That works for a single class. It falls apart the moment you try to subclass:

- Routes are registered on `router` against `UserView`. Override
  `list_users` on a subclass — the registered handler still calls the
  original.
- Re-decorate the subclass with `@cbv(router)` and you get duplicate routes.
- Decorate the subclass on a *different* router and only the subclass's
  directly-decorated methods register; the parent's routes don't follow.

FastAPI-Restly avoids this by deferring registration:

```python
class AdminUserView(UserView):
    async def list_users(self):
        # filter to soft-deleted users
        ...

fr.include_view(admin_app, AdminUserView)
```

When `include_view` runs, it walks `AdminUserView.__mro__`, finds inherited
route metadata, and registers handlers against `AdminUserView`. Your override
runs. The same view can be included on multiple routers.

That is what "true class-based views" means in this framework. You can:

- Define an abstract parent that supplies handlers but is never registered.
- Subclass a working view to specialise it for a different prefix, a
  different role, or a different audience.
- Mix in behaviour through multiple inheritance — see the
  [share-behaviour guide](howto_inheritance.md).

## The view hierarchy

```
View                   ← class-based view primitive (no CRUD)
└── BaseRestView       ← CRUD configuration + helpers (no endpoints)
    ├── RestView         ← sync CRUD endpoints
    │   └── ReactAdminView      ← + ra-data-simple-rest contract
    └── AsyncRestView    ← async CRUD endpoints
        └── AsyncReactAdminView ← + ra-data-simple-rest contract
```

- `View` is the bare CBV primitive. Use it for non-CRUD endpoints: auth flows,
  custom RPC, file uploads, or composite-key resources.
- `BaseRestView` extends `View` with `model`, `schema`, the auto-generated
  create/update schemas (`schema_create` / `schema_update`), query-modifier
  configuration, and helper methods like `to_response()` and
  `to_response_schema()`. The concrete CRUD methods live on `RestView` /
  `AsyncRestView`; `BaseRestView` is an abstract scaffold with no endpoints of
  its own.
- `RestView` and `AsyncRestView` provide the concrete sync and async
  implementations of the CRUD endpoints. **One of these is what you usually
  subclass.** They assume a single scalar resource id for the generated
  `/{id}` routes; composite primary keys are not supported by the default
  CRUD view contract. For legacy tables with composite keys, subclass `View`
  directly and define routes that match your API shape.

The public method surface is classified in the
[API reference](api_reference.md#view-method-surface). Each CRUD verb is split
into three tiers: `<verb>_endpoint` (HTTP contract), `handle_<verb>`
(authorization + commit bracket), and `<verb>` (domain operation). Cross-cutting
override points include `build_query`, `authorize`, hooks, and `to_response`.

## A complete example: shared base view

A common pattern: every view in your app needs auth, tenant scoping, and a
common error envelope. Express that once and inherit:

```python
import fastapi_restly as fr
from fastapi import Depends

async def require_logged_in(user_id: int = Depends(get_current_user_id)) -> int:
    return user_id

class TenantScopedView(fr.AsyncRestView):
    """Internal base — never registered directly."""
    dependencies = [Depends(require_logged_in)]

    def build_query(self):
        # automatic tenant filtering for every read — list, pagination
        # total, AND single-row retrieve all route through this method.
        return super().build_query().where(
            self.model.tenant_id == self.request.state.tenant_id
        )


@fr.include_view(app)
class InvoiceView(TenantScopedView):
    prefix = "/invoices"
    model = Invoice
    schema = InvoiceRead


@fr.include_view(app)
class CustomerView(TenantScopedView):
    prefix = "/customers"
    model = Customer
    schema = CustomerRead
```

Two views, one shared dependency, one shared filter. Add a new tenant-scoped
resource and it inherits the same auth + scoping behavior. For soft delete,
audit stamps, and permission scoping, see
[Composing views with mixins](howto_compose_views_with_mixins.md).

## Override a single tier

`AsyncRestView` and `RestView` split every CRUD verb into three tiers — the
route shell (wire contract), the request handler (authorization + commit
bracket), and the business method (domain logic, auth-free and commit-free).
One behavior change therefore means one method override, while routing,
authorization, and the commit stay framework-owned. The model, both request
lifecycles, and the override decision table live in
[How Overrides Work: The Three Tiers](the_handle_design.md); task-shaped
recipes in [Override CRUD Behavior](howto_override_endpoints.md).

## Dependency injection on class attributes

A class attribute on a view is wired as a FastAPI dependency only when its
annotation either:

- carries an `Annotated[..., Depends(...)]` marker, or
- names one of FastAPI's bare-injectable special types (`Request`,
  `Response`, `BackgroundTasks`, `WebSocket`).

This matches FastAPI function parameters. A plain annotation like
`model: type[Foo]` is only a type hint.

```python
from fastapi import Request
from typing import Annotated

class UserView(fr.AsyncRestView):
    # Wired: AsyncSessionDep is Annotated[AsyncSession, Depends(...)].
    session: fr.AsyncSessionDep

    # Wired: Request is one of FastAPI's bare-injectable specials.
    request: Request

    # Wired: explicit Depends marker.
    current_user: Annotated[User, Depends(get_current_user)]

    # NOT wired: plain annotation, just a type hint.
    model: type[User]
```

The shipped `AsyncSessionDep` / `SessionDep` aliases carry `Depends`, so
they keep working unchanged. The `request` attribute on `BaseRestView`
relies on the special-type rule. Any custom dependency declared on a view
class must use the `Annotated[X, Depends(...)]` form unless it's one of
the bare-injectable types.

This makes mixins safe: a mixin can declare what it expects from its host
without shadowing the host's wiring. See
[Composing views with mixins](howto_compose_views_with_mixins.md) for the
mixin pattern.

## When to use `View` directly

`View` is the right tool when your endpoints don't fit a CRUD shape:

```python
@fr.include_view(app)
class AuthView(fr.View):
    prefix = "/auth"
    tags = ["auth"]

    @fr.post("/login")
    async def login(self, credentials: LoginRequest) -> Token:
        ...

    @fr.post("/refresh")
    async def refresh(self, token: str) -> Token:
        ...

    @fr.post("/logout")
    async def logout(self) -> None:
        ...
```

Three related endpoints, one shared prefix and tag, one place for auth
dependencies. No model, no schema, no CRUD.

## When *not* to use a CBV

If you have a single one-off endpoint that does not share anything with
others, write a plain function endpoint. CBVs pay off when you have shared
metadata, shared dependencies, or related endpoints that benefit from being
co-located. Don't reach for them just for the sake of structure.

## Cross-references

- [How Overrides Work: The Three Tiers](the_handle_design.md) — the tier
  model behind every CRUD verb, both request lifecycles, and the override
  decision table.
- [Override Endpoints](howto_override_endpoints.md) — every tier on
  `AsyncRestView` / `RestView`, with call-chain diagrams.
- [Share Behaviour with Base Views](howto_inheritance.md) — patterns for
  multi-tenant scoping, role-based filtering, and shared mixins.
- [API Reference](api_reference.md) — full `View`, `BaseRestView`,
  `RestView`, `AsyncRestView` signatures and class attributes.
