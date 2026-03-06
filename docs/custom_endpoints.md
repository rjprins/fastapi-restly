# Custom Endpoints

Use custom endpoints when default CRUD routes are not enough.

This page shows the recommended FastAPI-Restly patterns:
- Keep generated CRUD routes for standard behavior.
- Add focused custom routes with `@fr.get`, `@fr.post`, etc.
- Override `process_*` hooks only when you need to change core CRUD behavior.

## Add a Read-Only Custom Route

```python
import fastapi_restly as fr


@fr.include_view(app)
class UserView(fr.AsyncAlchemyView):
    prefix = "/users"
    model = User
    schema = UserSchema

    @fr.get("/{id}/summary")
    async def summary(self, id: int):
        user = await self.process_get(id)
        return {"id": user.id, "name": user.name}
```

## Add a Custom Action Route

Use `@fr.post` for explicit actions (for example archive, publish, recalculate):

```python
@fr.include_view(app)
class OrderView(fr.AsyncAlchemyView):
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

## Override Generated CRUD Behavior

If you need to change built-in create/list/get/patch/delete behavior, override
`process_*` methods instead of rewriting route methods.

```python
@fr.include_view(app)
class UserView(fr.AsyncAlchemyView):
    prefix = "/users"
    model = User
    schema = UserSchema

    async def process_post(self, schema_obj):
        obj = await self.make_new_object(schema_obj)
        obj.created_by = "system"
        return await self.save_object(obj)
```

## Hide Generated Routes You Don't Want

```python
@fr.include_view(app)
class UserView(fr.AsyncAlchemyView):
    prefix = "/users"
    model = User
    schema = UserSchema
    exclude_routes = ["delete", "patch"]
```

## When to Use `@fr.route(...)`

Use `@fr.route(...)` only when you need full manual control over route options.
For normal endpoints, prefer `@fr.get`, `@fr.post`, `@fr.patch`, `@fr.delete`.
