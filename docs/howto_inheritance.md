# How-To: Share Behaviour with Base Views

FastAPI-Restly views are plain Python classes. There are no decorator wrappers or metaclass tricks that would prevent normal inheritance from working. This means you can build base view classes that capture shared logic — CRUD overrides, dependencies, access control, URL namespaces — and reuse it across every view in your project without repetition.

Each CRUD verb is three tiers (see [Override Endpoints](howto_override_endpoints.md) for the full model):

- The **route shell** (`create_endpoint`, `get_one_endpoint`, …) — the wire boundary. Rarely overridden on a base class.
- The **request handler** (`handle_create`, `handle_get_one`, …) — runs `authorize` and the commit bracket. Override on a base class to change orchestration for every subclass.
- The **business verb** (`create`, `get_one`, `update`, `delete`, `get_many`) — the auth-free, commit-free domain operation. This is the usual place to put shared logic.

The business verb is the natural home for shared behaviour, so most of the examples below override it.

## Share a CRUD override across multiple views

Override a business verb on a base class and every subclass picks it up automatically:

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

`audit_log.record` is called on every `POST` to `/users/` and `/orders/` without repeating the override. The base class itself is never registered — only the concrete subclasses are passed to `include_view`.

Because `create` is commit-free (the handler owns the commit), the recorded object is the same one that gets persisted — there is no risk of mutating after a flush has already happened.

## Call super() to layer overrides

A subclass can override a business verb and call `super()` to build on top of the base implementation rather than replace it:

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

The call chain is `OrderView.create` → `AuditBase.create` → `RestView.create`. All three layers run in order.

## Share an orchestration override

When the shared behaviour is about *timing* rather than the domain object itself — running a check before authorization, emitting an event after durability, wrapping the write in a custom transaction — override the request handler instead of the business verb. The handler keeps the route untouched while letting you reshape the orchestration:

```python
class NotifyBase(fr.RestView):
    def handle_create(self, schema_obj):
        obj = super().handle_create(schema_obj)
        # super().handle_create has already committed, so the row is durable.
        notify_created(obj)
        return obj
```

Every subclass of `NotifyBase` now fires `notify_created` only after the create has committed. For most after-the-fact side effects, prefer the `after_commit` hook; reach for a handler override when you need to change the surrounding control flow.

## Inherit a shared dependency

Dependencies declared as instance annotations on a base class are injected into every subclass. This is a clean way to make the current user, tenant, or request context available to all views without repeating the annotation.

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

`self.current_user` is available in every method of `NoteView` and any other subclass of `AuthBase`. Because `create` runs before the commit, stamping `owner_id` here persists correctly.

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
    exclude_routes = (fr.ViewRoute.CREATE, fr.ViewRoute.UPDATE, fr.ViewRoute.DELETE)

@fr.include_view(app)
class ProductView(ReadOnlyBase):
    prefix = "/products"
    model = Product
    schema = ProductRead
```

`ProductView` only exposes `GET /products/` and `GET /products/{id}`. The `ViewRoute` members name the route shells: `GET_MANY`, `GET_ONE`, `CREATE`, `UPDATE`, and `DELETE`.

## Implement soft-delete once

Override the `delete` business verb on a base class to change how deletion works for every subclass:

```python
class SoftDeleteBase(fr.RestView):
    def delete(self, obj):
        obj.deleted = True
        self.save_object(obj)

@fr.include_view(app)
class ArticleView(SoftDeleteBase):
    prefix = "/articles"
    model = Article
    schema = ArticleRead
```

`DELETE /articles/{id}` now sets `deleted = True` instead of removing the row. Every subclass of `SoftDeleteBase` gets this behaviour, and because `delete` is commit-free the flip is committed by the handler along with the rest of the request. Pair this with a `build_query` override that hides flagged rows so they disappear from reads as well — see [Compose Views with Mixins](howto_compose_views_with_mixins.md) for the full soft-delete pattern.

## Cross-references

- [Override Endpoints](howto_override_endpoints.md) — the three-tier model and the call chain.
- [Compose Views with Mixins](howto_compose_views_with_mixins.md) — layering structural concerns cooperatively, the richer cousin to single-base inheritance.
