# Custom Routes

A CRUD view can have routes of its own next to its five CRUD routes, such as
`GET /users/me` or `POST /posts/{id}/publish`. Declare them with a route
decorator, as on any view; see [Declare routes](#declare-a-route). A custom
route calls the same methods as the CRUD routes, so it runs the same steps:
the scope, `authorize`, the save and the commit.

| Your route | Call |
|---|---|
| Reads one row | [`handle_get_one`](#custom-read-route) |
| Reads a list | [`handle_get_many`](#per-read-where) |
| Creates, updates or deletes on another path | [`handle_create`](#create-from-custom-route), [`handle_update`](#update-from-custom-route), [`handle_delete`](#delete-from-custom-route) |
| Makes any other change | [`write_action`](#custom-action-route) |
| Writes several rows in one commit | [`shared_write_action_commit`](#shared-write-action-commit) |

(custom-read-route)=
## Add a custom read route

To read one row, call
{meth}`handle_get_one <fastapi_restly.views.RestView.handle_get_one>`; see
[Read a row from a custom route](#get-one-from-custom-route). It loads the row
through the scope, so a row that the caller cannot see is a 404. Then it
calls `authorize("get_one", obj=row)`, as `GET /{id}` does.
{meth}`get_one <fastapi_restly.views.RestView.get_one>` alone also loads
through the scope, but it does not call `authorize`.

A read of your own starts from
{meth}`self.select() <fastapi_restly.views.BaseRestView.select>`: the view's
model with its scope and its loader options. So the rows are the ones that
the view shows, and each row loads what the response needs:

```python
    @fr.get("/recent")
    async def recent(self) -> list[ResponseSchema]:
        await self.authorize(fr.Action.GET_MANY)
        stmt = self.select().order_by(Post.created_at.desc()).limit(5)
        return list(await self.session.scalars(stmt))
```

`ResponseSchema` from `fastapi_restly.views` stands for the view's response
schema. The route declares a list of it, so Restly passes each row through
`to_single_response`, as the list route does; see
[The response of a custom route](#custom-route-response). The route builds
its own read, so it calls `authorize` itself.

:::{admonition} Details
:class: details

The handlers return the ORM object, so a route can read every attribute of
the model, also the ones that the response leaves out. In the SaaS example,
`GET /projects/{id}/stats` calls `handle_get_one(id)`, and then counts the
project's tasks
([`app/projects/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/projects/views.py)).
:::

(custom-route-other-key)=
### Read a row by another key

{meth}`handle_get_one <fastapi_restly.views.RestView.handle_get_one>` and
{meth}`get_one <fastapi_restly.views.RestView.get_one>` take a SQLAlchemy
boolean expression in place of the id. The expression picks the row instead
of the primary key. The view's [scope](#view-scope), the loader options and
the 404 stay the same:

```python
    @fr.get("/by-email/{email}")
    async def get_by_email(self, email: str) -> ResponseSchema:
        return await self.handle_get_one(User.email == email)
```

{meth}`handle_update <fastapi_restly.views.RestView.handle_update>` and
{meth}`handle_delete <fastapi_restly.views.RestView.handle_delete>` take the
same expression. So `PATCH /users/by-email/{email}` can call
`handle_update(User.email == email, data)`. The SaaS example reads and updates
projects by slug in
[`app/projects/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/projects/views.py),
next to the routes by id. To make every item route use another key, override
`where_id`; see [Find the row by another key](#natural-key-route).

(custom-action-route)=
## Add a custom action route

An action changes state: publish, archive, recalculate. Declare it with
{func}`@fr.post <fastapi_restly.views.post>`, or with
{func}`@fr.patch <fastapi_restly.views.patch>` or
{func}`@fr.delete <fastapi_restly.views.delete>`. Most actions take one of
four forms.

**Change one row in a `write_action` block.** Load the row with
{meth}`get_one <fastapi_restly.views.RestView.get_one>`. Then change it inside
{meth}`write_action <fastapi_restly.views.RestView.write_action>`, under the
name of the action. `obj` names the row that the action is about. The blog
example publishes a post:

```{literalinclude} ../example-projects/blog/blog/main.py
:language: python
:start-at: class Action(fr.Action):
:end-at: return post
```

The view's `Action` class lists its actions: the five CRUD actions of
{class}`fr.Action <fastapi_restly.views.Action>`, and `PUBLISH`. See
[Action names](#action-names).

When the block starts, Restly calls `authorize("publish", obj=post)` and takes
the snapshot of the row. With `data=`, it also checks the references in the
data, as a CRUD write does. When the block ends, Restly saves, as after a
business method: it flushes, refreshes `post` and loads it with the view's
loader options. Then it runs `before_action_commit`, commits, and runs
`after_action_commit`, each with the action name `"publish"`. So the block
only changes the row. If the block raises, as it does here for a post that is
already published, nothing is committed. The route loads with `get_one`, not
with `handle_get_one`, because `write_action` authorizes the action itself.
[Authorize a custom action](#authorize-custom-actions) shows how `authorize`
handles a new name.

The SaaS example uses this form for the task changes `POST /tasks/{id}/start`,
`complete` and `reopen`
([`app/tasks/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/tasks/views.py)),
and for `POST /users/{id}/change-password`
([`app/users/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/users/views.py)).

**Run a CRUD action under another path.** When the action is a create, an
update or a delete with another path or another body, call the handler:
{meth}`handle_create <fastapi_restly.views.RestView.handle_create>`,
{meth}`handle_update <fastapi_restly.views.RestView.handle_update>` or
{meth}`handle_delete <fastapi_restly.views.RestView.handle_delete>`. The
route then runs every step of that CRUD action, with `authorize` and the
commit. The page of each route has an example:
[Create](#create-from-custom-route), [Update](#update-from-custom-route) and
[Delete](#delete-from-custom-route).

**Create in a block of your own.** Some creates need more than `create` does,
such as a parent row and its child rows. Build the new object, and pass it to
`write_action` as `obj`:

```python
    @fr.post("/with-lines", status_code=201)
    async def create_with_lines(self, data: OrderWithLines) -> ResponseSchema:
        order = Order(customer_id=data.customer_id)
        async with self.write_action(self.Action.CREATE, obj=order, data=data):
            order.lines = [OrderLine(sku=line.sku) for line in data.lines]
        return order
```

The order is not in the database yet, so `authorize` gets `obj=None`, as on
`POST /`, and `old` is `None`. Pass the request as `data=`: then
`authorize` gets it, so a create rule that reads the payload works here too.
Restly checks the references in `data` before the block, as the create
handler does. Then it adds the order to the session. When the block ends, it
saves the order with the lines that its relationship holds, and the hooks
receive the order as `new`.

The route builds the order before the block, so before `authorize` and the
reference check. That is safe, because building an object writes nothing. But
set a reference to a loaded row by its key, as `customer_id=` above, or set it
inside the block. With `Order(customer=customer)` before the block, the loaded
customer holds the new order in its `orders`. The action begins with a flush,
and SQLAlchemy then warns that the order is not in the session. A check of the
built object, such as a rule about its lines, goes inside the block, where
`authorize` has already passed.

(write-several-rows)=
**Change several rows in one action.** Pass `obj=None`: the action has no
single row. When the block ends, Restly saves every row of the view's model
that the block changed. It reloads them together, with the view's loader
options, so each row comes back as the database has it:

```python
    class Action(fr.Action):
        REORDER = "reorder"

    @fr.put("/positions")
    async def reorder(self, items: list[CookbookPosition]) -> list[ResponseSchema]:
        cookbooks = []
        async with self.write_action(self.Action.REORDER, obj=None, data=items):
            for item in items:
                cookbook = await self.get_one(item.id)
                cookbooks.append(await self.update(cookbook, item))
        return cookbooks
```

`authorize` gets `obj=None`, once, and the hooks get `new=None` and
`old=None`. The block calls the business method `update`, so a change to it
applies here too. Restly checks the references in each schema of the list.
The reload runs one query for each 500 rows, also for rows that the route
does not return.

:::{admonition} Details
:class: details

A block that needs the id of the new row before it ends, for rows that refer
to it by a plain id, calls `await self.session.flush()`. Restly has added the
new object to the session, so the flush inserts it. The SaaS example's
`POST /uploads` does this for an upload and its lines, and
`POST /projects/{id}/clone` does it for a project and its copied tasks
([`app/uploads/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/uploads/views.py),
[`app/projects/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/projects/views.py)).
Rows of another model, such as the lines, are written but not refreshed.

When each row of several needs its own `authorize` call and its own hooks,
call the handler for each row inside
[`shared_write_action_commit()`](#shared-write-action-commit) instead. Or call
`authorize("update", obj=cookbook, data=item)` in the loop yourself.

A block does not take a list as `obj`. Inside the block, nothing is written
until it ends, so a query there does not see your changes. When the query
must see them, call `session.flush()` first.

After the block, `obj` has the relationships of the view's loader options,
and the ones that the model loads eagerly, such as with `lazy="selectin"`.
The save unloads the rest, so a changed foreign key never shows the old
related row. This holds also for a relationship that the
route loaded before the block. On an async view, read such a relationship
after the block with `await obj.awaitable_attrs.<name>`, or add it to
{meth}`get_loader_options <fastapi_restly.views.BaseRestView.get_loader_options>`.

Restly checks the references in `data`, also in the schemas nested in it and
in each item of a list; see [Check that the referenced row exists](#reference-exists).

The routes above declare `ResponseSchema`, or a list of it. So Restly sends
the rows that they return through the view's response step, and OpenAPI
shows the same types as for the CRUD routes; see
[The response of a custom route](#custom-route-response). A route
that builds a schema with `model_construct()` skips validation, so its
reference fields need the right form: see
[Build references in a custom route](#idref-custom-routes).
:::
