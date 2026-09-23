# Views & CRUD

Views group related FastAPI endpoints in Python classes. A view declares shared
route configuration and dependencies once. Subclasses inherit or override its
endpoint methods.

## Choose a view

| You are building | Use |
|---|---|
| One simple standalone endpoint | A plain FastAPI route, with no Restly view |
| A group of related non-CRUD endpoints: login/auth flows, webhook receivers, RPC-style actions, composite-key resources | {class}`fr.View <fastapi_restly.views.View>` ([when to use `View` directly](#when-to-use-view-directly)) |
| A database-backed CRUD resource | {class}`fr.AsyncRestView <fastapi_restly.views.AsyncRestView>` / {class}`fr.RestView <fastapi_restly.views.RestView>` |
| CRUD plus custom actions such as publish, vote, or bulk operations | {class}`fr.AsyncRestView <fastapi_restly.views.AsyncRestView>` or {class}`fr.RestView <fastapi_restly.views.RestView>` with extra {func}`@fr.get <fastapi_restly.views.get>` or {func}`@fr.post <fastapi_restly.views.post>` methods ([custom actions](customize.md#add-a-custom-action-route)) |

Use {class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` for new
applications unless the application already uses synchronous SQLAlchemy. [Using
RestView](rest_views.md) defines its CRUD routes, schemas, and configuration.
[Customizing RestView](customize.md) shows how to override those routes or add
custom actions. All three classes use the same registration, dependency
injection, and inheritance mechanics.

(what-is-a-class-based-view)=
## How class-based views work

A class-based view (CBV) is a class that groups related endpoints together
with the configuration they share. In plain FastAPI, an endpoint is a
function:

```python
@app.get("/users")
async def list_users(session: AsyncSession = Depends(get_session)):
    ...

@app.post("/users")
async def create_user(payload: UserCreate, session: AsyncSession = Depends(get_session)):
    ...
```

A class-based view declares the same endpoints as methods on a class instead:

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

Dependencies, prefix, tags, and metadata are declared once on the class. The
`session` attribute is a FastAPI dependency too, injected per request and
available as `self.session`. Methods are ordinary Python methods, so helpers,
class config, and `self` all work normally.

## Why CBVs at all?

Function endpoints are fine for a few routes, but they become repetitive in
larger codebases:

- Repetition: the same `Depends(get_session)`, the same auth dependency, and
  the same response config are duplicated across every related endpoint.
- Scattering: endpoints that conceptually belong together (everything about
  users, everything about invoices) live as separate top-level functions, so
  renames, splits, and shared edits become tedious.
- No natural place for shared state: a request scope often has a few values
  that every endpoint in a group needs (the current user, a tenant context, a
  serialised filter). With functions, you pass them through parameters or
  recompute them; with a CBV, they are attributes on `self`.

A CBV solves all three with one tool: the class itself.

## Declaration and registration

The base class itself is small: FastAPI router metadata plus one
pre-registration hook.

```python
class View:
    prefix: ClassVar[str]
    tags: ClassVar[Iterable[str | Enum] | None] = None
    dependencies: ClassVar[Any] = None
    responses: ClassVar[dict[int | str, dict[str, Any]]] = {}

    @classmethod
    def before_include_view(cls): ...
```

Methods on a {class}`View <fastapi_restly.views.View>` subclass use {func}`@fr.get(...) <fastapi_restly.views.get>`, {func}`@fr.post(...) <fastapi_restly.views.post>`, or
{func}`@fr.route(...) <fastapi_restly.views.route>`. Those decorators only store route metadata; registration
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

{func}`include_view <fastapi_restly.views.include_view>` walks the class's MRO, collects every method tagged with route
metadata, wires each method's `self` up as a dependency on the view class (so
FastAPI instantiates a fresh view per request), and registers each route on
the parent router or app.

Routes are bound at *include-time* against the class you pass in; they are
not bound at decoration time. This is what makes subclassing work.

(app-wide-base-view)=

## One base view for the whole app

An application-wide base view declares your app's request context once on a
bare {class}`View <fastapi_restly.views.View>`. Subclass it everywhere. No CRUD
is required:

```python
from typing import Annotated
from fastapi import Depends


class AppView(fr.View):
    """Project base; every endpoint group in the app subclasses this."""

    session: fr.AsyncSessionDep
    current_user: Annotated[User, Depends(get_current_user)]


@fr.include_view(app)
class ProfileView(AppView):
    prefix = "/profile"

    @fr.get("/")
    async def whoami(self) -> dict:
        return {"user": self.current_user.name}


@fr.include_view(app)
class BillingView(AppView):
    prefix = "/billing"

    @fr.post("/checkout")
    async def checkout(self, payload: CheckoutRequest):
        order = Order(user_id=self.current_user.id, **payload.model_dump())
        self.session.add(order)
        await self.session.commit()
        return {"order_id": order.id}
```

In plain FastAPI, `session` and `current_user` would be `Depends` parameters
re-declared on every function in the project. Here they are declared once and
read from `self` in every method of every subclass. The same base also
composes under CRUD views, so the whole app shares one context layer:

```python
class AppRestView(AppView, fr.AsyncRestView):
    """CRUD resources get the same session + current_user attributes."""
```

Testing inherits the benefit: FastAPI's `dependency_overrides` applies to the
class-level dependencies, so overriding `get_current_user` reaches
`self.current_user` in every view at once.

(the-view-hierarchy)=
## The view hierarchy

The CRUD rows of the opening table are served by a short inheritance chain,
in which each layer adds behavior to the one above it:

```
View                   ← class-based view primitive (no CRUD)
└── BaseRestView       ← CRUD configuration + helpers (no endpoints)
    ├── RestView         ← sync CRUD endpoints
    │   └── ReactAdminView      ← + ra-data-simple-rest contract
    └── AsyncRestView    ← async CRUD endpoints
        └── AsyncReactAdminView ← + ra-data-simple-rest contract
```

- {class}`View <fastapi_restly.views.View>` is the bare CBV primitive. Use it for non-CRUD endpoints: auth flows,
  custom RPC, file uploads, or composite-key resources.
- {class}`BaseRestView <fastapi_restly.views.BaseRestView>` adds the model,
  schema, list, and response helpers shared by the concrete CRUD views. It is
  an abstract scaffold with no endpoints of its own.
- {class}`RestView <fastapi_restly.views.RestView>` and
  {class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` define the sync
  and async CRUD endpoint methods. [Using RestView](rest_views.md) owns their
  default contract, configuration, and limits.

The [API reference](#view-method-surface) classifies the public
method surface. [Customizing RestView](customize.md) explains the three override
tiers and cross-cutting override points.

## A shared base view for tenant routes

Tenant routes need the same identity dependency and often share an
[error envelope](howto_error_responses.md#change-the-error-envelope-app-wide).
A base view declares the view-level pieces once:

```python
import fastapi_restly as fr

class Current(fr.ContextNamespace):
    tenant_id: fr.ContextParam[int]

class AuthenticatedView(fr.AsyncRestView):
    """Internal base, never registered directly."""
    dependencies = [Current.depends(tenant_id=get_current_tenant_id)]


@fr.include_view(app)
class InvoiceView(AuthenticatedView):
    prefix = "/invoices"
    model = Invoice
    schema = InvoiceRead


@fr.include_view(app)
class CustomerView(AuthenticatedView):
    prefix = "/customers"
    model = Customer
    schema = CustomerRead
```

The dependency binds the current tenant for both views. A session-level
`with_loader_criteria` rule applies that tenant to every ORM read, including
reference checks and reads that replace a view scope. Keep `scope` available
for replaceable surfaces such as soft deletion and role visibility. The
[tenant row scoping](#tenant-row-scoping) recipe contains the session rule,
column stamp, and shared view base.

## Override a single tier

{class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` and {class}`RestView <fastapi_restly.views.RestView>` split every CRUD verb into three tiers: the
endpoint method (HTTP contract), the handler (authorization + commit
bracket), and the business method (domain logic, auth-free and commit-free).
One behavior change therefore means one method override, while routing,
authorization, and the commit stay framework-owned. The tier model, both
request lifecycles, the override decision table, and task-shaped recipes
live in [Customizing RestView](customize.md).

(dependency-injection-on-class-attributes)=
## Dependency injection on class attributes

A class attribute on a view is wired as a FastAPI dependency only when its
annotation either:

- carries an `Annotated[..., Depends(...)]` marker, or
- names one of FastAPI's bare-injectable special types (`Request`,
  `Response`, `BackgroundTasks`, `WebSocket`).

This matches FastAPI function parameters; a plain annotation like
`model: type[Foo]` is only a type hint. The following view demonstrates each
case:

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
they keep working unchanged. The `request` attribute on {class}`BaseRestView <fastapi_restly.views.BaseRestView>`
relies on the special-type rule. Any custom dependency declared on a view
class must use the `Annotated[X, Depends(...)]` form unless it is one of
the bare-injectable types.

This rule makes mixins safe: a mixin can declare what it expects from its
host without shadowing the host's wiring. See
[Composing views with mixins](howto_compose_views_with_mixins.md) for the
mixin pattern.

(when-to-use-view-directly)=
## When to use `View` directly

{class}`View <fastapi_restly.views.View>` is the right tool when your endpoints do not fit a CRUD shape:

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

The class gives three related endpoints one shared prefix and tag, and a
single place for auth dependencies, with no model, no schema, and no CRUD.

## When *not* to use a CBV

If you have a single one-off endpoint that does not share anything with
others, write a plain function endpoint. CBVs pay off when you have shared
metadata, shared dependencies, or related endpoints that benefit from being
co-located. Do not reach for them just for the sake of structure.

## Next steps

- [Using RestView](rest_views.md): the default CRUD contract, schemas, and view
  configuration.
- [Customizing RestView](customize.md): the tier model behind every CRUD verb,
  both request lifecycles, the override decision table, and every override
  recipe.
- [Share Behaviour with Base Views](howto_inheritance.md): patterns for
  tenant isolation, role-based filtering, and shared mixins.
- [API Reference](api_reference.md): full {class}`View <fastapi_restly.views.View>`, {class}`BaseRestView <fastapi_restly.views.BaseRestView>`,
  {class}`RestView <fastapi_restly.views.RestView>`, {class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` signatures and class attributes.

```{toctree}
:maxdepth: 1
:hidden:

Using RestView <rest_views>
Customizing RestView <customize>
```
