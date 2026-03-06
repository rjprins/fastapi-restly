# How-To: Override CRUD Behavior and Add Custom Endpoints

You can customize behavior by overriding `process_*` hooks or by defining custom routes on the same view.

## Override a Process Hook

```python
class UserView(fr.AsyncAlchemyView):
    prefix = "/users"
    model = User
    schema = UserSchema

    async def process_post(self, schema_obj):
        obj = await self.make_new_object(schema_obj)
        obj.created_by = "system"
        return await self.save_object(obj)
```

## Add a Custom Route

```python
@fr.include_view(app)
class UserView(fr.AsyncAlchemyView):
    ...

    @fr.get("/{id}/summary")
    async def summary(self, id: int):
        user = await self.process_get(id)
        return {"id": user.id, "name": user.name}
```

## Exclude Generated Routes

```python
class UserView(fr.AsyncAlchemyView):
    ...
    exclude_routes = ["delete"]
```

