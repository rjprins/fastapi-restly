# Filtering, Sorting & Pagination

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

Restly builds these list params when the view registers. It reads the
view's response class, and gives each field that maps to a column one filter,
plus filters with an operator suffix such as `__gte`. So a client can filter
and sort only on fields it can see. By default the response class is the
view's schema without its `WriteOnly` fields; see
[Set your own response class](#own-response-class).

By default a list has the keys of the resource's own fields. A view chooses
other keys with four settings; see [Choose the keys of a list](#choose-list-keys).
For a filter that is not a comparison with a column, see
[Add a filter of your own](#own-list-filter).

The filters narrow the rows inside the view's [scope](scopes.md). They can
never show a row that the scope hides. To limit the rows of every list,
declare the view's scope, as [Give one view other rows](#view-scope) shows.
For list params of another form, such as those of another client library,
see [Write the list params yourself](#own-list-params).

Restly rejects an unknown key with `422`. A filter only narrows the result, so
a mistyped key that Restly ignored would return more rows than the client
asked for. A view can ignore unknown keys instead; see
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

:::{admonition} Details
:class: details

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
:::

(filter-value-validation)=
### Filter values go through the field's validation

Equality, `__ne`, `__in` and the range filters validate each value with the
field's Pydantic validation, so the field validators of the schema run on
filter values too. A value that fails returns `400` (see
[Why a bad list request returns 422 or 400](#list-request-errors)).

:::{admonition} Details
:class: details

Restly validates one value at a time, without the other fields. So:

- Model validators do not run.
- {attr}`pydantic.ValidationInfo.field_name` is the Python name of the field,
  also when the URL uses an alias.
- {attr}`pydantic.ValidationInfo.data` is `{}`.

`__contains` and `__icontains` use the text as it is, and `__isnull` reads a
boolean.
:::

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

:::{admonition} Details
:class: details

Restly also splits one `__contains` or `__icontains` value at each space, so
`?name__contains=john%20doe` gives the same result. Prefer the repeated
parameter when you build the URL yourself: it has only one meaning.

Restly escapes `%`, `_` and `\` before it builds the `LIKE` condition. A
search finds the text as written; these characters are not wildcards.
:::

### Combine filters on one field

Repeat the parameter to add conditions with AND:

```text
GET /users?created_at__gte=2024-01-01&created_at__lt=2025-01-01
```

This gives `WHERE created_at >= '2024-01-01' AND created_at < '2025-01-01'`.

(sort-a-list)=
## Sort a list

The `sort` parameter takes sort keys separated by commas. A sort key is the
name of a field. A `-` before a key sorts in descending order:

```text
GET /users?sort=-created_at,name
```

By default every own field of the resource is a sort key. An unknown sort key
gets `422`. The description of `sort` in OpenAPI lists the sort keys. To
choose them, see [Choose the keys of a list](#choose-list-keys).

A dotted key sorts by a related field, when `include_sort_keys` has it:
`?sort=-city.country.code` orders by that column, and joins each step of the
path. A row without the related row stays in the list. Dotted keys follow
the rules of [Filter on a related row](#relation-filtering).

Without `sort`, the list is in primary key order. For another order when the
client sends no `sort`, set
{attr}`default_sort <fastapi_restly.views.BaseRestView.default_sort>`. It is
written as the client writes `sort`:

```python
class UserView(fr.AsyncRestView):
    ...
    default_sort = "-created_at"
```

`GET /users` is then newest first, and `GET /users?sort=name` is sorted by
name only. OpenAPI shows `-created_at` as the default of `sort`.

Restly always adds the primary key as the last sort term, every column of a
composite key included. So rows with equal values are never skipped or
repeated from one page to the next.

:::{admonition} Details
:class: details

A key of `default_sort` must be a field of the response class that can be a
sort key. `include_sort_keys` does not have to keep it, so a view can sort by
a field that the client cannot sort by. A key that the response class does
not have fails when the view registers, also when the view gets
`default_sort` from a base view. A view without those fields sets
`default_sort = None` for primary key order. An empty `?sort=` gets the
default too. An override of
{meth}`apply_list_params <fastapi_restly.views.RestView.apply_list_params>`
finds the default in `list_params.sort`. With `include_sort_keys = ()`, the
list params have no sort parameter, but the default still applies.

For a default order on a column that the response class does not have,
such as a `position` that an admin sets, order the statement when the
client sends no sort:

```python
class ItemView(fr.AsyncRestView):
    ...

    async def apply_list_params(self, stmt, list_params):
        if list_params.sort is None:
            stmt = stmt.order_by(Item.position)
        return await super().apply_list_params(stmt, list_params)
```
:::

(sort-nulls)=
### Where NULLs go

A sort puts NULLs last, in both directions and on every database. So the
users without a `last_login` come after the other users, in
`?sort=last_login` and in `?sort=-last_login`.

{attr}`sort_nulls <fastapi_restly.views.BaseRestView.sort_nulls>` sets where
NULLs go. `"first"` puts them first in both directions. `None` leaves them
where the database puts them: PostgreSQL puts them last in an ascending sort
and first in a descending sort, SQLite and MySQL the other way round. A
project base view can set `sort_nulls` once for every view.

:::{admonition} Details
:class: details

On PostgreSQL, Oracle and SQLite 3.30 or later, Restly writes `NULLS LAST`
in the SQL. MySQL and SQL Server do not have it, so Restly sorts on
`CASE WHEN ... IS NULL` first. A column of the model that cannot be NULL
gets neither. A column through a relationship always gets it, because the
row without the related row has NULL there.

The rule for NULLs can make a sort on a large table slower, because the
database cannot always read an index in that order:

- PostgreSQL reads a plain index in the order of `x NULLS LAST`, but not in
  the order of `x DESC NULLS LAST`. For a fast `?sort=-last_login`, also
  give the column an index in that order. Keep the plain index for
  `?sort=last_login`:

  ```python
  from sqlalchemy import Index

  Index("ix_user_last_login_desc", User.last_login.desc().nulls_last())
  ```

- MySQL and SQL Server cannot read a plain index in the order of the `CASE`,
  in either direction, so they sort the rows themselves.

If a sort must use a plain index, set `sort_nulls = None` on that view.
:::

### Rename the sort parameter

To keep the URLs of another library, rename the parameter with
{attr}`sort_query_param <fastapi_restly.views.BaseRestView.sort_query_param>`,
such as `"ordering"` for Django REST framework or `"order_by"` for
fastapi-filter:

```python
class UserView(fr.AsyncRestView):
    sort_query_param = "order_by"
```

Clients then send `?order_by=-created_at`, and `sort` becomes an unknown key.
A field called `sort` gets its filter key back.

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
give the field an alias, or leave the field's plain key out with
`exclude_filters = ("size",)`; see
[Choose the keys of a list](#choose-list-keys). A renamed parameter frees its old name, so a
field called `page` gets its filter back.

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

Use this only for a table that stays small, such as tags or countries. A list
without pagination returns the whole table in one response.

:::{admonition} Details
:class: details

`None` is short for
{class}`fr.NoPagination() <fastapi_restly.views.NoPagination>`. Use
`fr.NoPagination(envelope=...)` to choose the envelope of a list without
pagination; see [Change the list envelope](#change-list-envelope).
:::

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

(choose-list-keys)=
## Choose the keys of a list

A view chooses the keys of its list with
{attr}`include_filters <fastapi_restly.views.BaseRestView.include_filters>`
and
{attr}`include_sort_keys <fastapi_restly.views.BaseRestView.include_sort_keys>`:

```python
class ItemView(fr.AsyncRestView):
    ...
    include_filters = ("status", "category_id", "name__icontains", "created_at__*")
    include_sort_keys = ("name", "created_at")
```

An entry is a key as the client sends it, such as `name__icontains`, or a
pattern. In a pattern, `*` matches any characters except `.`. So
`"created_at__*"` is every filter on `created_at` with a suffix, and
`"vendor.*"` is every key on the fields of `vendor`. OpenAPI shows only the
keys that are on, and another key gets `422`.

To remove a few keys, use
{attr}`exclude_filters <fastapi_restly.views.BaseRestView.exclude_filters>`
and
{attr}`exclude_sort_keys <fastapi_restly.views.BaseRestView.exclude_sort_keys>`.
Restly applies them after the include settings:

```python
class ItemView(fr.AsyncRestView):
    ...
    exclude_filters = ("internal_note", "internal_note__*", "*__icontains")
```

:::{admonition} Details
:class: details

A `*` never matches a `.`, so each `*` covers one level only. For an item
with a `vendor`, and a `country` on the vendor, `"vendor.*"` has the keys on
the fields of `vendor`, but not those on `vendor.country`. For both levels, write
`("vendor.*", "vendor.*.*")`. A pattern has no other wildcard: `**`, `?` and
`[...]` are errors.

The pattern `"name*"` also matches a field such as `names`. Write
`("name", "name__*")` for the keys of `name` only.

`include_filters = ()` leaves no generated filters, and
`include_sort_keys = ()` removes the `sort` parameter. `page` and `page_size`
are not filters, and they always stay.

Dotted keys are off by default. Each one is a join that a client can add, and
a new field on a nested schema would add keys to every list that nests it.

An entry that a view sets and that matches no key fails when the view
registers. The message lists the keys of the field:

```text
ItemView.include_filters: 'price__icontains' matches no filter key of
ItemSchema. The filter keys of 'price' are: price, price__in, price__ne,
price__isnull, price__gte, price__lte, price__gt, price__lt. Only text fields
have __contains and __icontains.
```

A view inherits each setting on its own, as it inherits `pagination`. So a
project base view can set `include_filters = ()`, and a view turns its
filters on again with `("*",)`. An entry that a view inherits may match no
key, because a base view serves many schemas. For mypy, give the setting a
type on the base view: `include_filters: ClassVar[tuple[str, ...]] = ()`.
Without it, mypy takes the type of `()` and rejects `("*",)` in the view.

The sort keys are separate from the filter keys, so a view can sort on
`created_at` without a `?created_at=` filter. A sort key has no suffix. It
never goes through a list of related rows: an item with several tags has no
single `tags.name` to sort on.

To see the keys that a view could have, set
`include_filters = ("*", "*.*")` and `include_sort_keys = ("*", "*.*")` for a
moment, and look at `/docs`. Every entry must match a key, so leave out
`"*.*"` when the schema has no nested object. Add `"*.*.*"` for keys two
levels down.

{func}`derive_schema_list_params() <fastapi_restly.query.derive_schema_list_params>`
takes the four settings and `sort_query_param` as keywords, so a route
outside a view can build the same list params.

A key that clients use is part of your API. Removing it later breaks those
clients.
:::

(relation-filtering)=
## Filter on a related row

A dot reaches a field of a related row. These keys are off by default. Turn
them on with
{attr}`include_filters <fastapi_restly.views.BaseRestView.include_filters>`;
see [Choose the keys of a list](#choose-list-keys):

```python
class OrderView(fr.AsyncRestView):
    ...
    include_filters = ("*", "user.*")
```

```text
GET /orders?user.name=Alice
GET /orders?user.name__contains=ali
```

The relation must be a `relationship` on the SQLAlchemy model and a nested
schema on the response class, as in
[Return related objects in a response](#relationship-loading). A nested schema
that can be `None` (`UserSchema | None`) works, and so do several steps
(`?blog.author.name=Alice`). Each step needs its own `*` in the setting:
`?blog.author.name=` needs `"blog.*.*"`. A list of nested schemas
(`list[UserSchema]`) does not work.

A dotted filter joins the related table without that model's default scope;
see [Scopes](#default-scope). Restly joins through SQL aliases. If you add a
session rule with {func}`sqlalchemy.orm.with_loader_criteria`, set
`include_aliases=True`, so the rule also covers these joins. See
[Multi-Tenancy](#tenant-row-scoping).

:::{admonition} Details
:class: details

A path can follow a relationship of a model to the same model, such as
`?manager.name=Alice` or `?manager.manager.name=Alice`. Each step must be a
nested schema, and Restly follows each schema class only once in a path. So
a schema that names itself, such as `manager: EmployeeSchema | None` inside
`EmployeeSchema`, gives `?manager.name=` but not `?manager.manager.name=`.
For two steps, use another class for the second step:

```python
class EmployeeName(fr.IDSchema):
    name: str


class ManagerSchema(EmployeeName):
    manager: EmployeeName | None = None


class EmployeeSchema(EmployeeName):
    manager: ManagerSchema | None = None
```

Two relationships to the same model can be filtered together:

```text
GET /people?home_city.name=Amsterdam&work_city.name=Berlin
GET /people?home_city.country.code=NL&work_city.country.code=DE
```

Sorting accepts the same paths, when `include_sort_keys` has them. A filter
and a sort on the same relationship share one join. The join of a filter is
an inner join: a row without the related row is left out, also for a dotted
`__isnull` filter. The join of a sort is a `LEFT OUTER JOIN`, so the row
stays, and its value counts as NULL in the sort.

Aliases apply to every part of the path, the relationship and the field,
because the list params always use the public names of the response
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
:::

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

:::{admonition} Details
:class: details

A `MustExist[pk, ...]` field gets the filters of its `pk` type. An `int` id
gets equality, `__in`, `__ne`, `__isnull` and the range suffixes. A `UUID` id
gets no range suffixes, as the [list of suffixes](#filter-a-list) says. Only a
`str` id gets `__contains` and `__icontains`.

A relationship reference filters differently (see
[Choose a reference style](#reference-styles)):

- An {class}`IDSchema[Model] <fastapi_restly.schemas.IDSchema>` field filters
  by its id, as in `?author.id=1`, with the filters of the id's type. That key
  has a dot, so the view turns it on first, for example with
  `include_filters = ("*", "author.*")`.
- A flat {class}`IDRef[Model] <fastapi_restly.schemas.IDRef>` field on a
  relationship gets no filter. To filter by it, also put the foreign key
  column in the response class, for example as `author_id: fr.ReadOnly[int]`.
:::

(public-names)=
## Use aliases in the URL

The keys of the list params use the **public name** of each field: its
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

(own-list-filter)=
## Add a filter of your own

For a filter that is not a comparison with a column, decorate a view method
with {func}`fr.list_filter <fastapi_restly.views.list_filter>`:

```python
from sqlalchemy import ColumnElement


class ItemView(fr.AsyncRestView):
    ...

    @fr.list_filter("popular")
    def filter_popular(self, value: bool) -> ColumnElement[bool]:
        """Items with more than 1000 views, or with false the other items."""
        popular = Item.view_count > 1000
        return popular if value else ~popular
```

The client sends `?popular=true`. The type of `value` becomes the type of
the key: OpenAPI shows a boolean, with the docstring as its description, and
`?popular=maybe` gets `422`. The method gets the value after validation,
never the text from the URL.

The method returns a SQLAlchemy condition, a [clause](clauses.md), or `None`
for no condition. Restly adds the condition with AND, inside the view's
scope, after the generated filters.

Restly calls the method only when the key has a value. With a default on
`value`, it calls the method on every list request, with the default when the
key is missing. This list hides archived items, unless the client asks for
them:

```python
@fr.list_filter("allow_archived")
def filter_allow_archived(self, value: bool = False) -> ColumnElement[bool] | None:
    return None if value else ~Item.archived
```

A filter on a column lets a client find out the values of that column, one
request at a time. Write a filter only on data that the client may see.

:::{admonition} Details
:class: details

`value: list[str]` reads a repeated key, as in `?tag=a&tag=b`. A plain type
takes the last value, as in FastAPI.

The condition may use only the tables that are in the statement. A condition
on another table, such as `Vendor.name == value`, raises `TypeError`, because
SQL would add the table as a cross join that the scope does not reach. Use the
relationship instead: `Item.vendor.has(Vendor.name == value)`.

On an {class}`AsyncRestView <fastapi_restly.views.AsyncRestView>`, the
method can be `async def`, to load data with `self.session` first. It runs
when a plain method would, and only on routes that filter. Often a
subquery in the condition is enough, and it is faster than loading ids for
`IN (...)`. On a {class}`RestView <fastapi_restly.views.RestView>`, an
`async def` method fails when the class is defined.

The key is always on: `include_filters` and `exclude_filters` choose among
the generated keys only. A key with the name of a generated key that is on
fails when the view registers. To replace `name`, leave the generated key out
with `exclude_filters = ("name",)`, and decorate your own.

A subclass inherits the key. It drops the key by setting the name of the
method to `None`, as in `filter_popular = None`. A type checker rejects
`None` in place of a method, so give it a type: `filter_popular: Any = None`.
An override without the decorator fails when the class is defined.

Restly runs the filter methods only for the view's own list params: those of
a list route, or a dict or a `QueryParams` that a method gets. A Pydantic
model of another class, such as one of your own, does not run them, and
Restly warns with
{class}`RestlySkippedFilterMethodsWarning <fastapi_restly.exc.RestlySkippedFilterMethodsWarning>`.

A key follows the rules of the page parameters: no `__`, no `.` and no `_`
at the start. It is not the name of the view's page or sort parameters, such
as `page`, `page_size` or `sort`, and not the name of a Pydantic model
attribute, such as `copy`.

{func}`fr.query.apply_list_params <fastapi_restly.query.apply_list_params>`
has no view, so it cannot call the method. It raises `ValueError` for list
parameters with a value for the key. A key with a default always has a
value. Call the view's
{meth}`apply_list_params <fastapi_restly.views.RestView.apply_list_params>`
instead.
:::

(extra-query-parameters)=
## Accept extra query parameters

The unknown-key check accepts every query parameter that the route declares.
So declare a key of your own where your code uses it. Then the key has a
type, and OpenAPI shows it next to the filters. A key that changes the
response is a parameter of the endpoint method:

```python
from fastapi.responses import PlainTextResponse


class ItemView(fr.AsyncRestView):
    ...

    @fr.get("/")
    async def get_many_endpoint(self, list_params, as_text: bool = False):
        if not as_text:
            return await super().get_many_endpoint(list_params)
        result = await self.handle_get_many(list_params)
        return PlainTextResponse("\n".join(item.name for item in result.objects))
```

The client sends `?as_text=true`, and `?as_text=maybe` gets `422`. A key
that filters the rows is [a filter of your own](#own-list-filter). A value
that other methods or the scope need comes from a dependency: a
[class attribute](#dependency-injection-on-class-attributes) with `Depends`,
or a member of [`Current`](#current) that
{meth}`Current.depends <fastapi_restly.clauses.ContextNamespace.depends>`
binds.

Some keys are only known at run time, such as a family of keys
`extra.<name>` that your code reads from `self.request.query_params`. A
route cannot declare them. For these, set
{attr}`unknown_query_params <fastapi_restly.views.BaseRestView.unknown_query_params>`
to `"ignore"`. The view then accepts every key that the route does not read,
and leaves it out, as FastAPI does:

```python
class ItemView(fr.AsyncRestView):
    ...
    unknown_query_params = "ignore"
```

Then a mistyped filter key is not an error, and the client gets more rows
than it asked for. The default is `"forbid"`. These are the values of
Pydantic's `extra` setting.

:::{admonition} Details
:class: details

A key that a dependency reads is accepted wherever the dependency is
declared: on the app or a router, in the view's `dependencies`, or in a
class attribute. The key of an `APIKeyQuery` is accepted too. A project base
view can set `unknown_query_params` once for every view.
:::

(filters-in-own-route)=
## Use the filters in a route of your own

{meth}`apply_filters <fastapi_restly.views.RestView.apply_filters>` applies
the client's filters to a statement, without the sort and the page. This
route counts the items per category, under the client's other filters:

```python
@fr.get("/category-counts")
async def category_counts(self, list_params) -> dict[str, int]:
    await self.authorize(fr.Action.GET_MANY)
    stmt = sa.select(Item.category, sa.func.count()).group_by(Item.category)
    stmt = fr.apply_clauses(stmt, fr.resolve_scope(self))
    others = list_params.model_copy(update={"category": None})
    stmt = await self.apply_filters(stmt, others)
    return dict((await self.session.execute(stmt)).all())
```

The route calls `authorize` and applies the scope itself, as every read
route of your own does; see [Use the scope in your own query](#reading-the-scope).
It removes the client's own `category` filter. Otherwise the result has only
the category that the client chose.

:::{admonition} Details
:class: details

{meth}`apply_list_params <fastapi_restly.views.RestView.apply_list_params>`
would add `ORDER BY` and `LIMIT`. PostgreSQL rejects an order on a column
that the `GROUP BY` does not have.

A real route also removes `category__in` and the other keys of `category`.
The route still documents `page`, `page_size` and `sort`, and ignores them.

A route method with a `list_params` parameter takes the same list params as
`GET /`. Restly gives it the view's
{attr}`schema_list_params <fastapi_restly.views.BaseRestView.schema_list_params>`,
so filters, sort and page are read and checked in the same way, OpenAPI shows
them, and an unknown key returns `422`. A custom list route, such as a trash
route, passes them on with `self.handle_get_many(list_params, scope=...)`; see
[Read other rows on one route](#per-read-scope). The route can take its own
query parameters next to `list_params`, as in
`def search(self, list_params, mode: SearchMode)`.

A route can also pass `self.request.query_params` on, in place of
`list_params`. Restly reads the keys of the list params from it and leaves
out the keys that the route reads, such as `mode` above. Any other key gets
`422`, as on the list route, or is left out when the view sets
`unknown_query_params = "ignore"`. OpenAPI does not show the list keys of
such a route. A dict, as in `view.get_many({"name": "axe"})`, holds list
params only, so a key that they do not have always gets `422`.

On an async view, `apply_filters` is `async def`, as `apply_list_params` is.
A view can override it to add a condition to every list, and to every route
that calls it. `apply_list_params` calls it first. Add the condition to the
statement before `super().apply_filters(...)`. After it, a sort on a
relationship that a filter joined joins that table a second time. A join
that every list needs goes in an
{meth}`apply_list_params <fastapi_restly.views.RestView.apply_list_params>`
override. A route of your own that reads rows of the model starts from
`self.select()`, which has the scope, as
[Use the scope in your own query](#reading-the-scope) shows.
:::

(own-list-params)=
## Write the list params yourself

For list params that Restly does not generate, such as one search key, or
the keys of another client library, write them as a Pydantic model. Declare
it as
{attr}`schema_list_params <fastapi_restly.views.BaseRestView.schema_list_params>`,
and apply it to the statement in
{meth}`apply_filters <fastapi_restly.views.RestView.apply_filters>` and
{meth}`apply_list_params <fastapi_restly.views.RestView.apply_list_params>`:

```python
from typing import Literal

import pydantic


class ItemSearch(pydantic.BaseModel):
    q: str | None = None
    order: Literal["name", "-name"] = "name"
    page: int = pydantic.Field(1, ge=1)
    page_size: int = pydantic.Field(20, ge=1, le=100)


class ItemView(fr.AsyncRestView):
    prefix = "/items"
    model = Item
    schema = ItemSchema
    schema_list_params = ItemSearch

    async def apply_filters(self, stmt, list_params: ItemSearch):
        if list_params.q:
            stmt = stmt.where(Item.name.icontains(list_params.q))
        return stmt

    async def apply_list_params(self, stmt, list_params: ItemSearch):
        stmt = await self.apply_filters(stmt, list_params)
        name = Item.name.desc() if list_params.order == "-name" else Item.name
        offset = (list_params.page - 1) * list_params.page_size
        return stmt.order_by(name, Item.id).limit(list_params.page_size).offset(offset)
```

The client sends `GET /items/?q=axe&order=-name&page=2`. Restly reads the
model from the query string, as on every list route: a bad value or an
unknown key gets `422`, and OpenAPI shows each field. The scope still
applies, and `total_count` counts the rows that `apply_filters` keeps. The
envelope takes the page and the page size from the fields with the names of
the pagination parameters, here `page` and `page_size`.

:::{admonition} Details
:class: details

On a sync view, the two methods are `def`. `apply_list_params` adds the
order and the page. Order by a unique column last, such as `Item.id`, so
that a row never moves to another page.

The list settings, such as `include_filters`, `sort_query_param` and
`default_sort`, and
{func}`fr.list_filter <fastapi_restly.views.list_filter>` apply to the list
params that Restly generates. A view with list params of its own that has
one of them fails when it registers. For a default order in list params of
your own, give their sort field a default, as `order` has here. A subclass with a new `schema` gets
generated list params, so it declares its own again. The
[React Admin](react_admin.md) views have list params of their own: `sort`,
`range` and `filter`.
:::

## Internals

(list-parameters-lifecycle)=
### How Restly builds the list params

In {meth}`before_include_view() <fastapi_restly.views.BaseRestView.before_include_view>`,
Restly sets one class attribute,
{attr}`schema_list_params <fastapi_restly.views.BaseRestView.schema_list_params>`.
It is a Pydantic model that
{func}`derive_schema_list_params(cls.schema_response, cls.model, pagination=cls.pagination) <fastapi_restly.query.derive_schema_list_params>`
generates, with the view's list settings. It holds the pagination
parameters, the sort parameter, the filter keys that the settings keep, and
the view's own keys. Restly builds it once, when the view registers.

A route reads the list params through a dependency, not as a FastAPI
query model. FastAPI splits a query model into separate parameters only when
it is the only query parameter of the route. Next to a dependency that reads
`?api_key=`, the filters would become one required object. So Restly
validates the model from the query string itself, and shows each key as its
own parameter in OpenAPI, next to the other parameters of the route.

Apart from `__isnull`, the generated filter values are text in this model.
Restly validates them against the field when
{func}`apply_list_params <fastapi_restly.query.apply_list_params>` builds the
query. A key of your own has the type of its method's value.

## See also

- [Responses](responses.md): the list envelope and its fields.
- [Errors](errors.md): the `422` and `400` answers to a bad list
  request.
- [Nest a resource under its parent](#nested-resources): a child resource under its parent,
  such as `/projects/{project_id}/tasks`.
