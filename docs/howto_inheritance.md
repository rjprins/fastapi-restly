# Share Behaviour with Base Views

FastAPI-Restly views are plain Python classes. Use base classes for shared CRUD overrides, dependencies, access control, and URL namespaces.

Each CRUD verb is implemented in three tiers (see [Customize RestView](customize.md) for the full model):

- The **route shell** ({meth}`create_endpoint <fastapi_restly.views.RestView.create_endpoint>`, {meth}`get_one_endpoint <fastapi_restly.views.RestView.get_one_endpoint>`, and so on) is the wire boundary. It is rarely overridden on a base class.
- The **request handler** ({meth}`handle_create <fastapi_restly.views.RestView.handle_create>`, {meth}`handle_get_one <fastapi_restly.views.RestView.handle_get_one>`, and so on) runs {meth}`authorize <fastapi_restly.views.RestView.authorize>` and the commit bracket.
- The **business verb** ({meth}`create <fastapi_restly.views.RestView.create>`, {meth}`get_one <fastapi_restly.views.RestView.get_one>`, {meth}`update <fastapi_restly.views.RestView.update>`, {meth}`delete <fastapi_restly.views.RestView.delete>`, {meth}`get_many <fastapi_restly.views.RestView.get_many>`) is the auth-free, commit-free domain operation.

The business verb is the natural home for shared behaviour, so most of the examples below override it.

## Share a CRUD override across multiple views

To run the same logic on several resources, override a business verb on a base class; every subclass picks it up automatically:

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
    schema = UserRead

@fr.include_view(app)
class OrderView(AuditBase):
    prefix = "/orders"
    model = Order
    schema = OrderRead
```

`audit_log.record` now runs for both `/users/` and `/orders/`. Register only concrete subclasses, not the base. Because {meth}`create <fastapi_restly.views.RestView.create>` is commit-free, the handler persists the same object the base method recorded.

## Call super() to layer overrides

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
    schema = OrderRead

    def create(self, schema_obj):
        schema_obj.created_by = current_user()
        return super().create(schema_obj)
```

`OrderView.create` runs first and delegates to `AuditBase.create`, which in turn delegates to {meth}`RestView.create <fastapi_restly.views.RestView.create>`; all three layers run in order.

## Share an orchestration override

When shared behaviour is about *timing*, override the request handler instead of the business verb. This keeps the route shell unchanged:

```python
class NotifyBase(fr.RestView):
    def handle_create(self, schema_obj):
        obj = super().handle_create(schema_obj)
        # super().handle_create has already committed, so the row is durable.
        notify_created(obj)
        return obj
```

Every subclass of `NotifyBase` now fires `notify_created` after commit. For most post-commit side effects, prefer {meth}`after_commit <fastapi_restly.views.RestView.after_commit>` (see [transaction hooks](customize.md#transaction-hooks-before_commit--after_commit)); use a handler override when control flow must change.

## Inherit a shared dependency

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
    schema = NoteRead
```

`self.current_user` is available in every subclass method, and because {meth}`create <fastapi_restly.views.RestView.create>` runs before commit, stamping `owner_id` persists. The injection mechanism itself is described in [Dependency injection on class attributes](class_based_views.md#dependency-injection-on-class-attributes).

## Apply router-level dependencies to all routes

Setting `dependencies = [Depends(fn)]` applies `fn` to every route, and subclasses inherit it:

```python
class ProtectedBase(fr.RestView):
    dependencies = [Depends(require_auth)]

@fr.include_view(app)
class UserView(ProtectedBase):
    prefix = "/users"
    model = User
    schema = UserRead

@fr.include_view(app)
class OrderView(ProtectedBase):
    prefix = "/orders"
    model = Order
    schema = OrderRead
```

Every route on `/users/` and `/orders/` now requires authentication.

(prefix-concatenation)=

## Concatenate URL prefixes

When a base class defines {attr}`prefix <fastapi_restly.views.View.prefix>`, subclass prefixes are appended to it. This lets you declare a shared URL namespace once:

```python
class ApiV1(fr.RestView):
    prefix = "/api/v1"

@fr.include_view(app)
class UserView(ApiV1):
    prefix = "/users"     # becomes /api/v1/users
    model = User
    schema = UserRead

@fr.include_view(app)
class OrderView(ApiV1):
    prefix = "/orders"    # becomes /api/v1/orders
    model = Order
    schema = OrderRead
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
    schema = ReportRead
```

## Inherit custom routes

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
    schema = UserRead
```

`GET /users/health` is registered alongside the standard CRUD endpoints.

## Restrict available endpoints on a base class

Set {attr}`exclude_routes <fastapi_restly.views.BaseRestView.exclude_routes>` on a base class to make every subclass read-only, or to apply whatever restriction you need:

```python
class ReadOnlyBase(fr.RestView):
    exclude_routes = (fr.ViewRoute.CREATE, fr.ViewRoute.UPDATE, fr.ViewRoute.DELETE)

@fr.include_view(app)
class ProductView(ReadOnlyBase):
    prefix = "/products"
    model = Product
    schema = ProductRead
```

`ProductView` only exposes `GET /products/` and `GET /products/{id}`.

## Implement soft-delete once

A base class can override the {meth}`delete <fastapi_restly.views.RestView.delete>` business verb once for every subclass, exactly like the audit example above but with the soft-delete body. The canonical recipe, built on a `deleted_at` timestamp, lives in [Customize RestView](#soft-delete-recipe); the reusable mixin that also hides flagged rows on read is in [Compose Views with Mixins](howto_compose_views_with_mixins.md).

## Cross-references

The patterns above build on two neighbouring pages:

- [Customize RestView](customize.md) covers the three-tier model and the call chain.
- [Compose Views with Mixins](howto_compose_views_with_mixins.md) covers layering structural concerns cooperatively; it is the richer cousin to single-base inheritance.
