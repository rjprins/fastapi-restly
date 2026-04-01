# How-To: Override CRUD Behavior and Add Custom Endpoints

You can customize behavior by overriding `process_*` hooks or by defining custom routes on the same view. Every concrete view class must be decorated with `@fr.include_view(app)` or no routes will be registered.

## Override a Process Hook

Override any `process_*` method to change how a specific CRUD operation works. The available hooks are:

- `process_index(self, query_params, query=None)` — list endpoint (`GET /`). Pass `query` to supply a pre-filtered SQLAlchemy `Select` statement.
- `process_get(self, id)` — single-object endpoint (`GET /{id}`)
- `process_post(self, schema_obj)` — create endpoint (`POST /`)
- `process_patch(self, id, schema_obj)` — update endpoint (`PATCH /{id}`)
- `process_delete(self, id)` — delete endpoint (`DELETE /{id}`)
- `count_index(self, query_params)` — count query used when `include_pagination_metadata = True`. Override to apply the same filters as `process_index`.
- `delete_object(self, obj)` — low-level delete called by `process_delete`. Override to add soft-delete logic.

```python
import fastapi_restly as fr

@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserSchema

    async def process_post(self, schema_obj):
        obj = await self.make_new_object(schema_obj)
        obj.created_by = "system"  # requires a `created_by` column on User
        return await self.save_object(obj)
```

`self.make_new_object(schema_obj)` and `self.save_object(obj)` are instance methods on `AsyncRestView` (session-aware). They differ from the package-level free functions `fr.make_new_object(session, ...)` and `fr.save_object(session, ...)`, which take an explicit `session` argument.

## Add a Custom Route

Use `@fr.get`, `@fr.post`, `@fr.put`, `@fr.patch`, `@fr.delete`, or `@fr.route` to add endpoints alongside the generated ones.

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserSchema

    @fr.get("/{id}/summary")
    async def summary(self, id: int):
        user = await self.process_get(id)
        return {"id": user.id, "first_name": user.first_name, "email": user.email}
```

`process_get` returns the raw ORM object, so you can access model attributes directly.

## Add a Custom Action Route

Use `@fr.post` (or `@fr.patch`, `@fr.delete`) for explicit state-change actions such as archive, publish, or recalculate:

```python
@fr.include_view(app)
class OrderView(fr.AsyncRestView):
    prefix = "/orders"
    model = Order
    schema = OrderSchema

    @fr.post("/{id}/archive", status_code=202)
    async def archive(self, id: int):
        order = await self.process_get(id)
        order.archived = True
        await self.save_object(order)
        return {"id": order.id, "archived": order.archived}
```

## Exclude Generated Routes

Set `exclude_routes` to a tuple of route names to suppress specific generated endpoints.

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    exclude_routes = ("delete",)
```

Valid values are: `"index"`, `"get"`, `"post"`, `"patch"`, `"delete"`. Passing any other string raises `AttributeError` at startup.

## Choosing Between `@fr.route` and the Shorthand Decorators

Prefer `@fr.get`, `@fr.post`, `@fr.put`, `@fr.patch`, and `@fr.delete` for most endpoints — they set the HTTP method automatically, and `@fr.get` (200), `@fr.post` (201), and `@fr.delete` (204) also set default status codes. `@fr.put` and `@fr.patch` do not set a default; FastAPI uses 200.

Use `@fr.route(path, methods=[...], ...)` only when you need full manual control over route options (for example, to register a single path under multiple HTTP methods, or to set non-standard response codes).
