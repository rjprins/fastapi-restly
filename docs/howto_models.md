# Declare Your Models

Restly works with SQLAlchemy models on a declarative base of your own. The
base is your class, with its own registry and `MetaData`, as in any
SQLAlchemy project. Restly does not own it. It gives you two kinds of help:

- {class}`fr.ModelDefaults <fastapi_restly.models.ModelDefaults>` adds
  Restly's defaults to your base.
- {class}`fr.IDMixin <fastapi_restly.models.IDMixin>` and
  {class}`fr.TimestampsMixin <fastapi_restly.models.TimestampsMixin>` add
  common columns to a model.

All of it is optional. Restly's views also work with plain SQLAlchemy models.

(model-defaults)=

## Add Restly's defaults to your base

Put {class}`fr.ModelDefaults <fastapi_restly.models.ModelDefaults>` in your
base class, before `DeclarativeBase`:

```python
from sqlalchemy import ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

import fastapi_restly as fr


class Base(fr.ModelDefaults, DeclarativeBase):
    pass


class BlogPost(fr.IDMixin, Base):
    published: Mapped[bool] = mapped_column(default=False)
    title: Mapped[str]


class Comment(fr.TimestampsMixin, fr.IDMixin, Base):
    content: Mapped[str]
    blog_post_id: Mapped[int] = mapped_column(ForeignKey("blog_post.id"))
```

Every model on `Base` then gets these defaults:

- **A dataclass constructor.** The constructor takes keyword arguments only:
  `BlogPost(title="Hello")`. So a field with a default can come before a field
  without one, as `published` does above, and type checkers accept that. Use
  `mapped_column(init=False)` for a column that the constructor must not
  take.
- **A table name.** The table name is the class name in snake_case:
  `BlogPost` gets `blog_post`. That is why `ForeignKey("blog_post.id")` works.
  Set `__tablename__` on a model to choose another name.
- **Datetimes with a time zone.** `Mapped[datetime]` maps to
  `DateTime(timezone=True)`. Store UTC times in it. PostgreSQL returns them
  with a time zone, but SQLite returns them without one. For a clock time
  without a time zone, use `mapped_column(DateTime())` on that column.
- **Enums as strings.** A column typed with an `enum.Enum` stores the member
  as a string of up to 64 characters, not as a database enum type. A new
  member then needs no database migration.
- **`awaitable_attrs`.** Code outside a view can await a relationship that is
  not loaded yet, as
  [Relationship Loading and Async](howto_relationship_loading.md) shows.

(change-model-defaults)=

## Change a default

The base is your class, so you change it as SQLAlchemy documents. To give
constraints predictable names, which Alembic needs to drop them later, set
`metadata`. To change how a Python type maps to a column type, set
`type_annotation_map`:

```python
from decimal import Decimal

from sqlalchemy import MetaData, Numeric
from sqlalchemy.orm import DeclarativeBase

import fastapi_restly as fr


class Base(fr.ModelDefaults, DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )
    type_annotation_map = {Decimal: Numeric(12, 2)}
```

Your `type_annotation_map` entries win over Restly's. Restly's entries still
apply to the types you leave out. So `{datetime: DateTime()}` stores every
datetime on this base without a time zone, and enums are still strings.

For one column, pass the type to `mapped_column`, as in
`mapped_column(Enum(Color, native_enum=True))`. For one table, set
`__tablename__`.

(model-mixins)=

## Add common columns

{class}`fr.IDMixin <fastapi_restly.models.IDMixin>` adds `id`, an integer
primary key. The database chooses its value, so the constructor does not take
it.

{class}`fr.TimestampsMixin <fastapi_restly.models.TimestampsMixin>` adds
`created_at` and `updated_at`. Both are UTC datetimes with a time zone.
SQLAlchemy sets them when it inserts the row, and sets `updated_at` again on
every update. A schema can return them with
{class}`fr.TimestampsSchemaMixin <fastapi_restly.schemas.TimestampsSchemaMixin>`.

For a different primary key, declare `id` yourself instead of using
`fr.IDMixin`:

```python
from uuid import UUID, uuid4

from sqlalchemy.orm import Mapped, mapped_column


class Product(Base):
    id: Mapped[UUID] = mapped_column(
        primary_key=True, init=False, default_factory=uuid4
    )
    name: Mapped[str]
```

The two mixins add dataclass fields, so they need a dataclass base: one with
`fr.ModelDefaults`, or with SQLAlchemy's `MappedAsDataclass`. On a base
without either, they raise
{class}`fr.exc.RestlyConfigurationError <fastapi_restly.exc.RestlyConfigurationError>`.

## Use the base in your app

Everything that asks for your models' tables takes the base:

- {func}`fr.db.async_create_all(Base) <fastapi_restly.db.async_create_all>`
  creates the tables during development.
- `fr.testing.configure_tests(base=Base)` builds and cleans the test tables,
  as [Test APIs with RestlyTestClient and Fixtures](howto_testing.md) shows.
- Alembic's `env.py` sets `target_metadata = Base.metadata`, as
  [Deploying](deploying.md) shows.

A base only knows the models that have been imported.
[Project structure](howto_project_structure.md) shows how one module imports
them all.

## Models without Restly's defaults

Restly's views and schemas work with any SQLAlchemy declarative model,
dataclass or not. Models that you already have can stay on their own base, as
[Use Restly in an Existing Project](howto_existing_project.md) shows.

Adding `fr.ModelDefaults` to an existing base changes every model on it into
a dataclass. A field without a default becomes a required constructor
argument. Two objects with the same field values are equal, and an object
cannot go in a `set`. So add it to a new base, or change the models with care.
