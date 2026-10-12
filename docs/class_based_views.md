# Views

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
[Request Lifecycle & Hooks](customize.md) shows how to override those routes or add
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
    async def list_users(self) -> list[UserSchema]:
        users = await self.session.scalars(select(User))
        return [UserSchema.model_validate(user) for user in users]

    @fr.post("")
    async def create_user(self, payload: UserCreate) -> UserSchema:
        user = User(**payload.model_dump())
        self.session.add(user)
        await self.session.flush()
        return UserSchema.model_validate(user)
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
    route_options: ClassVar[Mapping[str | ViewRoute, Mapping[str, Any]]] = {}

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
metadata, makes each method's `self` a dependency on the view class (so
FastAPI instantiates a fresh view per request), and registers each route on
the parent router or app.

Routes are bound at *include-time* against the class you pass in; they are
not bound at decoration time. This is what makes subclassing work.

### Choosing between `@fr.route` and the shorthand decorators

Prefer {func}`@fr.get <fastapi_restly.views.get>`, {func}`@fr.post <fastapi_restly.views.post>`, {func}`@fr.put <fastapi_restly.views.put>`, {func}`@fr.patch <fastapi_restly.views.patch>`, and {func}`@fr.delete <fastapi_restly.views.delete>` for most endpoints. They set the HTTP method automatically and apply Restly's default status codes: `@fr.get`/`@fr.put`/`@fr.patch` use 200, `@fr.post` uses 201, and `@fr.delete` uses 204.

Use {func}`@fr.route(path, methods=[...], ...) <fastapi_restly.views.route>` only when you need full manual control over route options, for example to register a single path under multiple HTTP methods or to set a non-standard response code:

```python
    @fr.route("/{id}/thumbnail", methods=["GET", "HEAD"], status_code=200)
    async def thumbnail(self, id: int):
        ...
```

Both `@fr.route` and the shorthand decorators pass their keyword arguments through to FastAPI's route registration. Class-based routes therefore use the same configuration surface as regular FastAPI routes, including `response_model=`, `status_code=`, `dependencies=`, `responses=`, `tags=`, and other `APIRouter.add_api_route()` options.

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
  and async CRUD endpoint methods. [CRUD Routes](rest_views.md) owns their
  default contract, configuration, and limits.

