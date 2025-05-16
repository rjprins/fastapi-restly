# FastAPI-Ding
## A REST Framework for FastAPI
### Installation

```bash
$ pip install fastapi-alchemy
```
**FastAPI-Alchemy (`fa`)** is built on top of [FastAPI](https://fastapi.tiangolo.com) and [SQLAlchemy](https://www.sqlalchemy.org). It aims to be **batteries-included**, providing tools and utilities commonly needed in FastAPI projects that get deployed into production.


## Example Usage

`fa` gives you REST endpoints for a SQLAlchemy model in a completely customizable and extendable way. Here's the smallest possible example:

```python
import fastapi_alchmey as fa
from fastapi import FastAPI
from sqlalchemy.orm import Mapped


fa.settings.async_database_url = "sqlite+aiosqlite:///blog.db"


app = FastAPI()


class Blog(fa.IDBase):
    title: Mapped[str]


class BlogSchema(fa.IDSchema[Blog]):
    title: str


@fa.include_view(app)
class BlogView(fa.AsyncAlchemyView):
    prefix = "/blogs"
    model = Blog
    schema = BlogSchema
o```
This creates five endpoints:

- `GET /blogs` – (TODO:  trailing slash??) list all items, with support for filtering and sorting  
- `POST /blogs` – create a new item  
- `GET /blogs/<id>` – retrieve a single item by ID  
- `PUT /blogs/<id>` – update an existing item  
- `DELETE /blogs/<id>` – delete an item by ID

And produced [this OpenaAPI specification](TODO).

### That's Too Implicit for My Pythonic Senses

Where's the Pydantic model? What exactly is `IDBase`?

We get it — implicitness can be annoying. On the other hand, explicitness gets repetitive once you've seen the same patterns ten times. Code is all about choosing your trade-offs.

Luckily, `fa` lets you choose your level of abstraction. Here’s a more explicit example if you prefer seeing the details up front.
```python
import fastapi_alchemy as fa
from fastapi import FastAPI
from sqlalchemy import Mapped, mapped_column

app = FastAPI()

class World(fa.AlchemyView):
    id: Mapped[int] = mapped_column(primary_key=True)
    message: Mapped[str]

class WorldSchema(fa.BaseSchema):
    id: int
    message: str

@fa.include_view(app)
class WorldView(fa.AlchemyView):
    prefix = "world"
    model = World
    schema = WorldSchema
```

No hidden `id` column, no hidden Pydantic schema. Ahh, much better.

Of course, many things are still abstracted away. But surely you don’t want to know *everything*. You’ve got stuff to do.

## Show Me All That It Can Do
Minimal examples look nice, but reality never turns out that way.
Let's dive into the deep end where any serious project finds itself rather soon.
Here is an example that includes relationships, nested models, custom routes, and class-level dependencies.

```python
from fastapi_alchemy import fa
from fastapi import FastAPI
from sqlalchemy import Mapped, mapped_column

app = FastAPI()

class Product(fa.SQLBase, fa.TimestampMixin):
    id: Mapped[UUID] = mapped_column(primary_key=True)
    name: Mapped[str]
    addresses: relationship.. many-to-many
    blog_posts: relationship.. 1 to many

class Order(fa.IDStampsBase):
    population: Mapped[int]

class UserSchema(fa.BaseSchema):
    id: UUID
    blog_posts: list['CitySchema']

class CitySchema(TimestampsSchemaMixin):
    read_only_fields: ClassVar = ["population"]
    population: int

@fa.include_view(app)
class WorldView(view.AlchemyView):
    prefix = "world"
    model = World
    schema = WorldSchema

    @view.route("/ola")
    async def
```

We are going to unpack what happens here, so you know what you can and cannot do with `fa`. Here is a list of things that might interest you:

* [[fastapi-ding-docs/Custom endpoints]]
* Custom FastAPI dependencies on views
* REST views unrelated to SQLAlchemy or a database
* Other types of primary keys, i.e. UUID id
* Custom create and update schemas
* Disable default endpoints
* Nesting REST views


This includes an explicit Pydantic schema and an explicit `id` column.
Under the hood `fa` creates two more Pydantic schemas: A `creation_schema` that excludes read-only fields (like `id`). The `creation_schema` is used on the `POST` endpoint. It also create an `update_schema`, where everything is optional. This `update_schema` enables (partial) `PUT` requests.


[Tutorial](tutorial.md)
