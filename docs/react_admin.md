# React Admin

[React-admin](https://marmelab.com/react-admin/) with
[`ra-data-simple-rest`](https://github.com/marmelab/react-admin/tree/master/packages/ra-data-simple-rest)
expects a REST dialect that differs from the
[default CRUD routes](#default-crud-behavior) of Restly in four ways:

- The list route reads three JSON-encoded query parameters: `sort`, `range`
  and `filter`.
- The list route returns a plain JSON array, not a `data` envelope.
- The list response has a `Content-Range` header with the total number of
  rows.
- Updates use `PUT`.

{class}`AsyncReactAdminView <fastapi_restly.views.AsyncReactAdminView>` and
{class}`ReactAdminView <fastapi_restly.views.ReactAdminView>` speak this
dialect, so `ra-data-simple-rest` works without a custom data provider. They
are a {class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` and a
{class}`RestView <fastapi_restly.views.RestView>` with a different list
route and an added `PUT` route. Everything else is inherited: the other
routes, the business methods, the hooks, and the scope. So the rest of the
Guide applies to these views too.

(react-admin-quick-start)=

## Connect react-admin to a view

Replace {class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` with
{class}`AsyncReactAdminView <fastapi_restly.views.AsyncReactAdminView>`, or
{class}`RestView <fastapi_restly.views.RestView>` with
{class}`ReactAdminView <fastapi_restly.views.ReactAdminView>` for sync
sessions. During development, the react-admin frontend runs on its own
origin, so the app also needs [CORS middleware](#react-admin-cors) from the
start:

```python
from contextlib import asynccontextmanager

import fastapi_restly as fr
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import DeclarativeBase, Mapped

fr.configure(async_database_url="sqlite+aiosqlite:///app.db")

class Base(fr.ModelDefaults, DeclarativeBase):
    pass

class Product(fr.IDMixin, Base):
    name: Mapped[str]
    price: Mapped[float]

@asynccontextmanager
async def lifespan(_app: FastAPI):
    await fr.db.async_create_all(Base)  # dev tables; use Alembic in production
    yield

app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # the react-admin dev server
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Range"],
)

@fr.include_view(app)
class ProductView(fr.AsyncReactAdminView):
    prefix = "/products"
    model = Product
```

On the frontend, point `ra-data-simple-rest` at your API:

```jsx
import { Admin, Resource, ListGuesser } from "react-admin";
import simpleRestProvider from "ra-data-simple-rest";

const dataProvider = simpleRestProvider("http://localhost:8000");

export default () => (
  <Admin dataProvider={dataProvider}>
    <Resource name="products" list={ListGuesser} />
  </Admin>
);
```

`ListGuesser` finds the list columns from the response. Replace it with your
own list component as the UI grows.

The [shop example](https://github.com/rjprins/fastapi-restly/tree/main/example-projects/shop)
runs this setup. Its API in
[`shop/main.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/shop/shop/main.py)
has three resources with relationships, and products with UUID ids. Its
react-admin frontend is in
[`ui/test-admin/`](https://github.com/rjprins/fastapi-restly/tree/main/example-projects/shop/ui/test-admin).
Its README shows how to run both.

(react-admin-contract)=

## What a react-admin view serves

### The list route: `GET /resource`

The list route reads the three JSON-encoded query parameters of react-admin:

| Query parameter | Format | Example |
|---|---|---|
| `sort` | JSON `[field, direction]` | `sort=["name","ASC"]` |
| `range` | JSON `[start, end]` (both included) | `range=[0,24]` |
| `filter` | JSON object | `filter={"name":"foo"}` or `filter={"id":[1,2,3]}` |

These three are the whole contract.

The response body is a plain JSON array, and the `Content-Range` header
holds the total:

```
Content-Range: items 0-24/315
```

:::{admonition} Details
:class: details

Any other query key gets a 422, so a typo cannot return more rows than you
asked for without notice. The `page`, `page_size` and field filters of the
standard dialect are unknown keys here too. A key that a dependency
declares, or an `APIKeyQuery` key, is accepted.
For a key that the view reads itself, declare
{attr}`extra_query_params <fastapi_restly.views.BaseRestView.extra_query_params>`
on the view.

`ra-data-simple-rest` requests the collection path without a trailing slash
(`GET /products?sort=...`). This is the main path of a Restly collection, so
no redirect happens.

React-admin uses the `id` array form of `filter` (`{"id": [1, 2, 3]}`) for
its `getMany` calls. It becomes `WHERE id IN (1, 2, 3)`. Other filter values
must be equal to the column value. Only fields that the view's response class
sends are accepted. Other fields, also react-admin's full-text search
parameter `q`, get a 400. For a search on part of a value, or a full-text
search, override the view's
{meth}`apply_filters <fastapi_restly.views.RestView.apply_filters>`.

The settings that choose the keys of the standard dialect,
{attr}`include_filters <fastapi_restly.views.BaseRestView.include_filters>`,
`exclude_filters`, `include_sort_keys` and `exclude_sort_keys`, and
{func}`fr.list_filter <fastapi_restly.views.list_filter>`, do not apply to
this dialect. A react-admin view that has one fails when it registers. When
it inherits a setting from a base view, it sets the default again, such as
`exclude_filters = ()`, to show that the setting does not apply.
:::

### The other routes

Four of the other routes serve the other data provider calls of react-admin.
The standard `PATCH` route stays, for clients that send partial updates:

| Method | Path | Purpose | Source |
|---|---|---|---|
| `GET` | `/{id}` | Get one (react-admin `getOne`) | inherited from {class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` |
| `POST` | `/` | Create (react-admin `create`) | inherited from `AsyncRestView` |
| `PUT` | `/{id}` | Full update (react-admin `update`) | added by {class}`AsyncReactAdminView <fastapi_restly.views.AsyncReactAdminView>` |
| `PATCH` | `/{id}` | Partial update | inherited from `AsyncRestView` |
| `DELETE` | `/{id}` | Delete (react-admin `delete`) | inherited from `AsyncRestView` |

The views add `PUT /{id}`, because the default `update` method of
`ra-data-simple-rest` sends a `PUT` request. React-admin sends the complete
record on update, with `id` and the read-only fields. The update schema drops
the fields that it does not accept, and does not reject the request.

(react-admin-put)=

### Give PUT its own rules

The `PUT` route calls the same
{meth}`handle_update <fastapi_restly.views.RestView.handle_update>` handler
as `PATCH`, with the view's standard
{attr}`schema_update <fastapi_restly.views.BaseRestView.schema_update>`
payload. So an {meth}`update <fastapi_restly.views.RestView.update>` business
method that you override changes both routes. To give `PUT` its own rules,
[replace](#replace-endpoint-method) its endpoint method `put`. This version
requires the complete record, while `PATCH` still accepts a part of it:

```python
class ProductReplace(fr.BaseSchema):
    name: str
    price: float

@fr.include_view(app)
class ProductView(fr.AsyncReactAdminView):
    prefix = "/products"
    model = Product

    @fr.put("/{id}")
    async def put(self, id: int, payload: ProductReplace):
        obj = await self.handle_update(id, payload)
        return self.to_response(obj)
```

A `PUT` with a missing field returns 422. Keys that the schema does not
declare, such as the `id` that react-admin sends, are ignored.

(react-admin-id-arrays)=

## Send to-many references as id arrays

`ra-data-simple-rest` expects a to-many reference as a plain array of ids
(`"products": [1, 2, 3]`). Declare the field as
{class}`list[fr.IDRef[Model]] <fastapi_restly.schemas.IDRef>`. The shop
example does this for the products of an order:

```{literalinclude} ../example-projects/shop/shop/main.py
:language: python
:start-at: class OrderSchema(
:end-at: products: list[fr.IDRef[Product]]
```

[Reference several rows](#reference-lists) describes lists of
references in full.

(react-admin-cors)=

## Set up CORS for the frontend

During development, the react-admin app and the API run on different
origins, so the browser applies CORS to every request. Two things must be
true. The API must allow the origin of the frontend. And the `Content-Range`
header must be exposed to JavaScript, because browsers hide most response
headers from scripts. Add `CORSMiddleware` with `Content-Range` in
`expose_headers` to do both:

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # your frontend origin
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Range"],
)
```

The react-admin views also set `Access-Control-Expose-Headers: Content-Range`
on their list responses, so pagination still works when `expose_headers`
lacks the entry. Do not depend on that. Middleware or a proxy that sets its
own `Access-Control-Expose-Headers` replaces the header of the view, and one
that removes it hides the total from the frontend. Declare the header in
configuration that you control. FastAPI's
[CORS guide](https://fastapi.tiangolo.com/tutorial/cors/) explains the
middleware.

(react-admin-blank-list)=

## Fix a list that shows no rows

A react-admin list can show "No Products found" while the API has rows. This
is usually one of three configuration errors, and the browser console tells
you which one. Open the console in the developer tools and find the error:

**"The Content-Range header is missing in the HTTP Response"** comes from
`ra-data-simple-rest`. The response reached the browser, but JavaScript
cannot read the `Content-Range` header. Add `Content-Range` to
`expose_headers`, as [Set up CORS](#react-admin-cors) shows. Then look for
middleware or proxies that replace or remove `Access-Control-Expose-Headers`.
`curl -i` shows the header, but that proves nothing here: only what the
browser exposes to scripts matters.

**"TypeError: Failed to fetch"**, with a CORS policy error above it, means
that the browser blocked the request: the API sends no
`Access-Control-Allow-Origin` for the origin of the frontend. Add
`CORSMiddleware` with the frontend origin in `allow_origins`.

**"422 (Unprocessable Content)"** on the list request means that the route
rejected the query of react-admin, so the view is a plain
{class}`AsyncRestView <fastapi_restly.views.AsyncRestView>`: the standard
dialect does not accept the JSON-encoded `sort`, `range` and `filter`
parameters. Change the view to
{class}`AsyncReactAdminView <fastapi_restly.views.AsyncReactAdminView>`.

(react-admin-settings)=

## Change the defaults

A few settings on the view change the defaults above, and a base class can
share them between views.

(react-admin-page-size)=

### Set the default page size

When the frontend sends no `range` query parameter, the react-admin views
return the first 25 rows. This replaces the default
{attr}`default_page_size <fastapi_restly.views.NumberedPagination.default_page_size>`
of 50 for other views. To choose another value, set it in the view's
{attr}`pagination <fastapi_restly.views.BaseRestView.pagination>`:

```python
@fr.include_view(app)
class ProductView(fr.AsyncReactAdminView):
    prefix = "/products"
    model = Product
    pagination = fr.NumberedPagination(default_page_size=50)
```

A react-admin view uses two pagination settings: `default_page_size`, and
`max_page_size`, the most rows that one `range` may ask for. A wider
`range`, or one with a negative start or an end before its start, gets a
`400`. The view pages with `range` and reports the total in the
`Content-Range` header, so other settings, such as parameter names or an
envelope, raise an error when you register the view. So does
`pagination = None`, because the header needs the total count.

### Change the Content-Range unit

`ra-data-simple-rest` ignores the unit in the header; it only reads the
numbers. If another client needs a unit, override
`get_react_admin_range_unit`:

```python
class ProductView(fr.AsyncReactAdminView):
    prefix = "/products"
    model = Product

    def get_react_admin_range_unit(self) -> str:
        return "products"
```

### Share these settings between views

Put the shared settings in a base class of your project, and let your views
inherit from it, as
[Share behavior with base views](#share-behaviour-with-base-views) explains:

```python
class ReactAdminBase(fr.AsyncReactAdminView):
    pagination = fr.NumberedPagination(default_page_size=100)

    def get_react_admin_range_unit(self) -> str:
        return "items"

@fr.include_view(app)
class ProductView(ReactAdminBase):
    prefix = "/products"
    model = Product

@fr.include_view(app)
class CustomerView(ReactAdminBase):
    prefix = "/customers"
    model = Customer
```

(react-admin-internals)=

## Internals

{class}`AsyncReactAdminView <fastapi_restly.views.AsyncReactAdminView>` is a
small subclass of
{class}`AsyncRestView <fastapi_restly.views.AsyncRestView>`, built by
[replacing an endpoint method](#replace-endpoint-method). It replaces
{meth}`get_many_endpoint <fastapi_restly.views.RestView.get_many_endpoint>`
to read the react-admin query string. Then it calls the standard
{meth}`handle_get_many <fastapi_restly.views.RestView.handle_get_many>` and
{meth}`get_many <fastapi_restly.views.RestView.get_many>`. The react-admin
dialect itself is in
{meth}`apply_list_params <fastapi_restly.views.RestView.apply_list_params>`
(JSON `sort` and `range`), in
{meth}`apply_filters <fastapi_restly.views.RestView.apply_filters>`
(JSON `filter`) and
{meth}`to_list_response <fastapi_restly.views.BaseRestView.to_list_response>`
(a plain array body and `Content-Range`). These views do not use the list
parameters of the standard dialect, which
[Filtering & Pagination](#list-parameters-lifecycle) describes.

The views also add a `PUT /{id}` route that calls the standard
{meth}`handle_update <fastapi_restly.views.RestView.handle_update>` handler.
The endpoint methods of the other CRUD routes (`GET /{id}`, `POST /`,
`PATCH /{id}`, `DELETE /{id}`) and the business methods for writes are
inherited without change.

The shared helpers that parse the query and build the response are internal.
