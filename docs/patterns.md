# Patterns

This page collects common scenarios and gives the idiomatic Restly answer to
each. Entries are short on purpose: each states the problem, shows the
recommended shape in one example, and links to the page that owns the depth.
Every example on this page runs against the current release.

(patterns-nested-resources)=

## Nested resources (`/projects/{id}/tasks`)

To expose child rows under a parent, model the child as a flat resource and
filter by its foreign key; the filter parameter is generated automatically for
{class}`MustExist <fastapi_restly.schemas.MustExist>` fields:

```python
class TaskRead(fr.IDSchema):
    title: str
    project_id: fr.MustExist[int, Project]


@fr.include_view(app)
class TaskView(fr.AsyncRestView):
    prefix = "/tasks"
    model = Task
    schema = TaskRead
```

Clients then scope the list through the query string:

```text
GET /tasks/?project_id=17        # all tasks of one project
GET /tasks/?project_id__in=1,2   # tasks of several projects
```

When the nested URL is part of your API contract, add a custom route on the
*parent* view, so that parent scoping and 404 behavior come from the parent's
read path:

```python
import sqlalchemy as sa

class ProjectView(fr.AsyncRestView):
    prefix = "/projects"
    model = Project
    schema = ProjectRead

    @fr.get("/{id}/tasks", response_model=list[TaskRead])
    async def list_tasks(self, id: int):
        project = await self.handle_get_one(id)  # scoping, 404, and read-auth
        query = sa.select(Task).where(Task.project_id == project.id)
        tasks = (await self.session.scalars(query)).all()
        return [TaskRead.model_validate(t, from_attributes=True) for t in tasks]
```

The filter grammar, including
[foreign-key filtering](howto_query_modifiers.md#foreign-key-filtering), is
documented in [Filter, Sort, and Paginate Lists](howto_query_modifiers.md);
custom routes are covered in
[Customize RestView](customize.md).

## A different schema for the list endpoint

There is no `schema_list` attribute. A different list shape is an
HTTP-contract change, so it belongs in the route shell: replace
{meth}`get_many_endpoint <fastapi_restly.views.RestView.get_many_endpoint>`
with your own `response_model` and serialize through the slimmer schema.
Filtering, sorting, and pagination parameters keep working:

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

Replacing an endpoint method is the outermost override; see
[Replace an endpoint method to change the HTTP contract](customize.md#replace-an-endpoint-method-to-change-the-http-contract).

## Restore a soft-deleted row

Soft delete hides rows in {meth}`build_query <fastapi_restly.views.RestView.build_query>`,
so every generated read returns 404 for them, including the read your restore
action needs. The restore route therefore makes a deliberately unscoped
query, then mutates inside {meth}`write_action <fastapi_restly.views.RestView.write_action>`
so that authorization and the commit bracket still run:

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
        # so the bypass is an explicit query here, visibly on purpose.
        query = sa.select(self.model).where(self.model.id == id)
        obj = (await self.session.scalars(query)).one_or_none()
        if obj is None:
            raise fr.exc.NotFound(f"Item {id!r} not found")
        async with self.write_action("restore", obj=obj):
            obj.deleted_at = None
        return self.to_response(obj)
```

Soft delete itself is covered as a one-off override in
[Customize RestView](customize.md#delete-soft-delete-instead-of-removing-the-row)
and as a reusable mixin in
[Compose Views with Mixins](howto_compose_views_with_mixins.md#softdeletemixin-hide-deleted-rows),
which also discusses the admin bypass.

## Receive a webhook (inbound)

An inbound webhook receiver is not CRUD, so use a bare
{class}`fr.View <fastapi_restly.views.View>` with the raw `Request`. Verify
the signature before parsing, and commit explicitly: the framework's
auto-commit bracket only wraps {class}`RestView <fastapi_restly.views.RestView>`
handlers, so a bare `View` route owns its commit (the same contract as
{func}`fr.open_async_session() <fastapi_restly.db.open_async_session>`).

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

For *outbound* webhooks (calling someone else after a write), use the
{meth}`after_commit <fastapi_restly.views.RestView.after_commit>` hook
instead; see [Customize RestView](customize.md).

The decision between `View` and `RestView` is covered in
[When to use `View` directly](class_based_views.md#when-to-use-view-directly).

## An app-wide base view

Declare `session`, `current_user`, and the rest of your request context once
on a bare {class}`View <fastapi_restly.views.View>` base; every endpoint group
(CRUD or not) subclasses it and reads from `self`. This pattern is owned by
[One base view for the whole app](class_based_views.md#one-base-view-for-the-whole-app)
in Class-Based Views.

## Login and other auth flows

An `AuthView` with `/login`, `/refresh`, and `/logout` routes is the worked
example in
[When to use `View` directly](class_based_views.md#when-to-use-view-directly),
which owns this pattern.

## Custom action routes (`POST /{id}/publish`)

Reuse `handle_<verb>` when the action is CRUD under another URL; use
{meth}`write_action("publish", ...) <fastapi_restly.views.RestView.write_action>`
when the action has its own identity. The full walkthrough is
[Add a custom action route](customize.md#add-a-custom-action-route)
in Customize RestView, which owns this pattern.

## Tenant scoping

A `TenantScopedMixin` filters every read through
{meth}`build_query <fastapi_restly.views.RestView.build_query>` and stamps
writes cooperatively; the pattern is owned by
[`TenantScopedMixin` in Compose Views with Mixins](howto_compose_views_with_mixins.md#tenantscopedmixin-multi-tenant-row-scoping).
The single-base-class variant is in
[Share Behaviour with Base Views](howto_inheritance.md).