The [API reference](#view-method-surface) classifies the public
method surface. [Request Lifecycle & Hooks](customize.md) explains how a view handles a
request and where to override it.

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
    schema = InvoiceSchema


@fr.include_view(app)
class CustomerView(AuthenticatedView):
    prefix = "/customers"
    model = Customer
    schema = CustomerSchema
```

The dependency binds the current tenant for both views. A session-level
`with_loader_criteria` rule applies that tenant to every ORM read, including
reference checks and reads that replace a view scope. Keep `scope` available
for replaceable surfaces such as soft deletion and role visibility. The
[tenant row scoping](#tenant-row-scoping) recipe contains the session rule,
column stamp, and shared view base.

(share-behaviour-with-base-views)=

## Share Behaviour with Base Views

FastAPI-Restly views are plain Python classes. Use base classes for shared CRUD overrides, dependencies, access control, and URL namespaces.

Each CRUD route runs through three methods (see [How a view handles a request](#view-request-steps) for the full picture):

- The **endpoint method** ({meth}`create_endpoint <fastapi_restly.views.RestView.create_endpoint>`, {meth}`get_one_endpoint <fastapi_restly.views.RestView.get_one_endpoint>`, and so on) owns the HTTP contract. Override it only to change that contract.
- The **handler** ({meth}`handle_create <fastapi_restly.views.RestView.handle_create>`, {meth}`handle_get_one <fastapi_restly.views.RestView.handle_get_one>`, and so on) handles the steps, such as {meth}`authorize <fastapi_restly.views.RestView.authorize>` and the commit bracket. It is final: custom routes call it, and a base class cannot override it.
- The **business method** ({meth}`create <fastapi_restly.views.RestView.create>`, {meth}`get_one <fastapi_restly.views.RestView.get_one>`, {meth}`update <fastapi_restly.views.RestView.update>`, {meth}`delete <fastapi_restly.views.RestView.delete>`, {meth}`get_many <fastapi_restly.views.RestView.get_many>`) is the auth-free, commit-free domain operation.

The business method is the natural home for shared behaviour, so most of the examples below override it.

### Share a CRUD override across multiple views

To run the same logic on several resources, override a business method on a base class; every subclass picks it up automatically:

```python
class AuditBase(fr.RestView):
    def create(self, schema_obj):
        obj = super().create(schema_obj)
        audit_log.record("created", obj)
        return obj

@fr.include_view(app)
class UserView(AuditBase):
    prefix = "/users"
    model = User
    schema = UserSchema

@fr.include_view(app)
class OrderView(AuditBase):
    prefix = "/orders"
    model = Order
    schema = OrderSchema
```

`audit_log.record` now runs for both `/users` and `/orders`. Register only concrete subclasses, not the base. Because {meth}`create <fastapi_restly.views.RestView.create>` is commit-free, the handler persists the same object the base method recorded.

### Call super() to layer overrides

When one view needs its own logic on top of the shared behaviour, call `super()` to extend the base implementation:

```python
class AuditBase(fr.RestView):
    def create(self, schema_obj):
        obj = super().create(schema_obj)
        audit_log.record("created", obj)
        return obj

@fr.include_view(app)
class OrderView(AuditBase):
    prefix = "/orders"
    model = Order
    schema = OrderSchema

    def create(self, schema_obj):
        schema_obj.created_by = current_user()
        return super().create(schema_obj)
```

`OrderView.create` runs first and delegates to `AuditBase.create`, which in turn delegates to {meth}`RestView.create <fastapi_restly.views.RestView.create>`; all three layers run in order.

### Share post-commit behavior

When shared behaviour should happen only after a durable write, override the
after-hook on the base class:

```python
class NotifyBase(fr.RestView):
    def after_action_commit(self, action, new, old=None):
        if action == "create":
            notify_created(new)
```

Every subclass of `NotifyBase` now fires `notify_created` after the write is
durable. This remains true when a custom endpoint uses
{ref}`shared_write_action_commit() <shared-write-action-commit>`, which
queues the hook until the outermost block commits.

### Inherit a shared dependency

To make a value such as the current user available in every subclass, declare the dependency as an instance annotation on the base class:

```python
from typing import Annotated
from fastapi import Depends

class AuthBase(fr.RestView):
    current_user: Annotated[User, Depends(get_current_user)]

    def create(self, schema_obj):
        obj = super().create(schema_obj)
        obj.owner_id = self.current_user.id
        return obj

@fr.include_view(app)
class NoteView(AuthBase):
    prefix = "/notes"
    model = Note
    schema = NoteSchema
```

`self.current_user` is available in every subclass method, and because {meth}`create <fastapi_restly.views.RestView.create>` runs before commit, stamping `owner_id` persists. The injection mechanism itself is described in [Dependency injection on class attributes](class_based_views.md#dependency-injection-on-class-attributes).

### Apply router-level dependencies to all routes

Setting `dependencies = [Depends(fn)]` applies `fn` to every route, and subclasses inherit it:

```python
class ProtectedBase(fr.RestView):
    dependencies = [Depends(require_auth)]

@fr.include_view(app)
class UserView(ProtectedBase):
    prefix = "/users"
    model = User
    schema = UserSchema

@fr.include_view(app)
class OrderView(ProtectedBase):
    prefix = "/orders"
    model = Order
    schema = OrderSchema
```

Every route on `/users` and `/orders` now requires authentication.

A subclass that sets its own `dependencies` adds them after the base's, the
way nested FastAPI routers add up, so it cannot drop a guard the base
declares. To run its own dependency first, it lists the base's again:
`dependencies = [own, *ProtectedBase.dependencies]`. Each one still runs once.
{attr}`responses <fastapi_restly.views.View.responses>` add up the
same way, and a subclass's entry for a status code wins. Other class attributes,
such as `tags`, replace the base's value.

(prefix-concatenation)=

### Concatenate URL prefixes

When a base class defines {attr}`prefix <fastapi_restly.views.View.prefix>`, subclass prefixes are appended to it. This lets you declare a shared URL namespace once:

```python
class ApiV1(fr.RestView):
    prefix = "/api/v1"

@fr.include_view(app)
class UserView(ApiV1):
    prefix = "/users"     # becomes /api/v1/users
    model = User
    schema = UserSchema

@fr.include_view(app)
class OrderView(ApiV1):
    prefix = "/orders"    # becomes /api/v1/orders
    model = Order
    schema = OrderSchema
```

Prefixes concatenate across as many levels as you have:

```python
class AdminBase(fr.RestView):
    prefix = "/admin"

class V2Base(AdminBase):
    prefix = "/v2"

@fr.include_view(app)
class ReportView(V2Base):
    prefix = "/reports"   # becomes /admin/v2/reports
    model = Report
    schema = ReportSchema
```

A prefix can also contain a path parameter, such as `/projects/{project_id}`.
[Nested Resources](#nested-resources) builds child resources this way.

### Inherit custom routes

Custom routes defined with {func}`@fr.get <fastapi_restly.views.get>`, {func}`@fr.post <fastapi_restly.views.post>`, and friends on a base class are inherited by all registered subclasses:

```python
class HealthBase(fr.RestView):
    @fr.get("/health")
    def health(self):
        return {"ok": True}

@fr.include_view(app)
class UserView(HealthBase):
    prefix = "/users"
    model = User
    schema = UserSchema
```

`GET /users/health` is registered alongside the standard CRUD endpoints.

### Restrict available endpoints on a base class

Set {attr}`exclude_routes <fastapi_restly.views.BaseRestView.exclude_routes>` on a base class to make every subclass read-only, or to apply whatever restriction you need:

```python
class ReadOnlyBase(fr.RestView):
    exclude_routes = (fr.ViewRoute.CREATE, fr.ViewRoute.UPDATE, fr.ViewRoute.DELETE)

@fr.include_view(app)
class ProductView(ReadOnlyBase):
    prefix = "/products"
    model = Product
    schema = ProductSchema
```

`ProductView` only exposes `GET /products` and `GET /products/{id}`.

### Implement soft-delete once

A base class can override the {meth}`delete <fastapi_restly.views.RestView.delete>`
business method once for every subclass, exactly like the audit example above
but with the soft-delete body. The full `deleted_at` recipe is in
{ref}`Request Lifecycle & Hooks <soft-delete-recipe>`. The reusable mixin that also
hides flagged rows on read is in [Models](#soft-delete-mixin).

## Override a single step

{class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` and {class}`RestView <fastapi_restly.views.RestView>` run every CRUD route through three methods: the
endpoint method (HTTP contract), the handler, and the business method (domain
logic, auth-free and commit-free). The handler handles the steps, such as
authorization and the commit, and it is final. One behavior change therefore
means one override of a step, while routing, authorization, and the commit
stay framework-owned. The steps of a request, the override decision table,
and task-shaped recipes live in [Request Lifecycle & Hooks](#view-request-steps).

(dependency-injection-on-class-attributes)=
## Dependency injection on class attributes

A class attribute on a view becomes a FastAPI dependency only when its
annotation either:

- carries an `Annotated[..., Depends(...)]` marker (`Security(...)` counts,
  since it is a kind of `Depends`), or
- names one of FastAPI's bare-injectable special types (`Request`,
  `Response`, `BackgroundTasks`, `WebSocket`).

Every other annotation is only a type hint. This differs from a function
parameter, where FastAPI reads a plain annotation as a query or body
parameter. The following view demonstrates each case:

```python
from typing import Annotated

from fastapi import Depends, Request

class UserView(fr.AsyncRestView):
    # Dependency: AsyncSessionDep is Annotated[AsyncSession, Depends(...)].
    session: fr.AsyncSessionDep

    # Dependency: Request is one of FastAPI's bare-injectable specials.
    request: Request

    # Dependency: explicit Depends marker.
    current_user: Annotated[User, Depends(get_current_user)]

    # Not a dependency: a bare type is only a hint.
    reviewer: User
```

A dependency attribute is set on the view instance before any method runs, so
endpoint methods, business methods, `authorize`, and the commit hooks all
read it as `self.current_user`. An attribute that is not a dependency is never set
from the request: reading `self.reviewer` raises `AttributeError`.

The shipped `AsyncSessionDep` / `SessionDep` aliases carry `Depends`. The
`request` attribute on {class}`BaseRestView <fastapi_restly.views.BaseRestView>`
relies on the special-type rule.

A request parameter belongs on the endpoint method that reads it. A class
attribute with a `Path()`, `Query()`, `Header()`, `Cookie()`, `Body()`,
`Form()` or `File()` marker raises
{class}`RestlyConfigurationError <fastapi_restly.exc.RestlyConfigurationError>`
when the view is registered, because FastAPI would never set it. A path
segment or header that every route of the view needs comes through a
dependency instead; [Nested Resources](#nested-resources) shows the pattern for
a path segment.

A plain annotation never replaces a dependency annotation. A mixin or
subclass can declare `current_user: User` so that type checkers know the
attribute, and it still keeps the base's `Depends` marker. This rule makes
mixins safe: a mixin can declare what it expects from its host without
shadowing the host's dependencies. See
[Type stubs on mixins](#type-stubs-on-mixins-must-use-if-type_checking) for the
mixin pattern.

### Type stubs on mixins must use `if TYPE_CHECKING:`

A mixin often needs to declare what it requires from its host class
(`session`, `request`, helper methods like `_current_org_id`). Declaring
those as plain class members shadows the host's implementation via the MRO:

```python
# WRONG: this real method body shadows the host's _current_org_id.
class SoftDeleteMixin:
    def _current_org_id(self) -> int | None:
        ...  # a stub body is still a real method
```

Wrap the stubs in `if TYPE_CHECKING:` so pyright sees them but Python
does not add them to the runtime class:

```python
class SoftDeleteMixin:
    if TYPE_CHECKING:
        def _current_org_id(self) -> int | None: ...
```

The same applies to typed *attribute* annotations. Marker-based DI (see
[Views](class_based_views.md#dependency-injection-on-class-attributes))
means a plain `model: type[DeclarativeBase]` annotation no longer
shadows a dependency, but it can still shadow inherited attribute lookups
in some setups. `if TYPE_CHECKING:` is the safe wrapper for both.

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

(receive-a-webhook)=

### Receive a webhook (inbound)

An inbound webhook receiver is not CRUD, so use a bare
{class}`fr.View <fastapi_restly.views.View>` with the raw `Request`. Verify
the signature before parsing, and commit explicitly: the framework's
auto-commit bracket only wraps {class}`RestView <fastapi_restly.views.RestView>`
handlers, so a bare `View` route owns its commit (the same contract as
{func}`fr.open_async_session() <fastapi_restly.db.open_async_session>`).

```python
from fastapi import Request

@fr.include_view(app)
class PaymentWebhookView(fr.View):
    prefix = "/webhooks"
    session: fr.AsyncSessionDep

    @fr.post("/payments", status_code=204)
    async def receive_payment_event(self, request: Request):
        payload = await request.body()
        verify_signature(payload, request.headers.get("X-Signature"))
        event = json.loads(payload)
        self.session.add(PaymentEvent(kind=event["type"], data=payload.decode()))
        await self.session.commit()  # a bare View owns its commit
```

For *outbound* webhooks (calling someone else after a write), use the
{meth}`after_action_commit <fastapi_restly.views.RestView.after_action_commit>` hook
instead, or an outbox row for a webhook that must arrive. See
[When `after_action_commit` raises](#after-action-commit-raises).

## When *not* to use a CBV

If you have a single one-off endpoint that does not share anything with
others, write a plain function endpoint. CBVs pay off when you have shared
metadata, shared dependencies, or related endpoints that benefit from being
co-located. Do not reach for them just for the sake of structure.

(view-registration-internals)=

## Internals

[CRUD Routes](rest_views.md) owns the user-facing model, schema,
identity, route, and list configuration. At registration,
{meth}`before_include_view() <fastapi_restly.views.BaseRestView.before_include_view>`
turns that configuration into FastAPI signatures. It derives missing schemas,
builds the list params, sets the endpoint annotations, and
removes the route marker for each exclusion.

### include_view()

{func}`include_view() <fastapi_restly.views.include_view>` is the registration boundary between declarative view modules
and application composition. For larger apps, define view classes without
side effects in subject packages, then include them from the module that builds
your `FastAPI` app or `APIRouter`:

```python
fr.include_view(app, MyView)
```

This keeps imports predictable: importing `app.users.views` defines
`UserView`, while `app.main` decides which app or router receives it. See
[New & Existing Projects](howto_project_structure.md) for the layout this assumes.
For small apps and examples, `include_view()` also works as a decorator:

```python
@fr.include_view(app)
class MyView(fr.AsyncRestView):
    ...
```

Both forms call {meth}`before_include_view() <fastapi_restly.views.BaseRestView.before_include_view>` (which generates derived schemas,
annotates endpoint signatures, and registers the {attr}`schema_list_params <fastapi_restly.views.BaseRestView.schema_list_params>`), then
attach an `APIRouter` to the parent app/router.

## Next steps

- [CRUD Routes](rest_views.md): the default CRUD contract, schemas, and view
  configuration.
- [Request Lifecycle & Hooks](customize.md): how a view handles a request, the
  override decision table, and every override recipe.
- [API Reference](api_reference.md): full {class}`View <fastapi_restly.views.View>`, {class}`BaseRestView <fastapi_restly.views.BaseRestView>`,
  {class}`RestView <fastapi_restly.views.RestView>`, {class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` signatures and class attributes.
