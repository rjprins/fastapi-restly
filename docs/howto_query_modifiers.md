# Filtering & Pagination

The list endpoint of a view, `GET /{prefix}`, reads filters, a sort order and
a page from the URL:

```text
GET /users?name=John
GET /users?status=active,pending
GET /users?age__gte=18&age__lt=65
GET /users?deleted_at__isnull=true
GET /users?email__icontains=example
GET /users?sort=-created_at,name
GET /users?page=2&page_size=50
```

Restly builds these list parameters when the view registers. It reads the
view's response class, and gives each field that maps to a column one filter,
plus filters with an operator suffix such as `__gte`. So a client can filter
and sort only on fields it can see. By default the response class is the
view's schema without its `WriteOnly` fields; see
[Set your own response class](#own-response-class).

The filters narrow the rows inside the view's [scope](scopes.md). They can
never show a row that the scope hides.

Restly rejects an unknown key with `422`. A filter only narrows the result, so
a mistyped key that Restly ignored would return more rows than the client
asked for. To allow a key of your own, see
[Accept extra query parameters](#extra-query-parameters).

Lists are paginated by default: 50 rows per page, and a client can ask for up
to 1000. The rows come in a `data` envelope with the total count. The view's
{attr}`pagination <fastapi_restly.views.BaseRestView.pagination>` setting
changes the limits and the parameter names, or turns pagination off.

(filter-a-list)=
## Filter a list

A filter compares a field with a value from the URL. Plain equality uses the
name of the field:

```text
GET /users?name=John
```

A suffix on the name chooses another comparison:

| Suffix | SQL | Example |
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
| `__isnull` | `field IS NULL` or `IS NOT NULL` | `?deleted_at__isnull=true` |

`__isnull` takes `true` or `false`, not the text `null`.

Not every field gets every suffix:

- The range suffixes (`__gte`, `__lte`, `__gt`, `__lt`) exist only for types
  that have an order, such as numbers, text and dates. Booleans and UUIDs do
  not get them.
- `__contains` and `__icontains` exist only for text fields.
- A field that holds a `dict` or a `list`, such as a `JSON` or `ARRAY`
  column, gets only `__isnull`. A value from the URL cannot become a
  collection, so the other filters would fail on every request.
- A {data}`WriteOnly <fastapi_restly.schemas.WriteOnly>` field, or a field
  with `Field(exclude=True)`, gets no filter and is not a sort key. A filter
  on it would let a client find out its value.

A date and time without a UTC offset is read as UTC when the column stores a
time zone. On a base with
{class}`fr.ModelDefaults <fastapi_restly.models.ModelDefaults>`,
`Mapped[datetime]` stores one by default. A column declared `DateTime()`
compares the value as it is.

(filter-value-validation)=
### Filter values go through the field's validation

Equality, `__ne`, `__in` and the range filters validate each value with the
field's Pydantic validation, so the field validators of the schema run on
filter values too. A value that fails returns `400` (see
[Why a bad list request returns 422 or 400](#list-request-errors)).

Restly validates one value at a time, without the other fields. So:

- Model validators do not run.
- {attr}`pydantic.ValidationInfo.field_name` is the Python name of the field,
  also when the URL uses an alias.
- {attr}`pydantic.ValidationInfo.data` is `{}`.

`__contains` and `__icontains` use the text as it is, and `__isnull` reads a
boolean.

### Match one of several values

Values separated by commas in a plain equality filter are combined with OR:

```text
GET /users?status=active,pending
```

This gives `WHERE status = 'active' OR status = 'pending'`, which is the same
as SQL `IN`. To make `IN` visible in the URL, use `__in`:

```text
GET /users?status__in=active,pending
```

### Exclude several values

Values separated by commas in `__ne` are combined with AND. A row must differ
from every value:

```text
GET /users?status__ne=archived,deleted
```

This gives `WHERE status != 'archived' AND status != 'deleted'`, which is the
same as SQL `NOT IN`.

### Search for text

To require several pieces of text at once, repeat the parameter. Each value
becomes its own `LIKE` or `ILIKE` condition, and the conditions are combined
with AND:

```text
GET /users?name__contains=john&name__contains=doe
GET /users?name__icontains=john&name__icontains=doe
```

`__contains` gives `WHERE name LIKE '%john%' AND name LIKE '%doe%'`.
`__icontains` uses `ILIKE`.

Restly also splits one `__contains` or `__icontains` value at each space, so
`?name__contains=john%20doe` gives the same result. Prefer the repeated
parameter when you build the URL yourself: it has only one meaning.

Restly escapes `%`, `_` and `\` before it builds the `LIKE` condition. A
search finds the text as written; these characters are not wildcards.

### Combine filters on one field

Repeat the parameter to add conditions with AND:

```text
GET /users?created_at__gte=2024-01-01&created_at__lt=2025-01-01
```

This gives `WHERE created_at >= '2024-01-01' AND created_at < '2025-01-01'`.

(relation-filtering)=
## Filter on a related row

A dot reaches a field of a related row:

```text
GET /orders?user.name=Alice
GET /orders?user.name__contains=ali
```

The relation must be a `relationship` on the SQLAlchemy model and a nested
schema on the response class, as in
[Return related objects in a response](#relationship-loading). A nested schema
that can be `None` (`UserSchema | None`) works, and so do several steps
(`?blog.author.name=Alice`). A list of nested schemas (`list[UserSchema]`)
does not.

A path can follow a relationship of a model to the same model, such as
`?manager.name=Alice` or `?manager.manager.name=Alice`. Each step must be a
nested schema. Two relationships to the same model can be filtered together:

```text
GET /people?home_city.name=Amsterdam&work_city.name=Berlin
GET /people?home_city.country.code=NL&work_city.country.code=DE
```

Sorting accepts the same paths. A filter and a sort on the same relationship
share one join. The joins are inner joins: a row without the related row is
left out, also for a dotted `__isnull` filter.

A dotted filter joins the related table without that model's default scope;
see [Scopes](#default-scope). Restly joins through SQL aliases. If you add a
session rule with {func}`sqlalchemy.orm.with_loader_criteria`, set
`include_aliases=True`, so the rule also covers these joins. See
[Keep each tenant's rows apart](#tenant-row-scoping).

Aliases apply to every part of the path, the relationship and the field,
because the list parameters always use the public names of the response
class:

```python
from pydantic import Field


class AuthorSchema(fr.IDSchema):
    name: str = Field(alias="authorName")


class ArticleSchema(fr.IDSchema):
    author: AuthorSchema = Field(alias="writer")
```

The URL must then use the aliases:

```text
GET /articles?writer.authorName=Alice    # accepted
GET /articles?author.name=Alice          # rejected: use the aliases
```

(foreign-key-filtering)=
## Filter by a foreign key

A foreign key declared with
{class}`fr.MustExist[int, Post] <fastapi_restly.schemas.MustExist>` filters by
its own public name, the same name it has in JSON:

```text
GET /comments?post_id=1
GET /comments?post_id=1,2          # OR, as SQL IN
GET /comments?post_id__in=1,2
GET /comments?post_id__ne=1
GET /comments?post_id__gte=10      # range suffixes work on int ids
GET /comments?post_id__isnull=true
```

A `MustExist[pk, ...]` field gets the filters of its `pk` type. An `int` id
gets equality, `__in`, `__ne`, `__isnull` and the range suffixes. A `UUID` id
gets no range suffixes, as the [list of suffixes](#filter-a-list) says. No id
gets `__contains`.

A relationship reference filters differently (see
[Choose a reference style](#reference-styles)):

- An {class}`IDSchema[Model] <fastapi_restly.schemas.IDSchema>` field filters
  by its id, as in `?author.id=1`, with equality, `__in`, `__ne` and
  `__isnull`.
- A flat {class}`IDRef[Model] <fastapi_restly.schemas.IDRef>` field on a
  relationship gets no filter. To filter by it, also put the foreign key
  column in the response class, for example as `author_id: fr.ReadOnly[int]`.

(public-names)=
## Use aliases in the URL

The keys of the list parameters use the **public name** of each field: its
alias when it has one, and its Python name otherwise. The URL accepts only the
public name. `populate_by_name` changes how Pydantic reads a request body. It
does not add the Python names to the URL.

Take a field with an alias:

```python
from typing import Annotated

from pydantic import Field


class UserSchema(fr.IDSchema):
    user_name: Annotated[str, Field(alias="userName")]
```

The URL accepts only the alias:

```text
GET /users?userName=Alice        # accepted
GET /users?user_name=Alice       # rejected: the Python name is not public
```

To use another key in the URL, change the alias.

(sort-a-list)=
## Sort a list

The `sort` parameter takes field names separated by commas. A `-` before a
name sorts that field in descending order:

```text
GET /users?sort=-created_at,name
```

A dotted path sorts by a related field, as it filters:
`?sort=-city.country.code` orders by that column, and joins each step of the
path.

Restly always adds the primary key as the last sort term, every column of a
composite key included. So rows with equal values are never skipped or
repeated from one page to the next. Without `sort`, the list is in primary key
order.

(list-pagination)=
## Page through a list

The client asks for a page with `page` and `page_size`:

```text
GET /users?page=2&page_size=50
```

`page` starts at 1. `page_size` must be at least 1 and at most
`max_page_size`, 1000 by default. Without `page_size`, the endpoint uses
`default_page_size`, 50 by default. Restly also rejects a page whose SQL
offset, `(page - 1) * page_size`, is larger than `2**63 - 1`. A value out of
range returns FastAPI's standard `422`.

The response holds the page in `data`, with `total_count`, `page`,
`page_size` and `total_pages`. To change this envelope, see
[Change the list envelope](#change-list-envelope).

Set the limits with
{class}`fr.NumberedPagination <fastapi_restly.views.NumberedPagination>` on the
view class. On a public endpoint, use a smaller page size and maximum.
`max_page` also limits the page number:

```python
class UserView(fr.AsyncRestView):
    pagination = fr.NumberedPagination(
        default_page_size=25, max_page_size=200, max_page=100
    )
```

### Rename the page parameters

Rename the two parameters with `page_query_param` and
`page_size_query_param`:

```python
class UserView(fr.AsyncRestView):
    pagination = fr.NumberedPagination(
        page_query_param="p", page_size_query_param="size"
    )
```

Clients then send `?p=2&size=50`, `page` and `page_size` become unknown keys,
and OpenAPI shows the new names. A schema field with the same public name as
a pagination parameter fails when the view registers: rename the parameter,
or give the field an alias. A renamed parameter frees its old name, so a field
called `page` gets its filter back.

(set-pagination-once)=
### Set the pagination once for every view

Views inherit `pagination`, so a project base view sets it once. A view that
differs changes one setting with `replace()`, which returns a checked copy:

```python
APP_PAGINATION = fr.NumberedPagination(
    page_size_query_param="size", max_page_size=100
)


class AppView(fr.AsyncRestView):
    pagination = APP_PAGINATION


class LogView(AppView):
    pagination = APP_PAGINATION.replace(default_page_size=10, max_page_size=10)
```

(no-pagination)=
### Return every row without pagination

Set `pagination = None` to return every matching row:

```python
@fr.include_view(app)
class TagView(fr.AsyncRestView):
    prefix = "/tags"
    model = Tag
    schema = TagSchema
    pagination = None
    # Response: {"data": [ /* every tag */ ]}
```

The list has no `page` and `page_size` parameters, and Restly runs no count
query. Filters and `sort` still work. The response keeps the `data` envelope,
without the count and page fields.

`None` is short for
{class}`fr.NoPagination() <fastapi_restly.views.NoPagination>`. Use
`fr.NoPagination(envelope=...)` to choose the envelope of a list without
pagination; see [Change the list envelope](#change-list-envelope).

Use this only for a table that stays small, such as tags or countries. A list
without pagination returns the whole table in one response.

(fastapi-pagination-migration)=
### Coming from fastapi-pagination

The default `Page` of fastapi-pagination takes `?page=` and `?size=`, and
returns `items`, `total`, `page`, `size` and `pages`. To keep that contract
for existing clients, rename the page size parameter and the fields of the
envelope:

```python
from typing import Generic, TypeVar

import pydantic

T = TypeVar("T")


class Page(pydantic.BaseModel, Generic[T]):
    data: list[T] = pydantic.Field(alias="items")
    total_count: int = pydantic.Field(alias="total")
    page: int
    page_size: int = pydantic.Field(alias="size")
    total_pages: int = pydantic.Field(alias="pages")


class AppView(fr.AsyncRestView):
    pagination = fr.NumberedPagination(
        page_size_query_param="size", max_page_size=100, envelope=Page
    )
```

The default page size of 50 and the maximum of 100 match those of
fastapi-pagination. fastapi-pagination has no filters and no sorting. Restly
adds both, as this page describes.

(extra-query-parameters)=
## Accept extra query parameters

A query parameter that the route declares needs no setup: a parameter of the
endpoint method, or one that a dependency reads, wherever the dependency is
declared. That is the app or a router, the view's `dependencies`, or a class
attribute. The key of an `APIKeyQuery` counts too. The unknown-key check
accepts these keys, and OpenAPI shows them next to the filters.

A view that reads a key straight from `self.request.query_params`, in an
override, must name it. Otherwise the unknown-key check rejects it with
`422`:

```python
class UserView(fr.AsyncRestView):
    extra_query_params = ("include_deleted",)
```

(change-list-query)=
## Change the list query of a view

The filters apply to a query that the view's scope builds first. To limit
the rows of a view, declare its
{attr}`scope <fastapi_restly.views.BaseRestView.scope>`.
{meth}`get_many <fastapi_restly.views.RestView.get_many>`,
{meth}`count <fastapi_restly.views.RestView.count>` and
{meth}`get_one <fastapi_restly.views.RestView.get_one>` all apply it, so the
list, the total and the single-row reads agree:

```python
class UserView(fr.AsyncRestView):
    ...
    scope = fr.where_clause(User.active.is_(True))
```

[Scopes](scopes.md) owns this topic, including the `default_scope` of a model.
`get_many` takes no `query` argument, so put base query changes in the scope.

For another URL grammar, such as the filter syntax of another client
library, override
{meth}`apply_list_params(query, list_params) <fastapi_restly.views.RestView.apply_list_params>`.
It turns the URL parameters into the query. The
[React Admin](howto_react_admin.md) views override it for the react-admin
grammar. Override `get_many()` itself only for a list that has another
*result*, and build the query in the method.

A route method with a `list_params` parameter takes the same list parameters
as `GET /`. Restly gives it the view's
{attr}`schema_list_params <fastapi_restly.views.BaseRestView.schema_list_params>`,
so filters, sort and page are read and checked in the same way, OpenAPI shows
them, and an unknown key returns `422`. A custom list route, such as a trash
route, passes them on with `self.handle_get_many(list_params, scope=...)`; see
[Read other rows on one route](#per-read-scope). The route can take its own
query parameters next to `list_params`, as in
`def search(self, list_params, mode: SearchMode)`.

## Internals

(list-parameters-lifecycle)=
### How Restly builds the list parameters

In {meth}`before_include_view() <fastapi_restly.views.BaseRestView.before_include_view>`,
Restly sets one class attribute,
{attr}`schema_list_params <fastapi_restly.views.BaseRestView.schema_list_params>`.
It is a Pydantic model that
{func}`derive_schema_list_params(cls.schema_response, cls.model, pagination=cls.pagination) <fastapi_restly.query.derive_schema_list_params>`
generates. It holds the pagination parameters, `sort`, and the filters for
each field of the response class that maps to a column of the model. Restly
builds it once, when the view registers, and never again.

A route reads the list parameters through a dependency, not as a FastAPI
query model. FastAPI splits a query model into separate parameters only when
it is the only query parameter of the route. Next to a dependency that reads
`?api_key=`, the filters would become one required object. So Restly
validates the model from the query string itself, and shows each key as its
own parameter in OpenAPI, next to the other parameters of the route.

Apart from `__isnull`, the filter values are text in this model. Restly
validates them against the field when
{func}`apply_list_params <fastapi_restly.query.apply_list_params>` builds the
query.

The [React Admin](howto_react_admin.md) views read another grammar, with
their own parameters.

## See also

- [Responses](howto_response_schema.md): the list envelope and its fields.
- [Errors](howto_error_responses.md): the `422` and `400` answers to a bad list
  request.
- [Nest a resource under its parent](#nested-resources): a child resource under its parent,
  such as `/projects/{project_id}/tasks`.
