# How Overrides Work: The Three Tiers

Every CRUD verb in FastAPI-Restly has **three tiers**. Name the tier that owns
your change, override one method, and leave the rest alone.

This page covers the model, the read/write lifecycle, and the override lookup.

## The three tiers

For a verb like `create`, the same operation exists at three levels of
abstraction. From the wire inward:

```
POST /                       ← the route shell
  └─ create_endpoint(...)    1. WIRE: @route, FastAPI signature, response_model, to_response
       └─ handle_create(...) 2. REQUEST LOGIC: authorize + commit bracket
            └─ create(...)   3. DOMAIN: build the object, save it — auth-free, commit-free
```

1. **Route shell** — `get_many_endpoint`, `get_one_endpoint`,
   `create_endpoint`, `update_endpoint`, `delete_endpoint`. This is the **wire
   boundary**: the `@route` decorator, FastAPI signature, `response_model`, and
   `to_response`. Override it only to change the HTTP contract.

2. **Request handler** — `handle_get_many`, `handle_get_one`, `handle_create`,
   `handle_update`, `handle_delete`. This is the **request logic**: it runs
   `authorize`, owns the commit bracket, and returns the domain object. Override
   it to change orchestration or timing without re-declaring the route.

3. **Business verb** — `get_many`, `get_one`, `create`, `update`, `delete`.
   This is the **domain operation**: build, apply, save. It is **auth-free** and
   **commit-free**. This is the usual override point.

The handler owns the commit, not the business verb. Because `create` never
commits, an override can build an object, stamp a `password_hash`, call
`save_object`, and return; the handler commits afterward.

## Request lifecycle: a write (`create`)

`POST /` flows down through the tiers and the commit happens at the bottom of
the handler, after your domain logic has run:

```
POST /
  └─ create_endpoint(schema_obj)              # route shell (wire)
       └─ handle_create(schema_obj)           # request handler
            ├─ authorize("create", data=schema_obj)
            ├─ create(schema_obj)              # business verb (your override point)
            │    ├─ make_new_object(schema_obj)   # override to stamp extra fields
            │    └─ save_object(obj)              # flush + refresh (no commit)
            ├─ before_commit("create", new=obj)
            ├─ commit                             # the framework owns this
            └─ after_commit("create", new=obj)    # runs after durability
       └─ to_response(obj)                     # back at the wire boundary (single)
```

`update` and `delete` follow the same shape. Their handlers first load the row
through `get_one` (so they 404 on a hidden row), take a `snapshot(obj)` as
`old`, run the business verb, then run the same `before_commit` → commit →
`after_commit` bracket with both `new` and `old` available for dirty detection.

## Request lifecycle: a read (`get_one`)

Reads have no commit bracket. Read access is two separate concerns —
**visibility** and **policy** — handled at two different tiers:

```
GET /{id}
  └─ get_one_endpoint(id)            # route shell (wire)
       └─ handle_get_one(id)         # request handler
            ├─ get_one(id)           # business verb
            │    └─ build_query()    # VISIBILITY: scope (tenant, soft-delete, row-level)
            │                        #   → a hidden row is a clean 404 for every caller
            └─ authorize("get_one", obj=obj)   # POLICY: read-auth on the loaded row
       └─ to_response(obj)
```

Because `get_one` routes through `build_query`, **visibility lives in one
place** across list, count, and single-row reads. A hidden row returns 404 from
`GET /{id}`. `get_one` stays auth-free; `authorize` handles policy.

`get_many` works the same way: `build_query` (scope) → `apply_query_params`
(filter/sort/page) → `count`, with `authorize("get_many")` added by
`handle_get_many`.

## Which method do I override for X?

| I want to change…                       | Override / configure              | Tier / kind            |
|-----------------------------------------|-----------------------------------|------------------------|
| Domain logic (hash, derive, compute)    | `create` / `update` / `delete`    | business verb          |
| Orchestration, timing, transaction      | `handle_<verb>`                   | request handler        |
| The HTTP contract (status, signature)   | `<verb>_endpoint`                 | route shell (wire)     |
| Read scope / row visibility             | `build_query`                     | read extension point   |
| Filter / sort / pagination grammar      | `apply_query_params`              | read extension point   |
| The list total                          | `count`                           | read extension point   |
| Authorization / policy                  | `authorize` (override to gate)    | request-logic hook     |
| Server-stamped fields (audit/tenant)    | `make_new_object` / `update_object` (override cooperatively) | cooperative stamping  |
| In-transaction side effects             | `before_commit`                   | transaction hook       |
| Post-commit side effects (email/webhook)| `after_commit`                    | transaction hook       |
| The response shape                      | `to_response`                     | wire boundary          |

A good rule: **start at the business verb.** Move to `handle_<verb>` only when
timing or transaction handling must change. Touch the route shell only for HTTP
contract changes.

## Worked example: hash a password on create

Hashing a password is domain logic, so it belongs in `create`. The handler
commits after this method returns:

```python
import fastapi_restly as fr

from .auth import hash_password
from .models import User
from .schemas import UserRead


@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserRead

    async def create(self, schema_obj):
        obj = await self.make_new_object(schema_obj)
        obj.password_hash = hash_password(schema_obj.password)
        return await self.save_object(obj)
```

`handle_create` still authorizes and runs the commit bracket. The override only
changes the domain step.

## Worked example: a custom action route

A non-CRUD route can reuse the same tiers. Pick one of two shapes:

> If the action is just create/update/delete under another URL, reuse
> `handle_<verb>`. If it has its own policy, event name, validation, or mutation
> shape, use `write_action`.

**Reuse a handler** when the action is create/update/delete under another URL.
A `clone` can route through `handle_create`:

```python
    @fr.post("/{id}/clone")
    async def clone(self, id: int):
        original = await self.handle_get_one(id)   # scope + 404 + read-auth
        payload = ArticleCreate(title=f"{original.title} (copy)")
        return self.to_response(await self.handle_create(payload))
```

**Use `write_action`** when the action has its own identity. `publish` is a
state transition, so it authorizes and fires hooks as `"publish"`:

```python
    @fr.post("/{id}/publish")
    async def publish(self, id: int):
        article = await self.handle_get_one(id)   # scope + 404 + read-auth
        async with self.write_action("publish", obj=article):
            article.status = "published"
        return self.to_response(article)
```

`__aenter__` runs authorization and snapshot; `__aexit__` runs the commit
bracket. A raised exception skips commit. The response remains
`to_response(article)`: the action name drives authorization and hooks, while
the response only needs its wire shape. Create-shaped actions and more recipes:
[Add a custom action route](howto_override_endpoints.md#add-a-custom-action-route).

`write_action` and the CRUD handlers share `run_write_action` internally.

## The domain utilities

`make_new_object`, `update_object`, `save_object`, and `delete_object` are
**utilities you call**, not extension points. They build, apply, flush, and
remove ORM objects without committing. The same operations exist as free
functions for workers and service code, where the caller owns the
transaction. The full table and a worked free-function example:
[Domain utilities — call, don't override](#domain-utilities).

## Where to go next

- [Class-Based Views](class_based_views.md) — why subclassable views make all
  of this possible.
- [Override CRUD Behavior and Add Custom Endpoints](howto_override_endpoints.md)
  — every override point in depth, with more examples.
- [API Reference](api_reference.md) — the full view method surface and every
  public symbol.
