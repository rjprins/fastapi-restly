# How Overrides Work: The Three Tiers

Every CRUD verb in FastAPI-Restly exists at three tiers of abstraction. To
customize a verb, name the tier that owns your change, override one method,
and leave the rest alone. This page describes the tier model, the read and
write lifecycles, and how to choose an override point.

## The three tiers

For a verb like {meth}`create <fastapi_restly.views.RestView.create>`, the same operation exists at three levels of
abstraction. From the wire inward:

```
POST /                       ← the route shell
  └─ create_endpoint(...)    1. WIRE: @route, FastAPI signature, response_model, to_response
       └─ handle_create(...) 2. REQUEST LOGIC: authorize + commit bracket
            └─ create(...)   3. DOMAIN: build the object, save it (auth-free, commit-free)
```

1. The **route shell** is the wire boundary: the {func}`@route <fastapi_restly.views.route>` decorator,
   the FastAPI signature, `response_model`, and {meth}`to_response <fastapi_restly.views.BaseRestView.to_response>`. Override it only to
   change the HTTP contract.

   - {meth}`get_many_endpoint <fastapi_restly.views.RestView.get_many_endpoint>`
   - {meth}`get_one_endpoint <fastapi_restly.views.RestView.get_one_endpoint>`
   - {meth}`create_endpoint <fastapi_restly.views.RestView.create_endpoint>`
   - {meth}`update_endpoint <fastapi_restly.views.RestView.update_endpoint>`
   - {meth}`delete_endpoint <fastapi_restly.views.RestView.delete_endpoint>`

2. The **request handler** holds the request logic: it runs {meth}`authorize <fastapi_restly.views.RestView.authorize>`,
   owns the commit bracket, and returns the domain object. Override it to change
   orchestration or timing without re-declaring the route.

   - {meth}`handle_get_many <fastapi_restly.views.RestView.handle_get_many>`
   - {meth}`handle_get_one <fastapi_restly.views.RestView.handle_get_one>`
   - {meth}`handle_create <fastapi_restly.views.RestView.handle_create>`
   - {meth}`handle_update <fastapi_restly.views.RestView.handle_update>`
   - {meth}`handle_delete <fastapi_restly.views.RestView.handle_delete>`

3. The **business verb** is the domain operation: it builds, applies, and
   saves, and it is both auth-free and commit-free. This is the usual override
   point.

   - {meth}`get_many <fastapi_restly.views.RestView.get_many>`
   - {meth}`get_one <fastapi_restly.views.RestView.get_one>`
   - `create`
   - {meth}`update <fastapi_restly.views.RestView.update>`
   - {meth}`delete <fastapi_restly.views.RestView.delete>`

The handler owns the commit, not the business verb. Because `create` never
commits, an override can build an object, stamp a `password_hash`, call
`save_object`, and return; the handler commits afterward.

## Request lifecycle: a write (`create`)

A `POST /` request flows down through the tiers, and the commit happens at the
bottom of the handler, after your domain logic has run:

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

