# Structure a Project

Restly does not impose a project layout. Views are ordinary Python classes and
{func}`include_view() <fastapi_restly.views.include_view>` is an ordinary function call, so any arrangement of
modules that Python can import will work. This page describes the layout we
recommend anyway, so that a growing application does not have to invent one,
and explains what each part of it buys you.

## Start with one file

A single module is the right shape for a first resource.
[Getting Started](getting_started.md) builds a complete application in one
file, and nothing is gained by splitting it while it stays that size.

Split when the second or third resource arrives, or when one resource grows
explicit schemas and a handful of overrides. The signal is scrolling: when you
navigate the file by searching rather than by reading, it holds more than one
subject.

## Organize by subject, then by type

Give each resource a package, and name the modules inside it after the kind of
code they hold:

```text
myapp/
├── main.py                 # Application factory and lifespan
├── api.py                  # View registration
├── database.py             # Engine, sessions, and declarative base
├── model_registry.py       # Explicit model imports for Alembic
├── views.py                # Shared base view and mixins
├── users/
│   ├── __init__.py
│   ├── models.py
│   ├── schemas.py
│   └── views.py
└── tasks/
    ├── __init__.py
    ├── models.py
    ├── schemas.py
    └── views.py
```

The alternative is to organize by type first: a top-level `models/`,
`schemas/`, and `views/` package, each holding one module per resource. That
layout is common and it works, but it scatters every change across three
directories.

Subject-first suits Restly in particular. A view names its model, names its
schema, and carries the overrides for both, so the three modules are one unit
of work: adding a field touches the model, the schema, and usually a business
method, and all three sit in the same directory. Type-first organization is
the better fit for Restly's own source, where `models`, `schemas`, and `views`
really are separate subsystems, and the worse fit for an application, where
they are three views of one resource.

Name each package after the route segment it serves, so `users/` serves
`/users`.

## Keep imports pointing one way

Within a subject, models import nothing from their siblings, schemas import
models, and views import both. Application composition imports the views. That
ordering keeps subjects importable in isolation, which matters for Alembic,
which loads models without an application, and for tests, which load one view
without the rest of the app.

Nothing in a subject package should register itself as a side effect of being
imported. Keep `__init__.py` empty or limited to a docstring, and import
concrete modules rather than building re-export modules that pull in a whole
subject to reach one class.

(compose-in-one-place)=

## Compose in one place

Restly has no autodiscovery. Nothing scans your package for views or models,
which is why the layout above needs two explicit composition modules. The
tradeoff is deliberate: your `create_app()` stays yours, and the set of
registered views is something you can read rather than infer.

`api.py` registers every view on the application:

```python
# myapp/api.py
import fastapi_restly as fr
from fastapi import FastAPI

from .tasks.views import TaskView
from .users.views import UserView


def register_views(app: FastAPI) -> None:
    for view in (TaskView, UserView):
        fr.include_view(app, view)
```

The factory then calls it after
{func}`fr.configure(app, ...) <fastapi_restly.db.configure>`, as shown in
[A production `main.py` template](#production-main-template).

`model_registry.py` does the same job for SQLAlchemy metadata. Importing it
imports every model module, so Alembic sees a complete schema:

```python
# myapp/model_registry.py
from .tasks import models as tasks
from .users import models as users

__all__ = ["tasks", "users"]
```

`alembic/env.py` imports that one module instead of tracking each subject by
hand. See
[Migrations with Alembic](deploying.md#migrations-with-alembic).

## Share view behavior from the package root

Behavior common to every view belongs in a root `views.py`: the base class your
views inherit from, plus any mixins. The subject packages hold concrete views,
and the root module holds the foundation they are built on.

```python
# myapp/users/views.py
from ..views import TenantBase, SoftDeleteMixin

from .models import User
from .schemas import UserRead


class UserView(SoftDeleteMixin, TenantBase):
    prefix = "/users"
    model = User
    schema = UserRead
```

[Share Behaviour with Base Views](howto_inheritance.md) covers the base class,
and [Compose Views with Mixins](howto_compose_views_with_mixins.md) covers the
mixins.

## Add modules when they earn them

Resist creating a module before there is something to put in it. In
particular, a subject does not need a `service.py`. For CRUD, the view class is
already that layer: business methods such as
{meth}`create <fastapi_restly.views.RestView.create>` and
{meth}`update <fastapi_restly.views.RestView.update>` are where your logic
goes, and an extra layer that forwards to them adds indirection without
adding a seam. Write a `service.py` when logic genuinely runs outside a
request, such as work shared with a background worker.

The same applies to `dependencies.py`, `constants.py`, and `exceptions.py`
inside a subject. Each is worth having once it holds more than one item.

Keep application-wide concerns in top-level modules named for what they do,
such as `auth.py`, `settings.py`, or `outbox.py`, and promote one to a package
only when it grows several cohesive modules. Avoid generic buckets such as
`shared.py` or `utils.py`, which accumulate unrelated code and give no hint
about where anything lives.

## See also

- [SaaS example](examples.md#saas), a complete application in this layout
- [Deploying](deploying.md) for the factory, engine, and Alembic setup
- [Test APIs with RestlyTestClient and Fixtures](howto_testing.md) for the
  matching `conftest.py`
- [Use Restly in an Existing Project](howto_existing_project.md) when the
  layout is already decided
