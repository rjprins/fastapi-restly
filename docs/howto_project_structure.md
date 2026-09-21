# Project structure

Restly does not impose a project layout. Views are ordinary Python classes and
{func}`include_view() <fastapi_restly.views.include_view>` is an ordinary function call, so any arrangement of
modules that Python can import will work. This page describes the layout we
recommend anyway, so that a growing application does not have to invent one,
and explains what each part of it buys you.

(restly-new)=

## Generate it

`restly new` creates a project with a `users` resource, a test suite,
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
├── current.py              # Current namespace (optional)
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

(project-current)=
## Share Current from the package root

Declare [Current](howto_current.md) in `app/current.py` when multiple modules need
request-bound values. Import that declaration wherever it is used:

```python
from app.current import Current
```

The module can also expose `SetCurrentContextDep`, created with
{meth}`Current.depends <fastapi_restly.clauses.ContextNamespace.depends>`.
Attach this dependency in `main.py` or the shared `views.py`.

Keep `current.py` independent of view modules and the application factory.
Models and scripts must be able to import it without constructing an application.
`restly new` does not generate this module.

(compose-in-one-place)=

## Compose in one place

Restly has no autodiscovery: nothing scans your package for views or models, so
the layout registers them itself. That one place is a `VIEWS` tuple in
`main.py`, which the factory registers after
{func}`fr.configure(app, ...) <fastapi_restly.db.configure>`:

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

That tuple also decides which module has seen every model, since each view
imports its own. A declarative base holds the models that have been imported
and nothing else, and `IDBase` subclasses `DataclassBase`, so
`fr.DataclassBase.metadata` is the whole schema once `main.py` has been
imported. On your own base, see
[Use Your Own DeclarativeBase Models](howto_existing_project.md#use-your-own-declarativebase-models).

`alembic/env.py` relies on exactly that:

```python
# alembic/env.py
import fastapi_restly as fr
import app.main  # noqa: F401  (imports every view, and each view its model)

target_metadata = fr.DataclassBase.metadata
```

This works only because importing `main.py` builds nothing. Never put
`app = create_app()` at module level there: with settings that have no
defaults, importing the module would require a configured environment, and the
test suite is the first thing to break. The application object belongs in
`asgi.py`, which only the server imports; see
[Running the app](deploying.md#running-the-app).

A model no view reaches, such as an outbox or audit table, is not on that
graph. Import it at module level wherever the application uses it, or in
`main.py` beside `VIEWS` when only a worker or a script does.
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
