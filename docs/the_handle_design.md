# The handle design

Every CRUD verb in FastAPI-Restly is built from **three tiers**. Once you can
name the tier you want to change, overriding behavior stops being guesswork: you
reach for one method, leave the other two alone, and the rest of the framework
keeps working around your change.

This page teaches that mental model, walks the request lifecycle for a write and
a read, and gives you a single "which method do I override for X?" lookup table.

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
   boundary**: the `@route` decorator, the FastAPI signature and
   `response_model`, and the call to `to_response`. You rarely override this —
   only to renegotiate the HTTP contract (a different status code, an envelope,
   custom headers).

2. **Request handler** — `handle_get_many`, `handle_get_one`, `handle_create`,
   `handle_update`, `handle_delete`. This is the **request logic**: it runs
   `authorize` and owns the commit bracket (`before_commit` → commit →
   `after_commit`), then returns the domain object. Override it to change
   orchestration or timing — a custom transaction, an async delete — *without*
   re-declaring the route. Reuse it from custom actions when you want the same
   commit bracket.

3. **Business verb** — `get_many`, `get_one`, `create`, `update`, `delete`.
   This is the **domain operation**: build, apply, save. It is **auth-free** and
   **commit-free**. This is the usual override point — hash a password, derive a
   slug, compute a field.

The framework owns the commit *inside* the handler, not inside the business
verb. That single fact is what makes overrides safe: because `create` never
commits, an override that builds an object, stamps a `password_hash`, calls
`save_object`, and returns persists correctly. The old "mutate-after-save" trap
— where a field set after the commit silently never reached the database — is
gone, because in this design there is no commit to come after.

## Request lifecycle: a write (`create`)

`POST /` flows down through the tiers and the commit happens at the bottom of
the handler, after your domain logic has run:

```
POST /
  └─ create_endpoint(schema_obj)              # route shell (wire)
       └─ handle_create(schema_obj)           # request handler
            ├─ authorize("create", data=schema_obj)
            ├─ create(schema_obj)              # business verb (your override point)
            │    ├─ make_new_object(schema_obj)   # → prepare_create stamps extra fields
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
place** and is consistent across every read — list, count, and single fetch. A
row hidden by the scope cannot be fetched directly via `GET /{id}`; it is a 404,
not a 403, and you never had to remember to re-check. `get_one` itself stays
auth-free; the *policy* check (can this user read this visible row?) is added by
the handler through `authorize`.

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
| Authorization / policy                  | `authorize` + `permissions` dict  | request-logic hook     |
| Server-stamped fields (audit/tenant)    | `prepare_create` / `prepare_update` | cooperative stamping  |
| In-transaction side effects             | `before_commit`                   | transaction hook       |
| Post-commit side effects (email/webhook)| `after_commit`                    | transaction hook       |
| The response shape                      | `to_response`                     | wire boundary          |

A good rule of thumb: **start at the business verb.** Most real changes are
domain logic and belong there. Only move up to `handle_<verb>` when you need to
change *when* something happens relative to the commit, and only touch the route
shell when the HTTP contract on the wire genuinely has to change.

## Worked example: hash a password on create

Hashing a password is pure domain logic, so it goes in the business verb. The
business verb does not commit, so building the object, setting the hash, saving,
and returning persists correctly:

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

`handle_create` still wraps this: it authorizes first, then runs your `create`,
then runs the `before_commit` → commit → `after_commit` bracket. You did not
touch the route, the authorization, or the transaction — only the domain step.

## Worked example: a custom action route

A non-CRUD action — `POST /{id}/publish` — composes the tiers you already have.
The shortest form reuses a write path, so the action inherits `authorize`, the
commit bracket, and the `before_commit` / `after_commit` side effects:

```python
    @fr.post("/{id}/publish")
    async def publish(self, id: int):
        # handle_update loads through get_one (scope + 404), authorizes, and commits.
        return await self.handle_update(id, ArticleUpdate(status="published"))
```

If you want the full write orchestration for the action, route through
`handle_update` / `handle_create` as above — you get `authorize`, the commit
bracket, and `before_commit` / `after_commit` for free. If you instead need a
bespoke step that is neither a plain create nor update, give the action its own
name and bracket it with `self.write_action(...)` — a context manager that runs
the same lifecycle the CRUD handlers do:

```python
    @fr.post("/{id}/publish")
    async def publish(self, id: int):
        article = await self.handle_get_one(id)   # scope + 404 + read-auth
        async with self.write_action("publish", obj=article):
            article.status = "published"
        return self.to_response(article)
```

`__aenter__` runs `authorize(action, obj, data)` + `snapshot`; your inline body
mutates; `__aexit__` runs `before_commit` → commit → `after_commit` (a raise in
the body skips the commit). You never call `_commit()` by hand or reassemble the
steps. Note the response is simply `to_response(article)`: the write *action*
(`"publish"`) drives authorization and the commit hooks, while the response only
needs the wire *shape* (`single`, the default) — they are separate concerns, so
you never pass the action name to `to_response`. For a **create-shaped** action —
where the object does not exist until the body runs — deposit it on the yielded
handle and read it back:

```python
    async with self.write_action("create", data=req) as w:
        w.obj = await self.make_new_object(req)
    return self.to_response(w.obj)
```

Under the hood `write_action` and the CRUD handlers share one implementation:
the self-free function `fr.run_write_action`.

## The domain utilities

`make_new_object`, `update_object`, `save_object`, and `delete_object` are
**utilities you call**, not extension points you override. They build, apply,
flush, and remove ORM objects without committing, which is why they compose
cleanly inside custom actions (as above) and inside business-verb overrides.

The same operations exist as free functions —
`fr.async_make_new_object`, `fr.async_update_object`, `fr.async_save_object`,
`fr.async_delete_object` (and their sync counterparts without the `async_`
prefix) — so a background worker or service layer outside a view can run the
exact same persistence logic:

```python
from fastapi_restly import async_make_new_object, async_save_object


async def import_user(session, payload) -> User:
    obj = await async_make_new_object(session, User, payload, UserRead)
    return await async_save_object(session, obj)
```

These free functions do not commit either — the caller owns the transaction,
just as `handle_<verb>` does inside a view.

## Where to go next

- [Class-Based Views](class_based_views.md) — why subclassable views make all
  of this possible.
- [How-To: Override CRUD Behavior and Add Custom Endpoints](howto_override_endpoints.md)
  — every override point in depth, with more examples.
- [API Reference](api_reference.md) — the full view method surface and every
  public symbol.
