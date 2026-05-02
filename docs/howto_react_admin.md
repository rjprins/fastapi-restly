# How-To: React Admin Integration

[React-admin](https://marmelab.com/react-admin/) with
[`ra-data-simple-rest`](https://github.com/marmelab/react-admin/tree/master/packages/ra-data-simple-rest)
expects a specific REST wire contract that differs from the default Restly
contract in several ways: JSON-encoded sort and range parameters, a plain
array response body, and a `Content-Range` header for pagination.

`AsyncReactAdminView` and `ReactAdminView` implement this contract. Switch
to one of these view classes and `ra-data-simple-rest` works without a custom
data provider.

---

## Quick start

Replace `AsyncRestView` with `AsyncReactAdminView` (or `RestView` with
`ReactAdminView` for sync sessions):

```python
import fastapi_restly as fr
from fastapi import FastAPI
from sqlalchemy.orm import Mapped

app = FastAPI()
fr.configure(async_database_url="sqlite+aiosqlite:///app.db")

class Product(fr.IDBase):
    name: Mapped[str]
    price: Mapped[float]

@fr.include_view(app)
class ProductView(fr.AsyncReactAdminView):
    prefix = "/products"
    model = Product
```

On the frontend, point `ra-data-simple-rest` at your API:

```jsx
import { Admin, Resource } from "react-admin";
import simpleRestProvider from "ra-data-simple-rest";

const dataProvider = simpleRestProvider("http://localhost:8000");

export default () => (
  <Admin dataProvider={dataProvider}>
    <Resource name="products" />
  </Admin>
);
```

No custom data provider, no adapter layer.

---

## Wire contract

`AsyncReactAdminView` translates the `ra-data-simple-rest` query format to SQL
and returns responses the provider expects.

### List — `GET /resource/`

| Query parameter | Format | Example |
|---|---|---|
| `sort` | JSON `[field, direction]` | `sort=["name","ASC"]` |
| `range` | JSON `[start, end]` (inclusive) | `range=[0,24]` |
| `filter` | JSON object | `filter={"name":"foo"}` or `filter={"id":[1,2,3]}` |

Response: a plain JSON array. The `Content-Range` header carries the total:

```
Content-Range: items 0-24/315
```

The `id` array form of `filter` (`{"id": [1, 2, 3]}`) is used by react-admin
for `getMany` calls. It translates to `WHERE id IN (1, 2, 3)`.

### Other operations

| Method | Path | Purpose | Source |
|---|---|---|---|
| `GET` | `/{id}` | Get one — react-admin `getOne` | inherited from `AsyncRestView` |
| `POST` | `/` | Create — react-admin `create` | inherited from `AsyncRestView` |
| `PUT` | `/{id}` | Full update — react-admin `update` | added by `AsyncReactAdminView` |
| `PATCH` | `/{id}` | Partial update | inherited from `AsyncRestView` |
| `DELETE` | `/{id}` | Delete — react-admin `delete` | inherited from `AsyncRestView` |

`AsyncReactAdminView` and `ReactAdminView` add a `PUT /{id}` endpoint because
`ra-data-simple-rest`'s default `update` method issues a `PUT` request. The
default `PATCH /{id}` is also kept available, so clients that prefer partial
updates continue to work.

The PUT route delegates to the same `on_update` hook as PATCH and accepts the
view's standard `update_schema` payload. Override `on_update` (or replace the
PUT route directly) if you need different write semantics for the two methods.

---

## Tip: serialize related lists as scalar id arrays with `FlatIDSchema`

`ra-data-simple-rest` expects `to-many` relationship references in list/get
responses to be plain id arrays, e.g. `"product_ids": [1, 2, 3]`, not
`[{"id": 1}, ...]`. Use `fr.FlatIDSchema[Model]` instead of `fr.IDSchema[Model]`
for relationship list fields on your response schema:

```python
class OrderSchema(fr.IDSchema[Order]):
    customer_name: str
    products: list[fr.FlatIDSchema[Product]]  # serializes as [1, 2, 3]
```

`FlatIDSchema` accepts both raw scalars and `{"id": ...}` shapes on input, so
it doubles as a permissive write-side type for FK lists when paired with a
custom `on_create` / `on_update` that resolves them. For single-FK fields,
`fr.IDSchema[Model]` remains the right choice.

---

## CORS setup

Browsers block non-standard response headers by default. The `Content-Range`
header must be explicitly exposed in your CORS configuration, otherwise the
frontend cannot read the total and pagination breaks.

Add `CORSMiddleware` to your FastAPI app with `Content-Range` in
`expose_headers`:

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

`AsyncReactAdminView` also sets `Access-Control-Expose-Headers: Content-Range`
on every list response as a per-response fallback, but the middleware approach
is more reliable and is the recommended setup for production.

---

## Customization

### Override query-parameter parsing

Each parsing step is a separate method. Override one to change how a parameter
is interpreted without rewriting the rest:

```python
@fr.include_view(app)
class ProductView(fr.AsyncReactAdminView):
    prefix = "/products"
    model = Product

    def _parse_react_admin_params(self):
        sort, (start, end), filters = super()._parse_react_admin_params()
        # Enforce a maximum page size of 50 regardless of what the frontend sends
        end = min(end, start + 49)
        return sort, (start, end), filters
```

### Change the Content-Range unit

`ra-data-simple-rest` ignores the unit part of the header (it only parses the
numbers), but if another consumer cares, override `get_react_admin_range_unit`:

```python
class ProductView(fr.AsyncReactAdminView):
    prefix = "/products"
    model = Product

    def get_react_admin_range_unit(self) -> str:
        return "products"
```

### Override the list response

Override `_build_react_admin_list_response` to change the response format
entirely — for instance to add extra metadata headers:

```python
import fastapi, json

class ProductView(fr.AsyncReactAdminView):
    prefix = "/products"
    model = Product

    def _build_react_admin_list_response(self, items, total, start, end):
        response = super()._build_react_admin_list_response(items, total, start, end)
        response.headers["X-Api-Version"] = "2"
        return response
```

### Share the react-admin contract across multiple views

Put any customizations in a mixin and inherit from it alongside
`AsyncReactAdminView`:

```python
class ReactAdminBase(fr.AsyncReactAdminView):
    def get_react_admin_range_unit(self) -> str:
        return "items"

    def _parse_react_admin_params(self):
        sort, (start, end), filters = super()._parse_react_admin_params()
        end = min(end, start + 99)  # cap page size
        return sort, (start, end), filters

@fr.include_view(app)
class ProductView(ReactAdminBase):
    prefix = "/products"
    model = Product

@fr.include_view(app)
class CustomerView(ReactAdminBase):
    prefix = "/customers"
    model = Customer
```

---

## Under the hood

`AsyncReactAdminView` is a thin subclass of `AsyncRestView` built with the
[route replacement](howto_override_endpoints.md#replace-a-generated-route)
pattern. It replaces the `index` route to change the list contract and adds a
`PUT /{id}` route that delegates to the standard `on_update` hook. All other
generated routes (`GET /{id}`, `POST /`, `PATCH /{id}`, `DELETE /{id}`) and all
`on_*` hooks are inherited unchanged.

The shared parsing and response logic lives in `ReactAdminMixin`, which is
also what you would use directly if you need to build a fully custom
react-admin-compatible view from scratch.