{meth}`update <fastapi_restly.views.RestView.update>` and {meth}`delete <fastapi_restly.views.RestView.delete>` follow the same shape. Their handlers first load the row
through {meth}`get_one <fastapi_restly.views.RestView.get_one>` (so they 404 on a hidden row), take a {meth}`snapshot(obj) <fastapi_restly.views.BaseRestView.snapshot>` as
`old`, run the business verb, and then run the same bracket of {meth}`before_commit <fastapi_restly.views.RestView.before_commit>`,
commit, and {meth}`after_commit <fastapi_restly.views.RestView.after_commit>`, with both `new` and `old` available for dirty
detection. Recipes for these hooks are collected in
[Transaction hooks](howto_override_endpoints.md#transaction-hooks-before_commit--after_commit).

## Request lifecycle: a read (`get_one`)

Reads have no commit bracket. Read access instead involves two separate
concerns, **visibility** and **policy**, and each is handled at a different
tier:

```
GET /{id}
  └─ get_one_endpoint(id)            # route shell (wire)
       └─ handle_get_one(id)         # request handler
            ├─ get_one(id)           # business verb
            │    └─ build_query()    # VISIBILITY: scope (tenant, soft-delete, row-level)
            │                        #   a hidden row is a clean 404 for every caller
            └─ authorize("get_one", obj=obj)   # POLICY: read-auth on the loaded row
       └─ to_response(obj)
```

Because {meth}`get_one <fastapi_restly.views.RestView.get_one>` routes through {meth}`build_query <fastapi_restly.views.RestView.build_query>`, visibility lives in one
place across list, count, and single-row reads: a hidden row returns 404 from
`GET /{id}`. `get_one` itself stays auth-free; {meth}`authorize <fastapi_restly.views.RestView.authorize>` handles policy.
Scoping recipes are shown in
[Scope every read at once](howto_override_endpoints.md#build_query-scope-every-read-at-once).

{meth}`get_many <fastapi_restly.views.RestView.get_many>` works the same way: {meth}`build_query <fastapi_restly.views.RestView.build_query>` establishes the scope,
{meth}`apply_query_params <fastapi_restly.views.RestView.apply_query_params>` applies filtering, sorting, and pagination, and
{meth}`count <fastapi_restly.views.RestView.count>` produces the total, with `authorize("get_many")` added by
{meth}`handle_get_many <fastapi_restly.views.RestView.handle_get_many>`.

## Which method do I override for X?

The table below maps the change you want to make to the method that owns it:

| I want to change…                       | Override / configure              | Tier / kind            |
|-----------------------------------------|-----------------------------------|------------------------|
| Domain logic (hash, derive, compute)    | {meth}`create <fastapi_restly.views.RestView.create>` / {meth}`update <fastapi_restly.views.RestView.update>` / {meth}`delete <fastapi_restly.views.RestView.delete>`    | business verb          |
| Orchestration, timing, transaction      | `handle_<verb>`                   | request handler        |
| The HTTP contract (status, signature)   | `<verb>_endpoint`                 | route shell (wire)     |
| Read scope / row visibility             | {meth}`build_query <fastapi_restly.views.RestView.build_query>`                     | read extension point   |
| Filter / sort / pagination grammar      | {meth}`apply_query_params <fastapi_restly.views.RestView.apply_query_params>`              | read extension point   |
| The list total                          | {meth}`count <fastapi_restly.views.RestView.count>`                           | read extension point   |
| Authorization / policy                  | {meth}`authorize <fastapi_restly.views.RestView.authorize>` (override to gate)    | request-logic hook     |
| Server-stamped fields (audit/tenant)    | `make_new_object` / `update_object` (override cooperatively) | cooperative stamping  |
| In-transaction side effects             | {meth}`before_commit <fastapi_restly.views.RestView.before_commit>`                   | transaction hook       |
| Post-commit side effects (email/webhook)| {meth}`after_commit <fastapi_restly.views.RestView.after_commit>`                    | transaction hook       |
| The response shape                      | {meth}`to_response <fastapi_restly.views.BaseRestView.to_response>`                     | wire boundary          |

Start at the business verb. Move to `handle_<verb>` only when timing or
transaction handling must change, and touch the route shell only when the HTTP
contract itself changes.

## Worked example: hash a password on create

Hashing a password is domain logic, so it belongs in {meth}`create <fastapi_restly.views.RestView.create>`. The handler
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

{meth}`handle_create <fastapi_restly.views.RestView.handle_create>` still authorizes and runs the commit bracket. The override only
changes the domain step.

## Worked example: a custom action route

A non-CRUD route can reuse the same tiers, in one of two shapes. If the action
is just create, update, or delete under another URL, reuse the corresponding
`handle_<verb>`; if it has its own policy, event name, validation, or mutation
shape, use {meth}`write_action <fastapi_restly.views.RestView.write_action>`.

In the first shape, the action delegates to an existing handler. A `clone`
route can build a create payload and route it through {meth}`handle_create <fastapi_restly.views.RestView.handle_create>`:

```python
    @fr.post("/{id}/clone")
    async def clone(self, id: int):
        original = await self.handle_get_one(id)   # scope + 404 + read-auth
        payload = ArticleCreate(title=f"{original.title} (copy)")
        return self.to_response(await self.handle_create(payload))
```

In the second shape, the action has its own identity. `publish` is a state
transition, so it authorizes and fires hooks under the name `"publish"`:

```python
    @fr.post("/{id}/publish")
    async def publish(self, id: int):
        article = await self.handle_get_one(id)   # scope + 404 + read-auth
        async with self.write_action("publish", obj=article):
            article.status = "published"
        return self.to_response(article)
```

`__aenter__` runs authorization and the snapshot; `__aexit__` runs the commit
bracket, and a raised exception skips the commit. The response remains
{meth}`to_response(article) <fastapi_restly.views.BaseRestView.to_response>`: the action name drives authorization and hooks, while
the response only needs its wire shape. Create-shaped actions and more recipes
are collected in
[Add a custom action route](howto_override_endpoints.md#add-a-custom-action-route).
Internally, `write_action` and the CRUD handlers share {func}`run_write_action <fastapi_restly.views.run_write_action>`.

## The domain utilities

`save_object` and `delete_object` are utilities you call, not extension
points; `make_new_object` and `update_object` are both callable utilities and
cooperative override points for server-stamped fields (see the table above).
Together they build, apply, flush, and remove ORM objects without committing.
The same operations exist as free functions for workers and service code,
where the caller owns the transaction. The full table and a worked free-function example are in
[Domain utilities: call, don't override](howto_override_endpoints.md#domain-utilities-call-dont-override).

## Where to go next

The pages below go deeper into the tier model and its override points:

- [Class-Based Views](class_based_views.md): why subclassable views make all
  of this possible.
- [Override CRUD Behavior and Add Custom Endpoints](howto_override_endpoints.md):
  every override point in depth, with more examples.
- [API Reference](api_reference.md): the full view method surface and every
  public symbol.
