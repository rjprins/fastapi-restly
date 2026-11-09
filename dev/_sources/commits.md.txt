# Commits & Hooks

A write route commits once, at the end of its steps. This page explains what
commits and when, the two hooks around the commit, what happens on an error,
and how to commit several writes at once.
[How a CRUD route runs](#view-request-steps) shows where the commit comes
among the steps of a route.

(what-commits)=
## What commits, and when

Restly commits for you, once for each write action. The handler of a write
route, or a {meth}`write_action <fastapi_restly.views.RestView.write_action>`
block in a custom route, runs `before_action_commit`, commits, and then runs
`after_action_commit`. Your business methods and hooks do not commit. For code
that must commit, see
[Code that must commit in a write action](#atomic-write-actions).

The session that Restly opens for a request does not commit when the request
ends. It rolls back and closes. So a custom route that writes must commit
through a handler or a `write_action` block. A route on a plain
{class}`View <fastapi_restly.views.View>` has neither, so it commits the
session itself. When a request ends with changes
that are not committed, Restly warns with
{class}`RestlyUncommittedChangesWarning <fastapi_restly.exc.RestlyUncommittedChangesWarning>`.

To commit several writes at once, see
[Commit several writes together](#shared-write-action-commit).

:::{admonition} Details
:class: details

The warning is on by default. To turn it off for the app, pass
`warn_on_uncommitted=False` to {func}`fr.configure <fastapi_restly.db.configure>`.
A route that rolls back on purpose, such as a dry run, can turn it off for one
request with `session.info["_fr_suppress_uncommitted"] = True`.

A session generator of your own decides how a session is made and closed. It
does not change who commits, unless it commits itself, which it must not do;
see [Build sessions with your own generator](#own-session-generator).
:::

(transaction-hooks)=
## Run code before or after the commit

Every write action calls two hooks around its commit:

- {meth}`before_action_commit(action, new, old=None) <fastapi_restly.views.RestView.before_action_commit>`
  runs inside the transaction, before the commit. What it writes is committed
  together with the change, or not at all. Use it for outbox rows and audit
  rows. A view with `atomic_write_actions = False` is different; see
  [Code that must commit in a write action](#atomic-write-actions).
- {meth}`after_action_commit(action, new, old=None) <fastapi_restly.views.RestView.after_action_commit>`
  runs after the commit, when the change is saved. Use it for email, webhooks
  and cache invalidation.

The hooks run for the CRUD writes and for every `write_action` block. Reads do
not call them, and neither do the `fr.objects` functions. The arguments are:

- `action`: the name of the action, such as `"create"`, or the name that a
  custom route gives `write_action`. See [Action names](#action-names).
- `new`: the row that the action is about, after the save. It is `None` when
  the action removed the row, as a delete does, or when the action has no
  single row. On a session that expires on commit, `snapshot(new)` in
  `after_action_commit` is empty, and Restly warns; see
  [Session factory defaults](#session-factory-defaults).
- `old`: a dict of that row's column values before the action, from
  {meth}`snapshot <fastapi_restly.views.BaseRestView.snapshot>`. It holds only
  the columns that were loaded. It is `None` when the row is new, as on a
  create, or when the action has no single row.

So `old` and `new` tell what happened: a new row has no `old`, a removed row
has no `new`, and a changed row has both. This is the same for the CRUD
handlers and for every `write_action` block. So a hook can check what changed
without a list of routes:

```python
    async def after_action_commit(self, action, new, old=None):
        if old and new and old["status"] != new.status:  # PATCH, archive, ...
            await notify_status_change(new.id, new.status)
```

Do not change `new` or the database in `after_action_commit`. The commit is
done, so the change is not saved. A change to `new` still shows in the
response of this request, which hides the problem. Make the change in the
business method or in `before_action_commit`.

:::{admonition} Details
:class: details

After a delete, `new` is `None` and `old` holds the columns of the removed
row. A [soft delete](#soft-delete-recipe) keeps the row, so `new` is the row
with its new timestamp. A delete that a worker finishes after the commit is
on the page [Delete](#delete-method).

`old` comes from the action, not from the database. A create always gives
`old=None`, also when your `create` returns a row that exists already, such
as a tag with the same name. A hook that reads `old is None` as "a new row"
is wrong on that path. To give the hooks the stored values, look the row up
in a custom route, and change it in a `write_action` block with the row as
`obj`.

Code that a route runs after its `write_action` block also runs after the
commit, but only when the block commits itself. Inside
[`shared_write_action_commit()`](#shared-write-action-commit), or inside the
block of another action, that code runs before the commit. A hook runs after
the commit in both cases. So work that reads the committed row, such as
starting a job, goes in `after_action_commit`.

Restly finds `new` from the state of the row after the flush, as SQLAlchemy
reports it. A row that the action deleted with `session.delete()` or with an
ORM statement such as `delete(Post)` is gone. A Core `delete()` on the table
does not tell the session, so the refresh of the row fails with SQLAlchemy's
`InvalidRequestError`. Delete the row that `obj` names through the ORM.
:::

(after-commit-background-task)=
### Run the work after the response

Work in `after_action_commit`, such as sending an email, makes the client
wait for it. To answer first, give the work to FastAPI's
{class}`BackgroundTasks <fastapi.BackgroundTasks>`. Declare it as a
[class attribute](#dependency-injection-on-class-attributes) of the view, and
add the task in the hook:

```python
from fastapi import BackgroundTasks


class OrderView(fr.AsyncRestView):
    ...
    background_tasks: BackgroundTasks

    async def after_action_commit(self, action, new, old=None):
        if action == self.Action.CREATE:
            self.background_tasks.add_task(send_confirmation_email, new.id)
```

FastAPI runs the task after it sends the response. The order is committed
then, so a task that opens its own session finds it. A task queue works the
same way: enqueue the job in `after_action_commit`, with the id of the row.
Do not enqueue it in `before_action_commit`: a worker can start the job
before the commit, when other sessions cannot see the row yet, and the commit
can still fail. A task that fails does not undo the write; see the next
section.

(after-action-commit-raises)=
### When `after_action_commit` raises

The write is already committed, so an exception from the hook cannot undo it.
The request still fails, and a client that tries again repeats the write. For
an effect that may fail without harm, catch and log the error in the hook:

```python
    async def after_action_commit(self, action, new, old=None):
        if action == self.Action.CREATE:
            try:
                await send_confirmation_email(new.id)
            except Exception:  # the order is saved; don't fail the request
                logger.exception("Confirmation email failed for order %s", new.id)
```

An effect that must happen belongs in an outbox row, written in
`before_action_commit`. A worker then delivers it and tries again when it
fails. Make the delivery safe to repeat, because a retry can deliver it twice.
FastAPI's `BackgroundTasks` does not replace an outbox: a crash loses the
task, and nothing tries it again.

(write-action-error)=
## When a write action raises

When a write action raises, Restly rolls back what the action changed, and
the session stays usable. So a route can catch the error of one write and go
on:

```python
    from fastapi import HTTPException

    @fr.post("/bulk", status_code=201)
    async def bulk_create(self, items: list[OrderCreate]) -> list[ResponseSchema]:
        orders = []
        for item in items:
            try:
                orders.append(await self.handle_create(item))
            except HTTPException:  # also fr.exc.Conflict, for a duplicate value
                continue
        return orders
```

Each order is committed on its own. On `fr.RestView`, use the sync handlers.
To save all orders in one commit, see
[Commit several writes together](#shared-write-action-commit).

:::{admonition} Details
:class: details

Each outermost write action runs in a savepoint, SQLAlchemy's
{meth}`session.begin_nested() <sqlalchemy.orm.Session.begin_nested>`. The
savepoint begins after `authorize`, the reference check and the snapshot, and
it ends at the commit. When the action raises before the commit, Restly rolls
back to the savepoint. This undoes only what the action changed. Rows that
the action did not change stay loaded, so async code can still read them.
Rows that it changed, such as the row of an update, are expired. In async
code, call `await session.refresh(obj)` before you read such a row. Inside a
`shared_write_action_commit()` block, or inside the block of another action,
an action has no savepoint of its own; see
[Commit several writes together](#shared-write-action-commit). A view with
`atomic_write_actions = False` has no savepoint; see
[Code that must commit in a write action](#atomic-write-actions).

A savepoint begins with a flush of what is pending in the session, as
`begin_nested()` always does. So an object that a route adds to the session
before the write action is written when the action begins, and an error in
the action does not undo it. This is also true for a new object that a route
appends to a collection of a loaded row. Give a new object to `write_action`
as `obj`, without `session.add()`, and connect it to loaded rows inside the
block: the action then adds it inside its savepoint.

An error from `after_action_commit` does not undo the order: the order is
saved. See [When `after_action_commit` raises](#after-action-commit-raises).
When the commit itself fails, for example on a deferred constraint, Restly
rolls back the whole transaction. This also expires the rows that were loaded
before. On SQLite with the default settings of the driver, the end of the
savepoint can already commit, just before the commit; see
[Transactions on SQLite](#sqlite-transactions).

The savepoint costs two more statements for each write: `SAVEPOINT` and
`RELEASE SAVEPOINT`.

SQLAlchemy calls the listeners of the session for a savepoint too:
`after_begin` when it begins, `before_commit` and `after_commit` when it ends,
and `after_rollback` when it rolls back. So `before_commit` and `after_commit`
run twice for each outermost write action: when the savepoint ends, and at the
commit. The first time is before the commit, which can still fail. A listener
that must run only at the commit returns early when
`session.in_nested_transaction()` is true. Work that must wait for the commit,
such as an email, belongs in `after_action_commit`. On SQLite, the savepoint
can also make `database is locked` come sooner; see
[Turn on WAL for SQLite](#sqlite-wal).
:::

(shared-write-action-commit)=
## Commit several writes together

A handler or a `write_action` block commits on its own, unless it runs
inside another `write_action` block, which then owns the commit. To commit
several of them at once, put them inside a
{meth}`shared_write_action_commit() <fastapi_restly.views.AsyncRestView.shared_write_action_commit>`
block. The outermost block commits once. Then the queued `after_action_commit`
hooks run:

```python
    @fr.post("/bulk", status_code=201)
    async def bulk_create(self, items: list[OrderCreate]) -> list[ResponseSchema]:
        async with self.shared_write_action_commit():
            orders = [await self.handle_create(item) for item in items]
        return orders
```

Inside the block, each action still calls `authorize`, takes the snapshot,
makes its change and runs `before_action_commit`. Then it flushes and returns.
Its changes are not committed yet, and its `after_action_commit` has not run.
So code after a handler call inside the block runs before the commit. Code
that needs the commit, or an after-hook that has run, goes after the block,
or in `after_action_commit`. The route above returns the orders after the
block, so Restly builds their responses after the commit. On `fr.RestView`, use
`with` and the sync handlers. The method has the same name.

:::{admonition} Details
:class: details

The block can also hold the handlers of other views, for an operation on more
than one model. Each create runs the steps of its own view: `authorize`, the
reference checks, the save and the hooks. The SaaS example creates a label and
attaches it to a task:

```{literalinclude} ../example-projects/saas/app/labels/views.py
:language: python
:pyobject: TaskLabelView.create_and_attach
```

The label's handler saves the label, so the link gets its id. The label, the
link and the rows that their `before_action_commit` hooks write are committed
together. If any part fails, none of them is committed. Code that only
belongs to one action, such as copied child rows, goes in that action's
`write_action` block instead, as the clone route does.

Blocks inside each other share the commit when their views share a session.
A `write_action` block counts as such a block, so a handler that you call
inside it joins its commit. The handler saves, so it also writes the changes
of the outer block so far: set the required values of the block's objects
before you call it.
An exception that leaves a `shared_write_action_commit()` block cancels the
shared commit and drops the queued hooks. This also happens when an outer
block catches the exception. For a write action that raises inside the
block, see the savepoint below.
The outermost block runs in a savepoint of its own. When an exception leaves
it, Restly rolls back to that savepoint: what the block changed is undone, and
the session can write again. A direct `session.commit()` inside the block
raises `RuntimeError`, because the outermost block owns the commit.

When some rows may fail and the others must still be saved, put SQLAlchemy's
`session.begin_nested()` around each handler call. Catch the error outside
that savepoint:

```python
    from sqlalchemy.exc import IntegrityError

    async with self.shared_write_action_commit():
        for item in items:
            try:
                async with self.session.begin_nested():
                    await self.handle_create(item)
            except IntegrityError:
                continue
```

The savepoint holds the row's change and the rows that its
`before_action_commit` wrote. So a failed row rolls back both, and its queued
after-hook is dropped. Without the savepoint, a row that failed after its
change began, such as a `create` that refuses after `super().create()`, would
leave a half-done change in the session. So when the route catches such an
error without a savepoint, the commit raises `RuntimeError`, and nothing is
committed. An error before the change, from `authorize` or the reference
check, changed nothing, and the commit goes on. Inside the shared block, the row's error stays a raw
`IntegrityError`, so the route can catch it. An `IntegrityError` that escapes
the shared block, or that its commit raises, becomes a `409`; see
[Unique values and other database conflicts: 409](#default-exception-handling). After the shared commit, the after-hooks of the saved
rows run in order. They stop at the first exception.

The SaaS example runs both forms. `POST /tasks/bulk`, `/tasks/bulk-delete`
and `/tasks/import-csv` put a savepoint around each row inside one
`shared_write_action_commit()` block
([`app/tasks/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/tasks/views.py)).
`POST /task-labels/create-and-attach` commits a label and its link together
([`app/labels/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/labels/views.py)).
:::

(atomic-write-actions)=
## Code that must commit in a write action

A write action is one transaction, so Restly refuses a `session.commit()` in it.
The client gets a 500, and nothing is saved. Remove that commit if you can. The
action then saves all of its work or none of it.

Some code must keep its commit: a library that commits, or a function that a job
or a script also calls. For that code, set
{attr}`atomic_write_actions <fastapi_restly.views.BaseRestView.atomic_write_actions>`
to `False`, on the view or on a base view:

```python
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserSchema
    atomic_write_actions = False

    async def create(self, data):
        return await register_user(self.session, data)  # this function commits
```

This setting takes away two things: the refusal of a commit, and the savepoint.
All else stays as on other views: `authorize`, the save, both hooks, a commit at
the end, and a 409 for a conflict.

:::{warning}
The action is no longer one transaction. What the code committed stays saved when
a later step raises: the client gets an error, the rows are saved, and
`after_action_commit` does not run. This is also true for an error in
`before_action_commit`. What that hook writes is not saved together with the rows
of that commit, so an outbox row can be missing.
:::

An error rolls back all that is not committed, and not only the changes of the
action, because there is no savepoint. A route that catches the error and goes on
must load its rows again, because the rollback expires them.

:::{admonition} Details
:class: details

The outermost write action decides, and the actions inside it join it, as on
other views. So a handler inside the `write_action` block of this view does not
commit on its own. The commit comes at the end of the outermost action, and then
the `after_action_commit` hooks of all its actions run. Code inside an action of
this view may commit, also in a handler of a view that keeps the default. Inside
an action of a view that keeps the default, a commit is refused, also in a
handler of this view. An outermost
[`shared_write_action_commit()`](#shared-write-action-commit) block always refuses
a commit, also on this view. Inside an action of this view, such a block joins
the action, as an action does.

Restly refuses only `session.commit()`. A commit on the connection, such as
`session.connection().commit()`, is not refused, on any view.

On this view, Restly does not check what else the code does with the transaction:

- `session.rollback()` and `session.close()` undo all that is not committed, but
  the action goes on, and the client gets a 2xx. So changes can be lost without
  an error. A method that rolls back or closes the session must raise.
- `with session.begin():` raises when a transaction is open, and the load of an
  update opens one.

The save leaves alone a row that your code took out of this session, because it
expunged the row or gave it to another session. The save does not add that row
and does not read it back, so a task can still use it. The hooks get it as `new`,
but a change that a hook makes to it is not saved. The response uses it as it is,
so what the response reads must be loaded. A row with a change is different: the
save adds it and writes the change. A new row is different too: the save adds
it. After a delete and a commit in your code, the hooks get `new=None`.

[Django](https://docs.djangoproject.com/en/5.2/topics/db/transactions/) has the
same choice. Its `atomic` refuses a commit, and `non_atomic_requests` takes one
view function out of the transaction of the request. Here the setting is for
every route of the view.
:::

(logic-outside-a-view)=
(domain-utilities)=
## Run the same logic in a script or a worker

While the view is the only caller, keep the logic in its `create` or `update`.
There it has `self.session`, `self.request` and the view's dependencies.

When a script or a worker must run the same logic, move it into a plain
function and call it from both:

```python
from fastapi_restly.objects import async_make_new_object, async_save_object


def set_password(user: User, raw_password: str) -> None:
    user.password = hash_password(raw_password)


class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserSchema

    async def create(self, data):
        user = await super().create(data)
        set_password(user, data.password)
        return user


async def import_user(session, payload) -> User:
    user = await async_make_new_object(session, User, payload)
    set_password(user, payload.password)
    await async_save_object(session, user)
    await session.commit()
    return user
```

The script has no handler, so it saves and commits itself. Do not move logic
out of the view before there is a second caller.

A value that every write path must set does not need this. Put it on the
model ([Models](#write-path-values)), and every path gets it.

:::{admonition} Details
:class: details

The functions in {mod}`fr.objects <fastapi_restly.objects>` are for another
model and for code outside a view:
{func}`async_make_new_object <fastapi_restly.objects.async_make_new_object>`,
{func}`async_update_object <fastapi_restly.objects.async_update_object>`,
{func}`async_save_object <fastapi_restly.objects.async_save_object>` and
{func}`async_delete_object <fastapi_restly.objects.async_delete_object>`, and
their sync versions. See
[The same operations outside a view](#advanced-object-helpers). They do not
call `authorize` or the hooks. `async_make_new_object` and
`async_update_object` check the references too. In a CRUD route the handler
has checked them first, and the second check does not query the database
again. `async_save_object` refreshes the object, but it does not load the
relationships that the view's response schema names.

Up to 0.11, views had the methods `make_new_object`, `update_object` and
`save_object`. They are removed. A view class that defines one fails when
Python creates the class, with a message that names the replacement.
:::

## Internals

The CRUD handlers and `write_action` share one private implementation of the
commit bracket. It calls `authorize`, checks the references in `data`, takes
the snapshot, and runs the change under `session.no_autoflush`. Then it
saves: it flushes and refreshes the subject. Then one reload with the view's
loader options covers the subject and the other written rows of the view's
model. It does not apply the scope, so a row that the action moved out of it,
such as a soft-deleted row, still comes back. Then it runs
`before_action_commit`, the commit and `after_action_commit`. The outermost
bracket on a session owns the commit. So inside a `write_action` or a
`shared_write_action_commit()` block, a bracket flushes instead of
committing and queues its after-hook for the outermost block. A handler
hands the object that `create` or `update` returns to the bracket as the
subject. `write_action` gets its subject from `obj`. The outermost bracket
opens a savepoint after the snapshot, and an exception rolls back to it; see
[When a write action raises](#write-action-error).
