# Patterns

Common scenarios and the idiomatic Restly answer to each. Entries are short on
purpose: the problem, the blessed shape, one example, and a link to the page
that owns the depth. Every example on this page runs against the current
release.

(patterns-nested-resources)=

## Nested resources (`/projects/{id}/tasks`)

Model the child as a **flat resource and filter by its foreign key** — the
filter parameter is generated automatically for `IDRef` fields:

```python
class TaskRead(fr.IDSchema):
    title: str
    project_id: fr.IDRef[Project]


@fr.include_view(app)
class TaskView(fr.AsyncRestView):
    prefix = "/tasks"
    model = Task
    schema = TaskRead
```

```text
GET /tasks/?project_id=17        # all tasks of one project
GET /tasks/?project_id__in=1,2   # of several
```

When the nested URL is part of your API contract, add a custom route on the
**parent** view, so parent scoping and 404 behavior come from the parent's
read path:

```python
import sqlalchemy as sa

class ProjectView(fr.AsyncRestView):
    prefix = "/projects"
    model = Project
    schema = ProjectRead

    @fr.get("/{id}/tasks", response_model=list[TaskRead])
    async def list_tasks(self, id: int):
        project = await self.handle_get_one(id)  # scope + 404 + read-auth
        query = sa.select(Task).where(Task.project_id == project.id)
        tasks = (await self.session.scalars(query)).all()
        return [TaskRead.model_validate(t, from_attributes=True) for t in tasks]
```

Depth: [Filter, Sort, and Paginate Lists](howto_query_modifiers.md) (the
filter grammar, including `#foreign-key-filtering`) and
[Override CRUD Behavior](howto_override_endpoints.md) (custom routes).

## A different schema for the list endpoint

There is no `schema_list` attribute. A different list shape is an HTTP-contract
change, so it belongs in the **route shell**: replace `get_many_endpoint` with
your own `response_model` and serialize through the slimmer schema. Filtering,
sorting, and pagination parameters keep working:

```python
class UserSummary(fr.IDSchema):
    name: str


class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserRead  # detail routes keep the full schema

    @fr.get("/", response_model=list[UserSummary])
    async def get_many_endpoint(self, query_params):
        result = await self.handle_get_many(query_params)
        return [
            UserSummary.model_validate(u, from_attributes=True)
            for u in result.objects
        ]
```

Depth: [Override CRUD Behavior → Tier 1](howto_override_endpoints.md).

## Restore a soft-deleted row

Soft delete hides rows in `build_query`, so every generated read 404s on them —
including the read your restore action needs. The restore route therefore
makes a **deliberately unscoped** query, then mutates inside `write_action` so
authorization and the commit bracket still run:

```python
class ItemView(fr.AsyncRestView):
    prefix = "/items"
    model = Item
    schema = ItemRead

    def build_query(self):
        return super().build_query().where(self.model.deleted_at.is_(None))

    async def delete(self, obj):
        obj.deleted_at = datetime.now(timezone.utc)

    @fr.post("/{id}/restore", response_model=ItemRead, status_code=200)
    async def restore(self, id: int):
        # The framework's read path calls build_query() with no arguments,
        # so the bypass is an explicit query here — visibly on purpose.
        query = sa.select(self.model).where(self.model.id == id)
        obj = (await self.session.scalars(query)).one_or_none()
        if obj is None:
            raise fr.exc.NotFound(f"Item {id!r} not found")
        async with self.write_action("restore", obj=obj):
            obj.deleted_at = None
        return self.to_response(obj)
```

Depth: soft delete itself is owned by
[Override CRUD Behavior](howto_override_endpoints.md) (one-off) and
[Compose Views with Mixins](howto_compose_views_with_mixins.md) (reusable
mixin + the admin-bypass discussion).

## Receive a webhook (inbound)

An inbound webhook receiver is not CRUD — use a bare `fr.View` with the raw
`Request`. Verify the signature before parsing, and **commit explicitly**: the
framework's auto-commit bracket only wraps `RestView` handlers, so a bare
`View` route owns its commit (the same contract as
`fr.open_async_session()`).

```python
from fastapi import Request

@fr.include_view(app)
class PaymentWebhookView(fr.View):
    prefix = "/webhooks"
    session: fr.AsyncSessionDep

    @fr.post("/payments", status_code=204)
    async def receive_payment_event(self, request: Request):
        payload = await request.body()
        verify_signature(payload, request.headers.get("X-Signature"))
        event = json.loads(payload)
        self.session.add(PaymentEvent(kind=event["type"], data=payload.decode()))
        await self.session.commit()  # a bare View owns its commit
```

(For *outbound* webhooks — calling someone else after a write — use the
`after_commit` hook instead; see
[How Overrides Work](the_handle_design.md).)

Depth: [Class-Based Views → When to use `View`
directly](class_based_views.md#when-to-use-view-directly).

## An app-wide base view

Owned by [Class-Based Views → One base view for the whole
app](#app-wide-base-view) — declare `session`, `current_user`, and the rest of
your request context once on a bare `View` base; every endpoint group (CRUD
or not) subclasses it and reads from `self`.

## Login and other auth flows

Owned by [Class-Based Views → When to use `View`
directly](class_based_views.md#when-to-use-view-directly) — an `AuthView`
with `/login`, `/refresh`, and `/logout` routes is the worked example.

## Custom action routes (`POST /{id}/publish`)

Owned by [How Overrides Work → Worked example: a custom action
route](the_handle_design.md#worked-example-a-custom-action-route) — reuse
`handle_<verb>` when the action is CRUD under another URL; use
`write_action("publish", ...)` when it has its own identity.

## Tenant scoping

Owned by [Compose Views with Mixins](howto_compose_views_with_mixins.md) —
a `TenantScopedMixin` filters every read through `build_query` and stamps
writes cooperatively. The single-base-class variant is in
[Share Behaviour with Base Views](howto_inheritance.md).
