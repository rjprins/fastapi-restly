# Structure a Project

Restly does not impose a project layout. Views are ordinary Python classes and
{func}`include_view() <fastapi_restly.views.include_view>` is an ordinary function call, so any arrangement of
modules that Python can import will work. This page describes the layout we
recommend anyway, so that a growing application does not have to invent one,
and explains what each part of it buys you.

(restly-new)=

## Generate it

`restly new` writes the layout below, with a `users` resource, a test suite,
and the settings and schema wiring:

```bash
pip install fastapi-restly
restly new myapp
```

The name names the project: the directory, `pyproject.toml`, and the databases.
The package inside is always `app`, so `from app.settings import Settings` is
the same line in every Restly project.

Three choices, each a flag, each asked for on a terminal when you leave it out:

| | default | alternative |
|---|---|---|
| Views and driver | `--async` | `--sync` |
| Database | `--postgres`, with a `compose.yaml` | `--sqlite` |
| Schema | `--alembic` | `--create-all`, built at startup and in tests |

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
app/
├── main.py                 # Application factory, VIEWS, and lifespan
├── asgi.py                 # app = create_app(), the deployment entrypoint
├── settings.py             # Pydantic settings
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

Subject or domain first, code type second, once the application is large
enough for the choice to matter.

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
which is why the layout above registers them in one place instead. The
tradeoff is deliberate: your `create_app()` stays yours, and the set of
registered views is something you can read rather than infer.

That one place is a `VIEWS` tuple in `main.py`. The factory registers each of
its views after {func}`fr.configure(app, ...) <fastapi_restly.db.configure>`:

```python
# app/main.py
import fastapi_restly as fr
from fastapi import FastAPI

from .settings import Settings
from .tasks.views import TaskView
from .users.views import UserView

VIEWS = (TaskView, UserView)


def create_app() -> FastAPI:
    settings = Settings.current
    app = FastAPI()
    fr.configure(app, async_database_url=settings.database_url, health="/health")
    for view in VIEWS:
        fr.include_view(app, view)
    return app
```

[A production `main.py` template](#production-main-template) adds the engine and
the lifespan that disposes it.

Subject-first layouts leave one question open, which the rest of this section
settles: which module has seen every model. Start with where the models are
collected. Most applications never declare a declarative base of their own.
Models inherit
{class}`fr.IDBase <fastapi_restly.models.IDBase>` or
{class}`fr.DataclassBase <fastapi_restly.models.DataclassBase>`, and because
`IDBase` subclasses `DataclassBase` they share one `MetaData`, so the
application schema is `fr.DataclassBase.metadata`. Declare your own base only
when you need different mapping defaults, put it in a root `models.py`, and use
it wherever this page names `fr.DataclassBase`.

A base describes the models that have been imported and nothing else, so the
layout needs one module that has imported all of them. That module is
`main.py`. Its `VIEWS` tuple names every view, and each view imports its model.
Nothing else should have to be named: `main.py` is the one module every
application has, so tools can rely on it without knowing how the rest is
arranged.

This works only because the factory keeps `main.py` free of side effects.
Importing it defines `create_app()` and builds nothing, so a test suite can
name its own database rather than setting environment variables before its
imports. Never put `app = create_app()` at module level there: with settings
that have no defaults, importing the module would then require a configured
environment, and the test suite is the first thing to break. The application
object belongs in `asgi.py`, which only the server imports; see
[Running the app](deploying.md#running-the-app).

`alembic/env.py` imports it before reading `target_metadata`:

```python
# alembic/env.py
import fastapi_restly as fr
import app.main  # noqa: F401  (imports every view, and each view its model)

target_metadata = fr.DataclassBase.metadata
```

A `conftest.py` that asks `configure_tests()` to run `create_all` needs the
same coverage and usually has it already, since the app it passes was built by
the factory. Import `app.main` there too when the suite reaches for a base
without building an application first.

That leaves models no view reaches, such as an outbox or audit table. Import
those at module level wherever the application uses them, rather than inside a
function, so they stay on the same graph. A model nothing in the application
uses, written only by a worker or a script, goes in `main.py` beside `VIEWS`,
which keeps every such import in one place.
Getting it wrong is not silent: `alembic check` compares metadata against the
database and reports a missing model as a dropped table, so run it in CI. See
[Migrations with Alembic](deploying.md#migrations-with-alembic) and
[Test APIs with RestlyTestClient and Fixtures](howto_testing.md).

## Share view behavior from the package root

Behavior common to every view belongs in a root `views.py`: the base class your
views inherit from, plus any mixins. The subject packages hold concrete views,
and the root module holds the foundation they are built on. Add it once a second
view wants the same behavior, which is why a generated project does not have one.

```python
# app/users/views.py
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

At the package root, move `VIEWS` and its registration loop into an `api.py`
exposing `register_views(app)` once `main.py` grows crowded; `main.py` imports
it, so it still reaches every model.

Keep application-wide concerns in top-level modules named for what they do,
such as `auth.py`, `settings.py`, or `outbox.py`, and promote one to a package
only when it grows several cohesive modules.

## See also

- [SaaS example](examples.md#saas), a complete application in this layout
- [Deploying](deploying.md) for the factory, engine, and Alembic setup
- [Test APIs with RestlyTestClient and Fixtures](howto_testing.md) for the
  matching `conftest.py`
- [Use Restly in an Existing Project](howto_existing_project.md) when the
  layout is already decided
