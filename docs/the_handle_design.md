# Overriding RestView behavior

A {class}`RestView <fastapi_restly.views.RestView>` or {class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` generates complete CRUD
endpoints, and sooner or later one of them needs to behave differently: stamp
a field server-side, archive instead of delete, scope every read to the
current tenant. Because the endpoint is generated, making such a change means
overriding a method, and which method depends on the kind of change. So
before mapping changes to methods, this page first explains what a RestView
does with a request; every override point follows from that structure.

The page covers the generated CRUD machinery only. A plain {class}`View <fastapi_restly.views.View>`
has no generated behavior to override; the class mechanics that all views
share are covered in [Class-Based Views](class_based_views.md).

:::{note}
Overriding changes routes the view already generates. To *add* routes
alongside them, no override is needed: declare a method with
{func}`@fr.get <fastapi_restly.views.get>` or {func}`@fr.post <fastapi_restly.views.post>`, as on any view
([What is a class-based view?](class_based_views.md#what-is-a-class-based-view)).
If the added route writes to
the database, the [custom action example](#worked-example-a-custom-action-route)
below shows how it plugs into the same machinery.
:::

## The three tiers

Take `POST /`, the create route, and follow the request inward. FastAPI calls
{meth}`create_endpoint <fastapi_restly.views.RestView.create_endpoint>`, which calls {meth}`handle_create <fastapi_restly.views.RestView.handle_create>`, which calls
{meth}`create <fastapi_restly.views.RestView.create>`. Every CRUD verb is built this way: three nested methods, each
owning one kind of concern.

```
POST /
  └─ create_endpoint(...)    1. the endpoint method: the HTTP contract
       └─ handle_create(...) 2. the handler: authorization and the commit
            └─ create(...)   3. the business method: the domain change
```

The rest of this page, and the how-to guides, lean on these three terms.

The **endpoint method**, `<verb>_endpoint`, is the method FastAPI routes to.
It owns the HTTP contract: the {func}`@route <fastapi_restly.views.route>` decorator, the FastAPI
signature, `response_model`, and the final {meth}`to_response <fastapi_restly.views.BaseRestView.to_response>` call. Override
it only when the contract itself must change.

The **handler**, `handle_<verb>`, owns the request logic in between. It runs
{meth}`authorize <fastapi_restly.views.RestView.authorize>`, calls the business method, and, on writes, closes with the
**commit bracket**: {meth}`before_commit <fastapi_restly.views.RestView.before_commit>`, then the commit itself, then
{meth}`after_commit <fastapi_restly.views.RestView.after_commit>`. It returns the domain object, so custom routes can
reuse it. Override it to change orchestration or timing without re-declaring
the route.

The **business method** is the bare verb: {meth}`create <fastapi_restly.views.RestView.create>`, {meth}`update <fastapi_restly.views.RestView.update>`,
{meth}`delete <fastapi_restly.views.RestView.delete>`, {meth}`get_one <fastapi_restly.views.RestView.get_one>`, or {meth}`get_many <fastapi_restly.views.RestView.get_many>`. It makes the domain change:
build, apply, save. It is deliberately auth-free and commit-free, which is
what makes it the usual override point: your code runs with authorization
already checked and with the commit still owned by the handler.

The method names are regular across all five verbs, so `update_endpoint`
calls `handle_update`, which calls `update`, and so on. The worked example
below leans on the commit split in particular.

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

## Request lifecycle: a write (`create`)

A `POST /` request flows down through the tiers, and the commit happens at the
bottom of the handler, after your domain logic has run:

```
POST /
  └─ create_endpoint(schema_obj)              # endpoint method
       └─ handle_create(schema_obj)           # handler
            ├─ authorize("create", data=schema_obj)
            ├─ create(schema_obj)              # business method (your override point)
            │    ├─ make_new_object(schema_obj)   # override to stamp extra fields
            │    └─ save_object(obj)              # flush + refresh (no commit)
            ├─ before_commit("create", new=obj)
            ├─ commit                             # the framework owns this
            └─ after_commit("create", new=obj)    # runs after durability
       └─ to_response(obj)                     # back in the endpoint method
```

{meth}`update <fastapi_restly.views.RestView.update>` and {meth}`delete <fastapi_restly.views.RestView.delete>` follow the same shape. Their handlers first load the row
through {meth}`get_one <fastapi_restly.views.RestView.get_one>` (so they 404 on a hidden row), take a {meth}`snapshot(obj) <fastapi_restly.views.BaseRestView.snapshot>` as
`old`, run the business method, and then run the same bracket of {meth}`before_commit <fastapi_restly.views.RestView.before_commit>`,
commit, and {meth}`after_commit <fastapi_restly.views.RestView.after_commit>`, with both `new` and `old` available for dirty
detection. Recipes for these hooks are collected in
[Transaction hooks](howto_override_endpoints.md#transaction-hooks-before_commit--after_commit).

## Request lifecycle: a read (`get_one`)

Reads have no commit bracket. Read access instead involves two separate
concerns, **visibility** and **policy**, and each is handled at a different
tier:

```
GET /{id}
  └─ get_one_endpoint(id)            # endpoint method
       └─ handle_get_one(id)         # handler
            ├─ get_one(id)           # business method
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
| Domain logic (hash, derive, compute)    | {meth}`create <fastapi_restly.views.RestView.create>` / {meth}`update <fastapi_restly.views.RestView.update>` / {meth}`delete <fastapi_restly.views.RestView.delete>`    | business method        |
| Orchestration, timing, transaction      | `handle_<verb>`                   | handler                |
| The HTTP contract (status, signature)   | `<verb>_endpoint`                 | endpoint method        |
| Read scope / row visibility             | {meth}`build_query <fastapi_restly.views.RestView.build_query>`                     | read extension point   |
| Filter / sort / pagination grammar      | {meth}`apply_query_params <fastapi_restly.views.RestView.apply_query_params>`              | read extension point   |
| The list total                          | {meth}`count <fastapi_restly.views.RestView.count>`                           | read extension point   |
| Authorization / policy                  | {meth}`authorize <fastapi_restly.views.RestView.authorize>` (override to gate)    | handler hook           |
| Server-stamped fields (audit/tenant)    | `make_new_object` / `update_object` (override cooperatively) | cooperative stamping  |
| In-transaction side effects             | {meth}`before_commit <fastapi_restly.views.RestView.before_commit>`                   | transaction hook       |
| Post-commit side effects (email/webhook)| {meth}`after_commit <fastapi_restly.views.RestView.after_commit>`                    | transaction hook       |
| The response shape                      | {meth}`to_response <fastapi_restly.views.BaseRestView.to_response>`                     | endpoint tier          |

Start at the business method. Move to `handle_<verb>` only when timing or
transaction handling must change, and touch the endpoint method only when the
HTTP contract itself changes.

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
the response is serialized like any other. Create-shaped actions and more recipes
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
