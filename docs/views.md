# Views

A view is a Python class that groups related FastAPI endpoints. The class
declares once what its endpoints share, such as a URL prefix and
dependencies. Its methods are the endpoints. A subclass inherits all of this,
and it can override any method.

(what-is-a-class-based-view)=
## How a view works

In plain FastAPI, an endpoint is a function, and each function declares what
it needs:

```python
@app.get("/users")
async def list_users(session: AsyncSession = Depends(get_session)):
    ...

@app.post("/users")
async def create_user(payload: UserCreate, session: AsyncSession = Depends(get_session)):
    ...
```

A view declares the same endpoints as methods of one class:

```python
import fastapi_restly as fr
from fastapi import Depends
from sqlalchemy import select


class UserView(fr.View):
    prefix = "/users"
    dependencies = [Depends(require_login)]
    session: fr.AsyncSessionDep

    @fr.get("")
    async def list_users(self) -> list[UserSchema]:
        users = await self.session.scalars(select(User))
        return [UserSchema.model_validate(user) for user in users]

    @fr.post("")
    async def create_user(self, payload: UserCreate) -> UserSchema:
        user = User(**payload.model_dump())
        self.session.add(user)
        await self.session.commit()
        return UserSchema.model_validate(user)


fr.include_view(app, UserView)
```

The parts work together like this:

- `prefix` and `dependencies` apply to every route of the view.
  `require_login` runs before both endpoints.
- {func}`@fr.get <fastapi_restly.views.get>` and
  {func}`@fr.post <fastapi_restly.views.post>` only mark a method as a route.
  They register nothing.
- {func}`fr.include_view <fastapi_restly.views.include_view>` registers the
  routes on the app. It reads the class at that moment, together with the
  routes that the class inherits.
- For each request, FastAPI creates a new instance of the view. FastAPI
  resolves each dependency attribute, such as `session`, and the instance
  gets the value before the method runs. So `self.session` is the session of
  this request.
- The methods are normal Python methods. They can call other methods and read
  class attributes through `self`.

Because the routes bind when you register the class, a subclass gets the
routes of its base. When the subclass overrides a method, its own version
runs.

(choose-a-view-class)=
## Choose a view class

A view helps when endpoints share something: dependencies, OpenAPI metadata,
or a value such as the current user. For one endpoint that shares nothing, a
plain FastAPI function is simpler.

