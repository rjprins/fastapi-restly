# How-To: Share Behaviour with Base Views

FastAPI-Restly views are plain Python classes. There are no decorator wrappers or metaclass tricks that would prevent normal inheritance from working. This means you can build base view classes that capture shared logic — CRUD overrides, dependencies, access control, URL namespaces — and reuse it across every view in your project without repetition.

## Share a CRUD override across multiple views

Override any `handle_*` handler on a base class and every subclass picks it up automatically:

```python
class AuditBase(fr.RestView):
    def handle_create(self, schema_obj):
        obj = super().handle_create(schema_obj)
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

`audit_log.record` is called on every `POST` to `/users/` and `/orders/` without repeating the override. The base class itself is never registered — only the concrete subclasses are passed to `include_view`.

## Call super() to layer overrides

A subclass can override a `handle_*` handler and call `super()` to build on top of the base implementation rather than replace it:

```python
class AuditBase(fr.RestView):
    def handle_create(self, schema_obj):
        obj = super().handle_create(schema_obj)
        audit_log.record("created", obj)
        return obj

@fr.include_view(app)
class OrderView(AuditBase):
    prefix = "/orders"
    model = Order
    schema = OrderRead

    def handle_create(self, schema_obj):
        schema_obj.created_by = current_user()
        return super().handle_create(schema_obj)
```

The call chain is `OrderView.handle_create` → `AuditBase.handle_create` → `RestView.handle_create`. All three layers run in order.

## Inherit a shared dependency

Dependencies declared as instance annotations on a base class are injected into every subclass. This is a clean way to make the current user, tenant, or request context available to all views without repeating the annotation.

```python
from typing import Annotated
from fastapi import Depends

class AuthBase(fr.RestView):
    current_user: Annotated[User, Depends(get_current_user)]

    def handle_create(self, schema_obj):
        schema_obj.owner_id = self.current_user.id
        return super().handle_create(schema_obj)

@fr.include_view(app)
class NoteView(AuthBase):
    prefix = "/notes"
    model = Note
    schema = NoteRead
```

`self.current_user` is available in every method of `NoteView` and any other subclass of `AuthBase`.

## Apply router-level dependencies to all routes

`dependencies = [Depends(fn)]` on a view applies `fn` to every route the view registers. Subclasses inherit this, so you can enforce authentication or rate-limiting once on a base class:

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

## Concatenate URL prefixes

When a base class defines `prefix`, subclass prefixes are appended to it. This lets you declare a shared URL namespace once:

```python
class ApiV1(fr.RestView):
    prefix = "/api/v1"

@fr.include_view(app)
class UserView(ApiV1):
    prefix = "/users"     # → /api/v1/users
    model = User
    schema = UserRead

@fr.include_view(app)
class OrderView(ApiV1):
    prefix = "/orders"    # → /api/v1/orders
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
    prefix = "/reports"   # → /admin/v2/reports
    model = Report
    schema = ReportRead
```

## Inherit custom routes

Custom routes defined with `@fr.get`, `@fr.post`, etc. on a base class are inherited by all registered subclasses:

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

Set `exclude_routes` on a base class to make every subclass read-only (or whatever restriction you need):

```python
class ReadOnlyBase(fr.RestView):
    exclude_routes = ("post", "patch", "delete")

@fr.include_view(app)
class ProductView(ReadOnlyBase):
    prefix = "/products"
    model = Product
    schema = ProductRead
```

`ProductView` only exposes `GET /products/` and `GET /products/{id}`.

## Implement soft-delete once

Override `delete_object` on a base class to change how deletion works for every subclass:

```python
class SoftDeleteBase(fr.RestView):
    def delete_object(self, obj):
        obj.deleted = True
        self.session.flush()

@fr.include_view(app)
class ArticleView(SoftDeleteBase):
    prefix = "/articles"
    model = Article
    schema = ArticleRead
```

`DELETE /articles/{id}` now sets `deleted = True` instead of removing the row. Every subclass of `SoftDeleteBase` gets this behaviour.
