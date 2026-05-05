# Class-Based Views

Class-based views are the heart of FastAPI-Restly. They are what makes the
framework's CRUD scaffolding feel idiomatic, what makes shared behaviour easy
to express, and what makes the call to "just override one method" actually
work. Before diving into models or schemas, it is worth understanding why this
piece exists and what it gives you.

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

@fr.include_view(app)
class UserView(fr.View):
    prefix = "/users"
    dependencies = [Depends(require_logged_in)]

    @fr.get("")
    async def list_users(self):
        ...

    @fr.post("")
    async def create_user(self, payload: UserCreate):
        ...
```

The dependencies, the prefix, the tags, and any other metadata are declared
once on the class. The methods are still ordinary Python methods — you can
share helpers between them, store config on the class, and reach for `self`
without ceremony.

## Why CBVs at all?

Function endpoints are perfectly fine for a handful of routes. They start to
hurt once you have a real codebase:

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

That is the entire base class. It carries the metadata that maps to FastAPI's
`APIRouter` arguments and a single hook that fires right before the view's
routes get registered. Methods on a `View` subclass are decorated with
`@fr.get(...)`, `@fr.post(...)`, `@fr.route(...)` — these are *neutral
markers*; they only stash route metadata on the method. Nothing is registered
until you call:

```python
fr.include_view(app, UserView)
```

This direct form is the recommended architecture for larger apps: view modules
define classes, and the app/router composition layer decides where to mount
them. Small apps can use the decorator shortcut when import-time registration
is acceptable:

```python
@fr.include_view(app)
class UserView(fr.View): ...
```

`include_view` walks the class's MRO, collects every method tagged with route
metadata, instantiates a per-request copy of the view, and registers each
route on the parent router or app.

This is the single most important design choice in the framework. Routes are
bound at *include-time*, against the class you actually pass in, not at
*decoration-time* against whatever class happened to be decorated. That is
what makes everything else work.

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

When `include_view` runs, it walks `AdminUserView.__mro__`, sees the inherited
`list_users` route metadata from `UserView`, and registers the handler — but
the handler resolves through `AdminUserView`'s method dictionary, so your
override is what actually runs. The same view can be included on multiple
routers; each include creates its own routes against the subclass you passed
in.

That is what "true class-based views" means in this framework. You can:

- Define an abstract parent that supplies handlers but is never registered
  itself (this is exactly what `BaseRestView` is).
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

- `View` is the bare CBV primitive. Use it for non-CRUD endpoints — auth
  flows, custom RPC, file uploads, anything that does not fit the
  listing/retrieve/create/update/destroy shape.
- `BaseRestView` extends `View` with `model`, `schema`, the auto-generated
  create/update schemas, query-modifier configuration, and helper methods
  like `to_response_schema()`. It declares route methods (`listing`, `retrieve`,
  `create`, `update`, `destroy`) but provides no implementations — it is an
  abstract scaffold.
- `RestView` and `AsyncRestView` provide the concrete sync and async
  implementations of the CRUD endpoints. **One of these is what you usually
  subclass.**

The public method surface is classified in the
[API reference](api_reference.md#view-method-surface): route methods define the
HTTP contract, `handle_*` methods are override hooks, and object/query helpers
are public utilities for handlers and custom routes.

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

    def build_listing_query(self):
        # automatic tenant filtering for every list — and every pagination total,
        # because count_listing consults the same seam.
        return super().build_listing_query().where(
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
resource and you get the auth + scoping behaviour for free. For more
elaborate compositions — soft delete, audit stamps, permission scoping
layered together — see
[Composing views with mixins](howto_compose_views_with_mixins.md).

## Override a single method

`AsyncRestView` and `RestView` are designed so you can replace any one piece
without touching the rest. Override the handler (`handle_listing`, `handle_retrieve`,
`handle_create`, `handle_update`, `handle_destroy`) for business-logic changes that should
fire on both the generated route and any custom callers; override the
endpoint method itself (`listing`, `retrieve`, `create`, `update`, `destroy`) when you
want full control of the HTTP layer.

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserRead

    async def handle_create(self, schema_obj: UserCreate) -> User:
        # Compose the create flow yourself so the password hash is written
        # *before* save_object flushes. Calling super().handle_create() and
        # mutating after would lose the change — the row is already saved.
        user = await self.make_new_object(schema_obj)
        user.password_hash = hash_password(schema_obj.password)
        return await self.save_object(user)
```

When the derivation should fire on every insert regardless of which view
created the row (audit stamps, slug derivation, denormalised counters),
prefer a SQLAlchemy `before_insert` mapper event listener instead:

```python
from sqlalchemy import event

@event.listens_for(Article, "before_insert")
def _set_slug(mapper, connection, target):
    target.slug = slugify(target.title)
```

See SQLAlchemy's [mapper events
documentation](https://docs.sqlalchemy.org/en/20/orm/events.html#mapper-events)
for the full event API.

Everything else — listing, retrieval, update, delete, schema generation,
pagination — keeps working unchanged. See
[Override Endpoints](howto_override_endpoints.md) for the full list of handlers
and the call chain.

## Dependency injection on class attributes

A class attribute on a view is wired as a FastAPI dependency only when its
annotation either:

- carries an `Annotated[..., Depends(...)]` marker, or
- names one of FastAPI's bare-injectable special types (`Request`,
  `Response`, `BackgroundTasks`, `WebSocket`).

This matches the rule FastAPI itself applies to function parameters. A
plain annotation like `model: type[Foo]` is *not* wired — it's just a
type hint, safe to add for documentation or static-checker purposes.

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

This rule is what makes mixins safe to write: a mixin can declare what it
requires from its host class (`session`, `request`, helper methods)
without accidentally shadowing the host's wiring. See
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

Three related endpoints, one shared prefix and tag, one place to add
auth-flow-specific dependencies. No model, no schema, no CRUD — `View`
gives you exactly what you need and nothing else.

## When *not* to use a CBV

If you have a single one-off endpoint that does not share anything with
others, write a plain function endpoint. CBVs pay off when you have shared
metadata, shared dependencies, or related endpoints that benefit from being
co-located. Don't reach for them just for the sake of structure.

## Cross-references

- [Override Endpoints](howto_override_endpoints.md) — every handler on
  `AsyncRestView` / `RestView`, with call-chain diagrams.
- [Share Behaviour with Base Views](howto_inheritance.md) — patterns for
  multi-tenant scoping, role-based filtering, and shared mixins.
- [API Reference](api_reference.md) — full `View`, `BaseRestView`,
  `RestView`, `AsyncRestView` signatures and class attributes.