| You are building | Use |
|---|---|
| One endpoint that shares nothing with others | A plain FastAPI function, with no Restly view |
| A group of endpoints that are not CRUD: login, webhooks, RPC-style actions, resources with a composite key | {class}`fr.View <fastapi_restly.views.View>` ([Group endpoints that are not CRUD](#when-to-use-view-directly)) |
| CRUD routes for a database model | {class}`fr.AsyncRestView <fastapi_restly.views.AsyncRestView>` or {class}`fr.RestView <fastapi_restly.views.RestView>` ([CRUD Routes](crud_routes.md)) |
| CRUD routes plus actions such as publish | `AsyncRestView` or `RestView` with extra {func}`@fr.get <fastapi_restly.views.get>` or {func}`@fr.post <fastapi_restly.views.post>` methods ([Add a custom action route](#custom-action-route)) |

[Choose async or sync](#choose-async-or-sync) explains the choice between
`AsyncRestView` and `RestView`. All view classes follow the rules on this page
for routes, registration, dependencies and inheritance.

(the-view-hierarchy)=
### The view classes

The CRUD views build on {class}`View <fastapi_restly.views.View>`. Each class
adds to the one above it:

```
View                     ← routes, prefix, dependencies; no CRUD
└── BaseRestView         ← model, schemas, list and response helpers; no routes
    ├── RestView             ← sync CRUD routes
    │   └── ReactAdminView       ← + the ra-data-simple-rest contract
    └── AsyncRestView        ← async CRUD routes
        └── AsyncReactAdminView  ← + the ra-data-simple-rest contract
```

- {class}`View <fastapi_restly.views.View>` is the base of every view. Use it
  directly for endpoints that are not CRUD.
- {class}`BaseRestView <fastapi_restly.views.BaseRestView>` holds the
  configuration and the helpers that the CRUD views share. It has no routes.
  Import it from `fastapi_restly.views`; it is not in the `fr` namespace.
- {class}`RestView <fastapi_restly.views.RestView>` and
  {class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` add the sync and
  async CRUD routes. [CRUD Routes](crud_routes.md) describes them.
- {class}`ReactAdminView <fastapi_restly.views.ReactAdminView>` and
  {class}`AsyncReactAdminView <fastapi_restly.views.AsyncReactAdminView>`
  serve the contract that React Admin expects; see
  [React Admin](react_admin.md).

[View Reference](#view-method-surface) lists every method of these classes.

(declare-a-route)=
## Declare routes

A route is a method with a route decorator. Use
{func}`@fr.get <fastapi_restly.views.get>`,
{func}`@fr.post <fastapi_restly.views.post>`,
{func}`@fr.put <fastapi_restly.views.put>`,
{func}`@fr.patch <fastapi_restly.views.patch>` or
{func}`@fr.delete <fastapi_restly.views.delete>`. Each one sets the HTTP
method and a default status code. [Endpoint decorators](#endpoint-decorators)
lists the status codes. Pass `status_code=` to use another one.

The path is relative to the view's prefix. With `prefix = "/users"`,
`@fr.get("/{id}/summary")` serves `/users/{id}/summary`, and `@fr.get("")`
serves `/users` itself.

Every other keyword argument goes to FastAPI's `APIRouter.add_api_route()`,
such as `response_model=`, `dependencies=`, `responses=` and `tags=`. So a
route on a view has the same options as a FastAPI route.

To serve one path with more than one HTTP method, use
{func}`@fr.route <fastapi_restly.views.route>` and list the methods. It sets
no method and no status code for you:

```python
    @fr.route("/{id}/thumbnail", methods=["GET", "HEAD"], status_code=200)
    async def thumbnail(self, id: int):
        ...
```

A route method must not have the name of a business method: `get_many`,
`get_one`, `create`, `update` or `delete`. Registration raises `TypeError`
for such a name, also on a plain `View`. Give the route another name. To
replace a CRUD route, override its endpoint method, such as
`create_endpoint`; see
[Change the status code or headers of one route](#replace-endpoint-method).

On a CRUD view, a custom route usually loads and writes through the view's
handlers. [Add a custom read route](#custom-read-route) and
[Add a custom action route](#custom-action-route) show how.

(register-a-view)=
## Register a view

Call {func}`fr.include_view <fastapi_restly.views.include_view>` with the app
and the view class:

```python
fr.include_view(app, UserView)
```

The first argument is a FastAPI app or an `APIRouter`. In a larger app, define
each view in its own module, and do not register it there. Register all views
in the module that builds the app. Importing a view module then has no side
effects. [Compose in one place](#compose-in-one-place) shows this layout.

In a small app or a single file, use `include_view` as a class decorator. It
registers the class when Python defines it:

```python
@fr.include_view(app)
class UserView(fr.View):
    ...
```

Both forms register the same routes.

The routes bind when you register the view, not when you declare the
methods. `include_view` reads the class at that moment: its own route
methods and the ones it inherits, and the prefix and dependencies of every
class in its hierarchy. On a CRUD view, it also builds each schema that the
view does not declare. [Internals](#view-registration-internals) lists the
steps.

Some rules for registration:

- Register only the classes that serve routes. A base view that you only
  subclass stays unregistered; see
  [Share behavior with base views](#share-behaviour-with-base-views).
- You can register one view on more than one parent, such as a public app and
  an admin router. Each parent gets the routes.
- Registering a view again on the same parent does nothing. With
  `fr.configure(warn_on_misuse=True)`, Restly warns about it with a
  {class}`RestlyMisuseWarning <fastapi_restly.exc.RestlyMisuseWarning>`. It
  usually means that a module was imported twice, or that the decorator and a
  direct call both registered the view.
- FastAPI tries the routes in the order you added them. For a route such as
  `/users/me` next to the CRUD routes, see
  [Item paths and static routes](#item-route-matching).

(dependency-injection-on-class-attributes)=
## Declare dependencies as class attributes

A class attribute on a view is a FastAPI dependency only when its annotation
does one of these:

- It carries a `Depends(...)` marker inside `Annotated[...]`. A
  `Security(...)` marker counts too, because it is a kind of `Depends`.
- It names one of FastAPI's special types: `Request`, `Response`,
  `BackgroundTasks` or `WebSocket`.

Every other annotation is only a type hint. This is different from a function
parameter: there, FastAPI reads a plain annotation as a query or body
parameter. This view shows each case:

```python
from typing import Annotated

from fastapi import Depends, Request

class UserView(fr.AsyncRestView):
    # Dependency: AsyncSessionDep is Annotated[AsyncSession, Depends(...)].
    session: fr.AsyncSessionDep

    # Dependency: Request is one of FastAPI's special types.
    request: Request

    # Dependency: an explicit Depends marker.
    current_user: Annotated[User, Depends(get_current_user)]

    # Not a dependency: a plain type is only a hint.
    reviewer: User
```

The view instance gets each dependency attribute before any method runs.
So endpoint methods, business methods, `authorize` and the
commit hooks can all read `self.current_user`. An attribute that is not a
dependency is never set from the request: reading `self.reviewer` raises
`AttributeError`.

{data}`fr.AsyncSessionDep <fastapi_restly.db.AsyncSessionDep>` and
{data}`fr.SessionDep <fastapi_restly.db.SessionDep>` carry a `Depends`
marker. The `request` attribute of
{class}`BaseRestView <fastapi_restly.views.BaseRestView>` is a dependency
because of the special-type rule.

Put a request parameter, such as a query parameter or a header, on the
endpoint method that reads it. A class attribute with a `Path()`, `Query()`,
`Header()`, `Cookie()`, `Body()`, `Form()` or `File()` marker raises
{class}`RestlyConfigurationError <fastapi_restly.exc.RestlyConfigurationError>`
when you register the view, because FastAPI would never set it. When every
route of the view needs the same path segment or header, read it in a
dependency. [Nest a resource under its parent](#nested-resources) does this
for a path segment.

A plain annotation never replaces a dependency annotation. A mixin or a
subclass can declare `current_user: User` for type checkers, and the
attribute keeps the `Depends` marker of the base. So a mixin can declare what
it needs from its view without breaking the view's dependencies.

(mixin-type-stubs)=
### Declare what a mixin needs from its view

A mixin is a class that you list before the view class in the bases, as in
`class ProjectView(ArchiveMixin, fr.AsyncRestView)`. Its methods often use
attributes and methods of the view, and type checkers need to know them. Do
not write these stubs as normal class members. A stub method is a real
method, and Python finds it before the view's method:

```python
# Wrong: this stub is a real method. It hides AsyncRestView.get_one,
# so `await self.get_one(id)` returns None.
class ArchiveMixin:
    async def get_one(self, id): ...
```

Put the stubs under `if TYPE_CHECKING:`. Type checkers read them, and Python
does not add them to the class:

```python
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession


class ArchiveMixin:
    if TYPE_CHECKING:
        session: AsyncSession

        async def get_one(self, id: Any) -> Any: ...
```

An attribute annotation without a value, such as `session: AsyncSession`,
adds nothing to the class, so it hides nothing. It can also stay outside the
block, but one block for all stubs is easier to read. The `SoftDeleteMixin`
of the SaaS example does this
([`app/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/views.py)).

(share-behaviour-with-base-views)=
## Share behavior with base views

A base view is a view class that you subclass but do not register. Each
subclass inherits what the base declares: dependency attributes,
`dependencies`, route methods, overrides and other class attributes.
Register only the subclasses.

A base view overrides the same methods as a single view.
[How a view handles a request](#view-request-steps) explains which method to
override for each change. Two shared concerns have their own recipes on
[Models](models.md): values that every write path must set, such as an
owner id ([Values every write path sets](#write-path-values)), and
[soft delete](#soft-delete-mixin).

(app-wide-base-view)=
### One base view for the whole app

A base view for the whole app declares the request context once. Every view
in the app subclasses it. It needs no CRUD:

```python
from typing import Annotated

from fastapi import Depends
from sqlalchemy import select

import fastapi_restly as fr


class AppView(fr.View):
    """The base of every view in the app. Never registered."""

    session: fr.AsyncSessionDep
    current_user: Annotated[User, Depends(get_current_user)]


@fr.include_view(app)
class ProfileView(AppView):
    prefix = "/profile"

    @fr.get("")
    async def whoami(self) -> dict:
        return {"user": self.current_user.name}


@fr.include_view(app)
class MyInvoicesView(AppView):
    prefix = "/my-invoices"

    @fr.get("")
    async def my_invoices(self) -> list[InvoiceSchema]:
        invoices = await self.session.scalars(
            select(Invoice).where(Invoice.user_id == self.current_user.id)
        )
        return [InvoiceSchema.model_validate(invoice) for invoice in invoices]
```

In plain FastAPI, each function in the app would declare `session` and
`current_user` as parameters again. Here, each method reads them from `self`.
CRUD views can use the same base:

```python
class AppRestView(AppView, fr.AsyncRestView):
    """CRUD views get the same session and current_user attributes."""
```

This also helps tests: FastAPI's `app.dependency_overrides` replaces
`get_current_user` for every view at once.

(run-a-dependency-on-every-route)=
### Run a dependency on every route

{attr}`dependencies <fastapi_restly.views.View.dependencies>` lists FastAPI
dependencies that run for every route of the view. They give no value to the
methods. Use them for a check, such as a login check, or to bind a
{class}`fr.ContextNamespace <fastapi_restly.clauses.ContextNamespace>` such
as `Current` for the request:

```python
from fastapi import Depends


class AuthenticatedView(fr.AsyncRestView):
    """Never registered."""

    dependencies = [
        Depends(require_login),
        Current.depends(user_id=get_current_user_id),
    ]


@fr.include_view(app)
class InvoiceView(AuthenticatedView):
    prefix = "/invoices"
    model = Invoice


@fr.include_view(app)
class CustomerView(AuthenticatedView):
    prefix = "/customers"
    model = Customer
```

Every route of `/invoices` and `/customers` now requires a login and binds
`Current.user_id`. [Bind from a FastAPI dependency](#current-request-binding)
explains the binding. The SaaS example's `AuthenticatedView` uses this
pattern
([`app/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/views.py)).
To limit every read to the rows of one tenant, see
[Keep each tenant's rows apart](#tenant-row-scoping).

When a subclass sets its own `dependencies`, they run after the base's, in
the same way as nested FastAPI routers. So a subclass cannot remove a check
that its base declares. To run its own dependency first, a subclass lists the
base's dependencies again after it:
`dependencies = [own, *AuthenticatedView.dependencies]`. Each dependency
still runs only once.
{attr}`responses <fastapi_restly.views.View.responses>` add up in the same
way, and for a status code that both declare, the subclass's entry wins.
[Concatenate URL prefixes](#prefix-concatenation) shows how `prefix` adds
up. Other class attributes, such as `tags`, replace the base's value.

### Share an authorization check

A base view can read a dependency attribute in a method that every subclass
runs. Here, every caller may read and create, and only the owner of a row may
do anything else to it:

```python
from typing import Annotated

from fastapi import Depends


class OwnedView(fr.AsyncRestView):
    """Base for rows that only their owner may change. Never registered."""

    current_user: Annotated[User, Depends(get_current_user)]

    async def authorize(self, action, obj=None, data=None):
        if action in (fr.Action.GET_MANY, fr.Action.GET_ONE, fr.Action.CREATE):
            return
        if obj is None or obj.owner_id != self.current_user.id:
            raise fr.exc.Forbidden("Only the owner can change this")


@fr.include_view(app)
class NoteView(OwnedView):
    prefix = "/notes"
    model = Note


@fr.include_view(app)
class AlbumView(OwnedView):
    prefix = "/albums"
    model = Album
```

`NoteView` and `AlbumView` now run the same check. The owner check also
covers update, delete and every custom action, such as an `archive` route,
so a new action is never open to every caller.
[Authorization](#authorize-gate) explains `authorize`. The blog example's
`AuthoredView` shares one `authorize` between posts and comments in the same
way
([`blog/main.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/blog/blog/main.py)).

The view only reads the caller here. To store the caller on a row, such as
the owner id, use a column default on the model. Then every write path sets
it, also the ones that never pass through a view
([Stamp the fields the server owns](#server-stamped-fields)).

### Share a business method

A base view can override a business method once for all its subclasses.
Here, two resources store a password hash:

```python
class PasswordHashingView(fr.AsyncRestView):
    """Base for resources that store a password hash. Never registered."""

    async def create(self, schema_obj):
        obj = await self.make_new_object(schema_obj)
        obj.password = hash_password(schema_obj.password)
        return await self.save_object(obj)

    async def update(self, obj, schema_obj):
        obj = await self.update_object(obj, schema_obj)
        if "password" in schema_obj.model_fields_set:
            obj.password = hash_password(schema_obj.password)
        return await self.save_object(obj)


@fr.include_view(app)
class UserView(PasswordHashingView):
    prefix = "/users"
    model = User
    schema = UserSchema


@fr.include_view(app)
class ServiceAccountView(PasswordHashingView):
    prefix = "/service-accounts"
    model = ServiceAccount
    schema = ServiceAccountSchema
```

Here, only the HTTP routes receive a plain password, so the hash belongs in
`create` and `update`. The generated update body accepts `password` too, so
without the `update` override a `PATCH` request would store the new password
without a hash. [Hash a password](#password-hash-example) explains these
overrides for one view.

A subclass can override the same method again. Its version then replaces the
base's version. To run the base's code too, call `super()`, as in any Python
class.

### Run code after the commit

A base view can also share a transaction hook:

```python
class NotifyingView(fr.AsyncRestView):
    async def after_action_commit(self, action, new, old=None):
        if action == fr.Action.CREATE:
            await notify_created(new)
```

Every subclass of `NotifyingView` calls `notify_created` after a create is
committed. This is also true when a custom route groups several writes with
{ref}`shared_write_action_commit() <shared-write-action-commit>`: the hook
then waits until the outermost block commits.
[Run code before or after the commit](#transaction-hooks) explains both hooks, and
[When after_action_commit raises](#after-action-commit-raises) explains what
happens when the hook fails.

### Inherit routes

Every registered subclass inherits the custom routes of its base:

```python
class FieldsRouteView(fr.AsyncRestView):
    @fr.get("/fields")
    async def fields(self) -> list[str]:
        return list(self.schema_response.model_fields)


@fr.include_view(app)
class UserView(FieldsRouteView):
    prefix = "/users"
    model = User
    schema = UserSchema
```

`GET /users/fields` now exists next to the CRUD routes of `UserView`. A
subclass also inherits
{attr}`exclude_routes <fastapi_restly.views.BaseRestView.exclude_routes>`,
so a base view can leave out the same CRUD routes in every subclass; see
[Exclude CRUD routes](#exclude-crud-routes).

(prefix-concatenation)=
### Concatenate URL prefixes

When a base view sets {attr}`prefix <fastapi_restly.views.View.prefix>`, the
prefix of each subclass is added after it. So you declare a shared URL
namespace once:

```python
class ApiV1(fr.AsyncRestView):
    prefix = "/api/v1"


@fr.include_view(app)
class UserView(ApiV1):
    prefix = "/users"     # becomes /api/v1/users
    model = User


@fr.include_view(app)
class OrderView(ApiV1):
    prefix = "/orders"    # becomes /api/v1/orders
    model = Order
```

This works for any number of levels:

```python
class AdminBase(fr.AsyncRestView):
    prefix = "/admin"


class V2Base(AdminBase):
    prefix = "/v2"


@fr.include_view(app)
class ReportView(V2Base):
    prefix = "/reports"   # becomes /admin/v2/reports
    model = Report
```

A prefix can also contain a path parameter, such as `/projects/{project_id}`.
[Nest a resource under its parent](#nested-resources) builds child resources
in this way.

(when-to-use-view-directly)=
## Group endpoints that are not CRUD

Use a plain {class}`View <fastapi_restly.views.View>` for endpoints that do
not fit the CRUD shape: login routes, webhook receivers, RPC-style actions. A
`View` has no model, no schema and no CRUD routes. It gives the endpoints one
prefix, one tag and one place for shared dependencies.

A `View` route that writes to the database must commit. Only the CRUD views
commit for you. When a request ends with changes that were not committed,
Restly warns with a
{class}`RestlyUncommittedChangesWarning <fastapi_restly.exc.RestlyUncommittedChangesWarning>`.

(login-routes)=
### Login, refresh and logout routes

```python
@fr.include_view(app)
class AuthView(fr.View):
    prefix = "/auth"
    tags = ["auth"]
    session: fr.AsyncSessionDep

    @fr.post("/login", status_code=200)
    async def login(self, credentials: LoginRequest) -> Token:
        ...

    @fr.post("/refresh", status_code=200)
    async def refresh(self, body: RefreshRequest) -> Token:
        ...

    @fr.post("/logout", status_code=204)
    async def logout(self) -> None:
        ...
```

`@fr.post` answers `201` by default. A login creates no resource, so these
routes set their own status code. The methods are FastAPI endpoints, so they
can use FastAPI's security tools like any other endpoint. To make the
logged-in user available to other views, bind it for each request; see
[Bind from a FastAPI dependency](#current-request-binding).

(receive-a-webhook)=
### Receive a webhook

A receiver for incoming webhooks is not CRUD, so use a plain `View` and read
the raw `Request`. Check the signature before you parse the body, and commit
yourself:

```python
import json

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
        await self.session.commit()  # a plain View commits itself
```

A route that commits itself also handles its own database errors. For
example, a sender that sends the same event twice can break a unique
constraint. Restly does not turn that `IntegrityError` into a `409`, because
the route is outside a Restly write; see
[Unique values and other database conflicts: 409](#default-exception-handling).

To call another service after a write, use the
{meth}`after_action_commit <fastapi_restly.views.RestView.after_action_commit>`
hook of a CRUD view instead. For a call that must not get lost, write an
outbox row in `before_action_commit`; see
[When after_action_commit raises](#after-action-commit-raises).

(view-registration-internals)=
## Internals

{func}`include_view() <fastapi_restly.views.include_view>` connects a view
class to FastAPI. FastAPI builds routes from function signatures, so most of
its work is to give the route methods the right signatures. If the view is
already registered on the same parent, it does nothing. Otherwise, it runs
these steps:

1. It copies each route method that the class inherits onto the class
   itself. Restly can then change the signature of the copy without changing
   the base or other subclasses.
2. It checks that no route method has the name of a business method.
3. It gives each route method a unique name, such as
   `userview_get_one_endpoint`. It makes the `self` parameter a dependency on
   the view class, `Depends(UserView)`, so FastAPI creates a new view for
   each request.
4. It calls
   {meth}`before_include_view() <fastapi_restly.views.BaseRestView.before_include_view>`.
   On a CRUD view, this builds the schemas that the view does not declare and
   the {attr}`schema_list_params <fastapi_restly.views.BaseRestView.schema_list_params>`.
   It sets the item path from `id_type`, fills in the types of the endpoint
   methods, and removes the route marker of each excluded route.
5. It turns the dependency attributes into keyword parameters of the view's
   `__init__`. FastAPI then resolves them, and the new instance stores them.
6. It builds an `APIRouter`. The prefix joins the prefixes of all classes in
   the hierarchy, base first. Their `dependencies` and `responses` add up in
   the same order. It adds each route method with its decorator arguments and
   its `route_options`, and then includes the router in the parent.

Steps 1 to 5 run once for each class. A second registration on another
parent only builds a new router.

`before_include_view()` is a class method that you can override to change
the route methods before registration. On a CRUD view, call `super()` in it.

## See also

- [CRUD Routes](crud_routes.md): the default CRUD routes and their
  configuration.
- [Request Lifecycle & Hooks](lifecycle.md): how a view handles a request,
  and which method to override.
- [View Reference](view_reference.md): every class attribute and method of
  {class}`View <fastapi_restly.views.View>`,
  {class}`BaseRestView <fastapi_restly.views.BaseRestView>`,
  {class}`RestView <fastapi_restly.views.RestView>` and
  {class}`AsyncRestView <fastapi_restly.views.AsyncRestView>`.
