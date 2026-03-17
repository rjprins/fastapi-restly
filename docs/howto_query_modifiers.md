# How-To: Filter, Sort, and Paginate Lists

List endpoints (`GET /{prefix}/`) support query modifiers out of the box.

## V1 Style

```text
GET /users/?filter[name]=John
GET /users/?sort=-id
GET /users/?limit=20&offset=0
GET /users/?contains[email]=example
```

## V2 Style

```text
GET /users/?name=John
GET /users/?email__contains=example
GET /users/?order_by=-id
GET /users/?page=1&page_size=20
```

## Switch Active Version

```python
from fastapi_restly import QueryModifierVersion, set_query_modifier_version

set_query_modifier_version(QueryModifierVersion.V2)
```

Call `set_query_modifier_version(...)` before `@fr.include_view(...)` so the
generated query schema and runtime behavior stay aligned.

## Per-View Fixed Version

```python
class UserView(fr.AsyncAlchemyView):
    query_modifier_version = fr.QueryModifierVersion.V2
    ...
```

## Per-View Override Pattern

```python
class UserView(fr.AsyncAlchemyView):
    ...

    async def process_index(self, query_params, query=None):
        query = sqlalchemy.select(self.model).where(self.model.active.is_(True))
        return await super().process_index(query_params, query=query)
```
