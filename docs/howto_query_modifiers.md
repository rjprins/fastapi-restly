# Filter, Sort, and Paginate Lists

List endpoints (`GET /{prefix}`) support filtering, sorting, and
pagination through URL query parameters out of the box. Filter parameters
are derived from the response schema; sort and pagination use a fixed set
of names.

Pagination is on by default: lists are capped at 50 rows per page and
wrapped in a `data` envelope. Clients page with `page` and `page_size`. The
view's {attr}`pagination <fastapi_restly.views.BaseRestView.pagination>`
setting changes the limits, the parameter names and the envelope. Lower the
default page size and the maximum on public endpoints; set `pagination = None`
to return every matching row uncapped.

Unknown query keys are rejected with 422. Filters are narrowing controls,
so a typo or unsupported operator silently ignored could widen the result
set. The default list endpoints therefore validate the request against the
schema's declared parameters and reject anything else; for which
malformed requests produce which status, see
[422 vs 400 on list endpoints](howto_error_responses.md#422-vs-400-on-list-endpoints).
To allow extra view-specific keys, see
[Extra query parameters](#extra-query-parameters).

## Filtering

Filter parameters compare a schema field against a value taken from the
URL. Plain equality uses the bare field name:

```text
GET /users?name=John
```

Operator suffixes on the field name select other comparisons:

| Suffix | SQL equivalent | Example |
|---|---|---|
| *(none)* | `field = value` | `?name=John` |
| `__gte` | `field >= value` | `?age__gte=18` |
| `__lte` | `field <= value` | `?age__lte=64` |
| `__gt` | `field > value` | `?age__gt=17` |
| `__lt` | `field < value` | `?age__lt=65` |
| `__in` | `field IN (...)` | `?status__in=active,pending` |
| `__ne` | `field != value` | `?status__ne=archived` |
| `__contains` | `field LIKE '%value%'` | `?email__contains=Example` |
| `__icontains` | `field ILIKE '%value%'` | `?email__icontains=example` |
| `__isnull` | `field IS NULL` / `IS NOT NULL` | `?deleted_at__isnull=true` |

`__isnull` accepts a boolean value (`true` or `false`), not the string
`"null"`.

Not every operator is generated for every field. Range operators
(`__gte`/`__lte`/`__gt`/`__lt`) are only generated for orderable column
types; they are omitted for booleans and UUIDs. `__contains` and
`__icontains` are only generated for string fields. Collection-typed fields
(`dict`/`list`, typically `JSON` or `ARRAY` columns) generate only
`__isnull`: a query-string value cannot coerce into a collection, so the
other operators would fail on every request. A
{data}`WriteOnly <fastapi_restly.schemas.WriteOnly>` field gets no filters
and is not a sort key, since either would let a client read its value back.

A datetime value without a UTC offset is read as UTC when the column is
timezone-aware, which is the default for `Mapped[datetime]`; a column
declared `DateTime()` compares the naive value as given.

### Filter value validation

Equality, `__ne`, `__in`, and range filters run the field's Pydantic
validation. They do not run model validators. Field validators receive
the Python field name in {attr}`pydantic.ValidationInfo.field_name`, even
when the URL uses an alias. {attr}`pydantic.ValidationInfo.data` is `{}`:
each filter value is validated independently, without other field values.

### Comma logic on bare equality

Comma-separated values in a plain equality filter are OR-combined:

```text
GET /users?status=active,pending
```

This produces `WHERE status = 'active' OR status = 'pending'`, which is
equivalent to SQL `IN`. Use `__in` when you want that same SQL `IN`
meaning to be explicit in the URL:

```text
GET /users?status__in=active,pending
```

### Comma logic on `__ne` (NOT IN)

Comma-separated values in `__ne` are AND-combined, meaning the row must
differ from every listed value:

```text
GET /users?status__ne=archived,deleted
```

This produces `WHERE status != 'archived' AND status != 'deleted'`, which
is equivalent to SQL `NOT IN`.

### AND-combining multiple contains terms

To require several substrings at once, the precise form is to repeat the
parameter: each predicate becomes its own `LIKE` / `ILIKE` clause and the
clauses are AND-combined:

```text
GET /users?name__contains=john&name__contains=doe
GET /users?name__icontains=john&name__icontains=doe
```

`__contains` produces `WHERE name LIKE '%john%' AND name LIKE '%doe%'`;
`__icontains` uses `ILIKE`.

As a convenience, whitespace inside one `__contains` or `__icontains`
value is also AND-split, so `?name__contains=john%20doe` is equivalent to
the repeated-parameter form above. Prefer repeated parameters when you
control the URL; they are unambiguous and survive any client/server
quoting changes.

Literal `%`, `_`, and `\` characters are escaped before SQL `LIKE` /
`ILIKE`, so contains searches use literal substrings, not wildcards.

### Multiple filters on the same field

Repeat the parameter to add AND conditions:

```text
GET /users?created_at__gte=2024-01-01&created_at__lt=2025-01-01
```

This produces
`WHERE created_at >= '2024-01-01' AND created_at < '2025-01-01'`.

## Sorting

Use the `sort` parameter with comma-separated field names, prefixing a
name with `-` for descending order:

```text
GET /users?sort=-created_at,name
```

Dotted relation paths sort exactly as they filter:
`?sort=-city.country.code` orders by the related column, joining each hop
of the path.

The primary key is always the final `ORDER BY` term, every column of a
composite key included, so equal-valued rows on a non-unique sort are never
skipped or repeated across pages. With no `sort` parameter the primary key
is the whole order.

## Pagination

Pagination is controlled by the `page` and `page_size` parameters:

```text
GET /users?page=2&page_size=50
```

`page` is 1-based. The combination must satisfy
`(page - 1) * page_size <= 2**63 - 1`. A larger SQL offset is rejected with
`422` before the query runs. `page_size` must be `>= 1` and `<= max_page_size`
(default 1000). When the client omits `page_size`, the endpoint falls back to
`default_page_size` (50). Set them with
{class}`fr.NumberedPagination <fastapi_restly.views.NumberedPagination>` on the
view class. `max_page` caps the page number too:

```python
class UserView(fr.AsyncRestView):
    pagination = fr.NumberedPagination(
        default_page_size=25, max_page_size=200, max_page=100
    )
```

Out-of-range pagination values produce a standard `422` response from
FastAPI.

### Parameter names

Rename the two parameters with `page_query_param` and
`page_size_query_param`:

```python
class UserView(fr.AsyncRestView):
    pagination = fr.NumberedPagination(
        page_query_param="p", page_size_query_param="size"
    )
```

Clients then send `?p=2&size=50`, `page` and `page_size` become unknown keys,
and OpenAPI lists the new names. A schema field whose public name matches a
pagination parameter fails when the view registers. Rename the parameter or
give the field an alias. Renaming a parameter frees its old name, so a `page`
column gets its filter back.

### Set it once for every view

Views inherit `pagination`, so a project base view sets it once. A view that
differs changes one setting with `replace()`, which returns a checked copy:

```python
APP_PAGINATION = fr.NumberedPagination(
    page_size_query_param="size", max_page_size=100
)


class AppView(fr.AsyncRestView):
    pagination = APP_PAGINATION


class LogView(AppView):
    pagination = APP_PAGINATION.replace(max_page_size=10)
```

### The pagination envelope

A list endpoint wraps its rows in a `data` envelope with `total_count` and
page metadata. Set `pagination = None` to drop the count and page fields and
return every matching row in a bare `data` envelope:

```python
class UserView(fr.AsyncRestView):
    pagination = None
```

`None` is short for `fr.NoPagination()`. The pagination's `envelope` setting
replaces the envelope model: rename its fields, drop some, or nest them.
`fr.NoPagination(envelope=...)` does the same for a view without pagination.
[Response Envelopes and List Metadata](howto_response_schema.md#change-the-list-envelope)
is canonical for the envelope's shape.

### Coming from fastapi-pagination

fastapi-pagination's default `Page` takes `?page=` and `?size=` and returns
`items`, `total`, `page`, `size` and `pages`. To keep that contract for
existing clients, rename the page size parameter and the envelope fields:

```python
from typing import Generic, TypeVar

import pydantic

T = TypeVar("T")


class Page(pydantic.BaseModel, Generic[T]):
    data: list[T] = pydantic.Field(serialization_alias="items")
    total_count: int = pydantic.Field(serialization_alias="total")
    page: int
    page_size: int = pydantic.Field(serialization_alias="size")
    total_pages: int = pydantic.Field(serialization_alias="pages")


class AppView(fr.AsyncRestView):
    pagination = fr.NumberedPagination(
        page_size_query_param="size", max_page_size=100, envelope=Page
    )
```

The default page size of 50 and the maximum of 100 match fastapi-pagination's.
fastapi-pagination has no filters or sorting. Restly adds both, as described
above.

## Extra query parameters

A query parameter that the route declares needs no setup: one the endpoint
method takes, or one a dependency reads, wherever the dependency is
declared (on the app or a router, in the view's `dependencies`, or on a
class attribute). The key of an `APIKeyQuery` counts too. The unknown-key
check accepts these keys like filters, and OpenAPI lists them beside the
filters.

A view that reads a custom key straight from `self.request.query_params`
(in an override) must name it, or the 422 validation rejects it as an
unknown key:

```python
class UserView(fr.AsyncRestView):
    extra_query_params = ("include_deleted",)
```

## Alias support

Query parameter keys follow the Pydantic schema field's **public name**:
the [alias](howto_custom_schema.md#aliases) when one is declared, the
Python field name otherwise. The public name is the only name the URL
surface accepts. `populate_by_name` controls how Pydantic parses request
bodies; it does **not** extend the list-params URL contract with extra
Python-name aliases.

Consider a schema field declared with an alias:

```python
class UserRead(BaseModel):
    user_name: Annotated[str, Field(alias="userName")]
```

Only the alias is accepted on the URL surface:

```text
GET /users?userName=Alice        # supported
GET /users?user_name=Alice       # rejected, the Python name is not exposed
```

If you want a different URL key, change the alias.

## Relation filtering

Filtering on a related model's field uses dot notation:

```text
GET /orders?user.name=Alice
GET /orders?user.name__contains=ali
```

The relation must be defined on both the SQLAlchemy model (as a
`relationship`) and the Pydantic schema (as a
[nested schema field](howto_relationship_idschema.md#nested-relationship-objects)).
Optional nested schemas (`UserRead | None`) and deep nesting
(`?blog.author.name=Alice`) are supported. Lists of nested schemas
(`list[UserRead]`) are not.

Paths through a self-referential relationship work too, such as
`?manager.name=Alice` or `?manager.manager.name=Alice`. Each hop must be
exposed by a nested schema. Different relationships to the same model can
be filtered together:

```text
GET /people?home_city.name=Amsterdam&work_city.name=Berlin
GET /people?home_city.country.code=NL&work_city.country.code=DE
```

Sorting accepts the same paths. A filter and sort on the same relationship
share its join. These are inner joins: filtering or sorting through an
absent relationship excludes the row, including a dotted `__isnull` filter.

Relationship joins use SQL aliases. If you use
{func}`sqlalchemy.orm.with_loader_criteria` for tenant or visibility rules,
set `include_aliases=True` so the rules cover these joins. See the
[tenant row scoping recipe](#tenant-row-scoping).

Aliases apply to **every** segment of the dotted path, both the relation
field and the nested column, because the list-params keys always follow
the response schema's public names:

```python
class AuthorRead(BaseModel):
    name: str = Field(alias="authorName")

class ArticleRead(BaseModel):
    author: AuthorRead = Field(alias="writer")
```

Requests must then use the aliased segments:

```text
GET /articles?writer.authorName=Alice    # supported (aliased segments)
GET /articles?author.name=Alice          # rejected, use public aliases
```

(foreign-key-filtering)=
## Foreign-key filtering

A scalar foreign key declared with
{class}`fr.MustExist[int, Post] <fastapi_restly.schemas.MustExist>` is
filterable by its own public name, the same name the wire format uses:

```text
GET /comments?post_id=1
GET /comments?post_id=1,2          # OR (SQL IN)
GET /comments?post_id__in=1,2
GET /comments?post_id__ne=1
GET /comments?post_id__gte=10      # range operators apply to int ids
GET /comments?post_id__isnull=true
```

A `MustExist[pk, ...]` id filters exactly like its plain scalar `pk`
type: an `int` id gets equality, `__in`, `__ne`, `__isnull`, and the
range family (`__gte`/`__lte`/`__gt`/`__lt`); a `UUID` id omits the range
family (UUIDs are not orderable; see the range-operator note under
[Filtering](#filtering)). It never gets the substring (`__contains`)
family. A relationship reference (`IDRef` / `IDSchema`), by contrast,
keeps its id opaque and supports only equality, `__in`, `__ne`, and
`__isnull`;
[Choosing a Reference Style](howto_relationship_idschema.md#choosing-a-reference-style)
compares the two reference styles.

## Quick reference

The requests below summarize the grammar in one place:

```text
GET /users?name=John
GET /users?status=active,pending
GET /users?age__gte=18&age__lt=65
GET /users?deleted_at__isnull=true
GET /users?email__icontains=example
GET /users?name__contains=john doe
GET /users?sort=-id
GET /users?page=2&page_size=50
```

## Overriding query logic per view

Everything above operates on a query the view scope establishes first.
Declare the base filter as the view's
{attr}`scope <fastapi_restly.views.BaseRestView.scope>`;
{meth}`get_many <fastapi_restly.views.RestView.get_many>`,
{meth}`count <fastapi_restly.views.RestView.count>`, and
{meth}`get_one <fastapi_restly.views.RestView.get_one>` all apply it, so
the filter covers listings, totals, and single-row fetches:

```python
import fastapi_restly as fr

class UserView(fr.AsyncRestView):
    ...
    scope = fr.where_clause(User.active.is_(True))
```

[Scopes](scopes.md) owns the topic, including the model-wide
`default_scope` form and composing clauses.

The `get_many` business method does not accept a separate `query` argument.
Keep SQL-level base query changes in the scope so listing, pagination
totals, and single-row fetches all see the same visibility rules.

For a different URL **grammar** (other parameter names, another dialect's
filter syntax), the seam is
{meth}`apply_query_params(query, query_params) <fastapi_restly.views.RestView.apply_query_params>`,
which owns translating URL parameters into the query;
[React Admin Integration](howto_react_admin.md) is the shipped worked
example of a view family overriding it. Reserve overriding `get_many()`
itself for a genuinely different *result* shape, where you construct the
query explicitly inside the method.

Any route method that declares a `query_params` parameter takes the same
listing grammar as `GET /`: Restly annotates it with the view's generated
{attr}`listing_param_schema <fastapi_restly.views.BaseRestView.listing_param_schema>`,
so filters, sort and page parse and validate the same way, appear in OpenAPI,
and an unknown key is rejected with `422`. A custom listing such as a trash
route passes them on with `self.handle_get_many(query_params, scope=...)`;
see [A route names its own scope](#per-read-scope). The route can take its
own query parameters beside `query_params`, such as
`def search(self, query_params, mode: SearchMode)`.

## See also

- [List-parameters lifecycle](technical_details.md#list-parameters-lifecycle):
  how the filter grammar is generated and frozen at registration time.
- [Nested Resources](howto_nested.md): serving a child resource under its
  parent, such as `/projects/{project_id}/tasks`.
