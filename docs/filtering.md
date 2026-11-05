# Filtering, Sorting & Pagination

The list endpoint of a view, `GET /{prefix}`, reads filters, a sort order and
a page from the URL:

```text
GET /users?name=John
GET /users?status__in=active,pending
GET /users?age__gte=18&age__lt=65
GET /users?deleted_at__isnull=true
GET /users?email__icontains=example
GET /users?sort=-created_at,name
GET /users?page=2&page_size=50
```

Restly builds these list params once for each view. It reads the view's
response schema, and gives each field that maps to a column one filter,
plus filters with an operator suffix such as `__gte`. So a client can filter
and sort only on the fields that go out. By default the response schema is
the view's main schema without its `WriteOnly` fields; see
[Set your own response schema](#own-response-schema).

By default a list has the keys of the resource's own fields. A view chooses
other keys with four settings; see [Choose the keys of a list](#choose-list-keys).
For a filter that the generated keys do not give, see
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
changes the limits and the parameter names, pages
[with limit and offset](#limit-offset-pagination), or turns pagination off.

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
| `__ne` | `field != value`, or NULL | `?status__ne=archived` |
| `__not_in` | `field NOT IN (...)`, or NULL | `?status__not_in=archived,deleted` |
| `__icontains` | `field ILIKE '%value%'` | `?email__icontains=example` |
| `__isnull` | `field IS NULL` or `IS NOT NULL` | `?deleted_at__isnull=true` |

`__isnull` takes `true` or `false`, not the text `null`.

`__ne` and `__not_in` keep the rows where the field is NULL. SQL leaves these
rows out, but a client that asks for "not archived" also means the row without a
status.

Each key has the type of its field. OpenAPI shows this type, and a value of
the wrong type gets `422`; see
[Filter values go through the field's validation](#filter-value-validation).

:::{admonition} Details
:class: details

Not every field gets every suffix:

- The range suffixes (`__gte`, `__lte`, `__gt`, `__lt`) exist only for types
  that have an order, such as numbers and dates. Booleans, UUIDs and labels do
  not get them: an enum, or a `Literal` of strings. An int enum, such as an
  `IntEnum`, has the order of its numbers and gets them. A flag, such as an
  `IntFlag`, gets the other filters, but no range.
- A text column gets no range suffixes, because the database sorts its values
  as text. So `"9"` comes after `"10"`, and the order of letters depends on the
  database. The one exception is a field of type `date` on a text column,
  because dates in the form `2024-01-31` sort in order of time.
- A column of SQLAlchemy's `Enum` type gets no range suffixes, whatever the
  type of the field, because it holds labels. `Mapped[Status]`, with an enum
  `Status`, is such a column by default. So an int enum needs a column that
  stores the number, such as `Integer`. On PostgreSQL, a native ENUM orders
  its labels as they were declared. To filter by that order, write the range
  yourself; see [Add a filter of your own](#own-list-filter).
- `__icontains` exists only for a field on a text column, whatever the type of
  the field. So an `HttpUrl` field on a text column has it, and a `str` field on
  a number column does not. Labels do not have it: a column of SQLAlchemy's
  `Enum` type, an enum, a `Literal` and a `bool`. On a
  {class}`~sqlalchemy.types.TypeDecorator` column, only a `str` field has it,
  because SQLAlchemy runs the type on the search text too.
- `__isnull` exists only on a column that can be NULL, and on each dotted key,
  because a related row can be missing. On another column, it would give the
  same answer for each row.
- A field that holds a `dict` or a `list` has no filter but `__isnull`. This
  covers a `JSON` or `ARRAY` column, and a `pydantic.Json[list]` or
  `pydantic.Json[dict]` field on any column. The other filters would fail or
  find no row on every request. Such a field
  is no sort key either: a document or a list has no order that a client
  means, and PostgreSQL's `json` type has none at all.
- A field on a JSON column has no filter but `__isnull` too, also when it holds one
  value, such as a `str` in `fr.models.PydanticJSON(str)`. The column holds
  JSON text, such as `"x"` with its quotes, and SQL compares that text, not
  the value. So the other filters would find no row on SQLite and fail on
  PostgreSQL. Such a field is no sort key either. In the `filter` of a
  react-admin view, and in `FilterKey.parse()`, such a field takes only
  `null`. To filter on a key in a document, write the filter yourself; see
  [Add a filter of your own](#own-list-filter).
- A {data}`WriteOnly <fastapi_restly.schemas.WriteOnly>` field, a field
  with `Field(exclude=True)`, and a field of Pydantic's secret types, such as
  `SecretStr`, also in a union such as `SecretStr | int`, get no filter and
  are not a sort key. A filter on such a field
  would let a client find out its value. Restly cannot see a value that a
  serializer hides, such as a number shown as `***-1234`. Leave such a field
  out with `exclude_filters` and `exclude_sort_keys`.

A filter compares a time in UTC: a time without an offset is UTC, and a
time with an offset is compared as the same time in UTC. See
[Keep times in UTC](#utc-times).

A `column_property` counts as a column. So a value that SQL computes for
each row, such as the number of tasks of a project, gets filters and a sort
key too, such as `?task_count__gte=1` and `?sort=-task_count`.
[Add a value to each row](#value-on-each-row) shows such a value.
:::

(filter-value-validation)=
### Filter values go through the field's validation

Each filter value goes through the Pydantic validation of its field. The type
counts, and so do the field validators of the schema. A value that fails gets
`422`, in the form that FastAPI uses for any query parameter, and `loc` names
the key:

```json
{
  "detail": [
    {
      "type": "int_parsing",
      "loc": ["query", "age__gte"],
      "msg": "Input should be a valid integer, unable to parse string as an integer",
      "input": "old"
    }
  ]
}
```

A filter compares a value and does not store it, so the bounds of the field do
not count, such as `gt` or `min_length`. With `price: Decimal = Field(gt=0)`,
`?price__gte=0` works, and `?price=-1` finds no rows. OpenAPI does not show
bounds on these keys.

What changes a value stays, also for a filter. So a validator that turns euros
into cents does this on `?cents=10` and on `?cents__gte=10`. A rich type stays
too: `?site=https://a.example` compares with `https://a.example/`, as a
request body stores it. So a client finds a row with the text that it sent.

A value that a validator turns into `None` gets `422`, such as empty text that
a validator makes `None`. A filter needs a value. To find the rows without a
value, the client sends `?note__isnull=true`, and the message says so. An
empty `__icontains` stays no filter.

:::{admonition} Details
:class: details

Restly validates one value at a time, without the other fields. So:

- Model validators do not run.
- {attr}`pydantic.ValidationInfo.field_name` is the Python name of the field,
  also when the URL uses an alias.
- {attr}`pydantic.ValidationInfo.data` is `{}`.

The list params hold the value after this validation, such as a `Decimal` in
`list_params.price__gte`, or a list in `list_params.price__in`. A reference
gives its id.

A field of type `Any`, such as the `id` of
{class}`fr.IDSchema <fastapi_restly.schemas.IDSchema>`, gives the value the
Python type of its column, such as a `UUID` from its text. On a column of
SQLAlchemy's `Enum` type, the value must also be one of its labels. A
`Literal` of numbers, such as `Literal[1, 2]`, and an enum whose values are
numbers read the number from the text, as an `IntEnum` does. So `?level=1`
works. A number
must fit its integer column, such as the range of `Integer`, and text must hold
no NUL character; see [Column validation](#column-validation). A filter does
not check the length of text.

A number in a filter must be between `-1e50` and `1e50`, as in django-filter.
So a huge number, such as `?price=1e999999`, gets `422` and never reaches the
database. A NaN stays when the field allows it. For a bigger number, write
[a filter of your own](#own-list-filter).

A bound stays when Pydantic checks it with a function. This happens after a
validator of your own, on a union such as `Decimal | int`, and on some types,
such as `HttpUrl`. Restly cannot tell such a function from your own check.

`__icontains` uses the text as it is. It only refuses a NUL character. `__isnull`
reads a boolean.
:::

(match-several-values)=
### Match several values

`__in` and `__not_in` take several values. Separate them with commas, or repeat
the key:

```text
GET /users?status__in=active,pending
GET /users?status__in=active&status__in=pending
GET /users?status__not_in=archived,deleted
```

Every other key takes one value. A comma is part of that value, so `?name=Doe,John`
looks for the name `Doe,John`. A key that takes one value and comes twice gets
`422`, so a client cannot believe that the second value counts. This also holds
for `sort`, the page parameters and keys of your own.

:::{admonition} Details
:class: details

With one key, each comma separates two values, and the spaces around a value go.
With a repeated key, each value stays as it is, so it can hold a comma. A client
that repeats the key sometimes and sends one value other times gets that one
value split. To look for one value that holds a comma, use the plain key.

The `loc` of an error on `__in` also has the position of the value, such as
`["query", "id__in", 1]`.
:::

(search-text)=
### Search for text

`__icontains` finds one piece of text, in any case:

```text
GET /users?name__icontains=john%20doe
```

The spaces around the text go, and an empty value is no filter. A space inside
is part of the text, so this finds `John Doe`, and not `Doe, John`. To require
every word, write a filter of your own:

```python
import sqlalchemy
from sqlalchemy import ColumnElement


@fr.list_filter("q")
def filter_q(self, value: str) -> ColumnElement[bool] | None:
    """Items whose name holds every word."""
    words = value.split()
    if not words:
        return None
    return sqlalchemy.and_(
        *(Item.name.icontains(word, autoescape=True) for word in words)
    )
```

:::{admonition} Details
:class: details

Restly escapes `%`, `_` and `\` before it builds the `LIKE` condition. A
search finds the text as written; these characters are not wildcards. In a
filter of your own, pass SQLAlchemy's `autoescape=True` for the same result,
such as `Item.name.icontains(q, autoescape=True)`. Without it, `?q=%` matches
every row.

For a search where the case counts, write a filter of your own with
`Item.name.contains(value, autoescape=True)`. On SQLite, `LIKE` ignores the case
of ASCII letters, so such a search works the same only on a database such as
PostgreSQL.
:::

### Combine filters on one field

Send several keys of one field to add conditions with AND:

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
gets `422`, and so does a key that appears twice, such as `?sort=name,-name`.
The description of `sort` in OpenAPI lists the sort keys. To choose them, see
[Choose the keys of a list](#choose-list-keys).

A dotted key sorts by a related field, when `include_sort_keys` has it:
`?sort=-city.country.code` orders by that column, and joins each step of the
path. A row without the related row stays in the list. Dotted keys follow
the rules of [Filter on a related row](#relation-filtering).

Without `sort`, the list is in primary key order. For another order when the
client sends no `sort`, set
{attr}`default_sort <fastapi_restly.views.BaseRestView.default_sort>`. It is
written as `sort`, with the Python names of the fields:

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

A key of `default_sort` must be a field of the response schema that can be a
sort key. `include_sort_keys` does not have to keep it, so a view can sort by
a field that the client cannot sort by. A key that the response schema does
not have fails when the view registers, also when the view gets
`default_sort` from a base view. A key that appears twice, such as in
`"name,-name"`, fails when the class is defined, or when the view registers
for a mixin. A view without those fields sets
`default_sort = None` for primary key order. An empty `?sort=` gets the
default too. An override of
{meth}`apply_list_params <fastapi_restly.views.RestView.apply_list_params>`
finds the default in `list_params.sort`. With `include_sort_keys = ()`, the
list params have no sort parameter, but the default still applies.

For a default order on a column that the response schema does not have,
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

A column of SQLAlchemy's `Enum` type sorts by its labels, in the order of
the database. On SQLite and on a column that is not a native enum, such as
the one that `fr.ModelDefaults` gives `Mapped[Status]`, that is the order
of the text. A native ENUM on PostgreSQL sorts its labels as they were
declared. An int enum sorts by its numbers only on a column that stores
them, such as `Integer`.
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
give the field an alias, or leave the field out with
`exclude_filters = ("size",)`; see
[Choose the keys of a list](#choose-list-keys). A renamed parameter frees its old name, so a
field called `page` gets its filter back.

(limit-offset-pagination)=
### Page with limit and offset

For a list that pages with `limit` and `offset`, set
{class}`fr.OffsetPagination <fastapi_restly.views.OffsetPagination>`:

```python
class ArticleView(fr.AsyncRestView):
    pagination = fr.OffsetPagination(default_limit=20, max_limit=100)
```

The client asks for the rows after a position:

```text
GET /articles?limit=20&offset=40
```

`offset` starts at 0. `limit` must be at least 1 and at most `max_limit`, 1000
by default. Without `limit`, the endpoint uses `default_limit`, 50 by default.
A value out of range returns `422`. An offset after the last row returns an
empty `data` with the correct `total_count`.

The response holds the rows in `data`, with `total_count`, `limit` and
`offset`. To change this envelope, see
[Change the list envelope](#change-list-envelope). The filters, the sort,
[filters of your own](#own-list-filter) and the
[settings that choose the keys](#choose-list-keys) work as they do with page
numbers.

Many clients send `skip` instead of `offset`. Rename the parameter with
`offset_query_param`:

```python
class ArticleView(fr.AsyncRestView):
    pagination = fr.OffsetPagination(offset_query_param="skip")
```

:::{admonition} Details
:class: details

`limit_query_param` renames the limit. The rules for a renamed parameter are the
same as for [the page parameters](#rename-the-page-parameters).

A name in the URL does not rename the field in the JSON. With `skip` in the URL,
the response still has `offset`. To send `skip` there too, write an envelope
with `offset: int = pydantic.Field(alias="skip")`.

The database still reads the rows that an offset skips, so a large offset is
slow. On a public endpoint, set `max_offset`. A larger offset then returns `422`.
Without it, Restly rejects only an offset above `2**63 - 1`.

The defaults are the same as those of
{class}`NumberedPagination <fastapi_restly.views.NumberedPagination>`, so a list
that changes its style keeps its number of rows. A
[react-admin view](react_admin.md) pages with `range`, and keeps a
{class}`NumberedPagination <fastapi_restly.views.NumberedPagination>`.
:::

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

:::{admonition} Details
:class: details

Without an annotation, a type checker takes the type of `pagination` from its
value. A view below it that sets another style, or `None`, then fails the check.
Annotate the base view with
{class}`fr.Pagination <fastapi_restly.views.Pagination>`, the base class of the
styles:

```python
from typing import ClassVar


class AppView(fr.AsyncRestView):
    pagination: ClassVar[fr.Pagination | None] = APP_PAGINATION
```
:::

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

Its `LimitOffsetPage` takes `?limit=` and `?offset=`, and returns `items`,
`total`, `limit` and `offset`. The same approach works with
{class}`fr.OffsetPagination <fastapi_restly.views.OffsetPagination>`:

```python
class LimitOffsetPage(pydantic.BaseModel, Generic[T]):
    data: list[T] = pydantic.Field(alias="items")
    total_count: int = pydantic.Field(alias="total")
    limit: int
    offset: int


class AppView(fr.AsyncRestView):
    pagination = fr.OffsetPagination(max_limit=100, envelope=LimitOffsetPage)
```

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

An entry is a key with the Python name of its field, such as
`name__icontains`, or a pattern. For a field with an alias, see
[Use aliases in the URL](#public-names). In a pattern, `*` matches any characters except `.`. So
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
    exclude_filters = ("internal_note", "*__icontains")
```

In `exclude_filters`, a name without a suffix leaves out the whole field: the
plain key and each key with a suffix. So `"internal_note"` also removes
`internal_note__in`, and you cannot miss a filter key of the field. The sort
key is separate: `exclude_sort_keys` removes it. A dotted key, such as
`owner.internal_note`, is separate too. A key with a suffix, such as
`"name__in"`, leaves out that key only.

:::{admonition} Details
:class: details

A `*` never matches a `.`, so each `*` covers one level only. For an item
with a `vendor`, and a `country` on the vendor, `"vendor.*"` has the keys on
the fields of `vendor`, but not those on `vendor.country`. For both levels, write
`("vendor.*", "vendor.*.*")`. A pattern has no other wildcard: `**`, `?` and
`[...]` are errors.

The pattern `"name*"` also matches a field such as `names`. Write
`("name", "name__*")` for the keys of `name` only.

In `include_filters`, a name without a suffix is that one key. To remove only
the plain key of a field, so without `exclude_filters`, list the keys that stay.
`"*__*"` is every key with a suffix, so
`include_filters = ("*__*", "id", "email")` has no `?name=`, but it has
`?name__in=`.

`include_filters = ()` leaves no generated filters, and
`include_sort_keys = ()` removes the `sort` parameter. The parameters of the
pagination, such as `page` and `page_size`, are not filters, and they always
stay.

Dotted keys are off by default. Each one is a join that a client can add, and
a new field on a nested schema would add keys to every list that nests it.

An entry that a view sets and that matches no key fails when the view
registers. The message lists the keys of the field:

```text
ItemView.include_filters: 'price__icontains' matches no filter key of
ItemSchema. The filter keys of 'price' are: price, price__in, price__ne,
price__not_in, price__gte, price__lte, price__gt, price__lt. Only a field on a
text column has __icontains, and not an enum, a Literal or a bool. On a
TypeDecorator column, the field must be a str.
```

A view inherits each setting on its own, as it inherits `pagination`. So a
project base view can set `include_filters = ()`, and a view turns its
filters on again with `("*",)`. An entry that a view inherits may match no
key, because a base view serves many schemas. For mypy, give the setting a
type on the base view: `include_filters: ClassVar[tuple[str, ...]] = ()`.
Without it, mypy takes the type of `()` and rejects `("*",)` in the view.
The same holds for every setting; see
[What a type checker cannot see](#typing-limits).

The sort keys are separate from the filter keys, so a view can sort on
`created_at` without a `?created_at=` filter. A sort key has no suffix. It
never goes through a list of related rows: an item with several tags has no
single `tags.name` to sort on.

To see the keys that a view could have, set
`include_filters = ("*", "*.*")` and `include_sort_keys = ("*", "*.*")` for a
moment, and look at `/docs`. Every entry must match a key, so leave out
`"*.*"` when the schema has no nested object. Add `"*.*.*"` for keys two
levels down.

{func}`derive_list_params_schema() <fastapi_restly.query.derive_list_params_schema>`
takes the four settings and `sort_query_param` as keywords, so a route
outside a view can build the same list params. Declare them with `Query()`:

```python
from typing import Annotated

from fastapi import Query

ItemListParams = fr.query.derive_list_params_schema(ItemSchema, Item)


@app.get("/items")
async def list_items(list_params: Annotated[ItemListParams, Query()]): ...
```

Do not use `Depends()` for them. FastAPI then gives the `__in` keys no value and
lets an unknown key through, without an error. With `Query()`, an unknown key
gets `422`. That is also a key that a dependency of the route reads, such as the
key of an `APIKeyQuery`, as with any FastAPI model with `extra="forbid"`. Send
such a value in a header, or use a view: a view accepts these keys. A key that
takes one value and comes twice takes the last value on
such a route: only a view refuses it.

A key that clients use is part of your API. Removing it later breaks those
clients. A key also goes away when you remove its field from the response
schema.
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
GET /orders?user.name__icontains=ali
```

The relation must be a `relationship` on the SQLAlchemy model and a nested
schema on the response schema, as in
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
and a sort on the same relationship share one join.

Each join is a `LEFT OUTER JOIN`, so a row without the related row counts as
NULL, as in any other filter. `?vendor.id__isnull=true` finds the rows without a
vendor, and `?vendor.name__ne=acme` keeps them. A key that looks for a value,
such as `?vendor.name=acme`, leaves them out.

Aliases apply to every part of the path, the relationship and the field,
because the list params always use the public names of the response
schema:

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

The key settings of a view use the Python names in each part of the path. So
`include_filters = ("author.name",)` turns on `?writer.authorName=`.
:::

(foreign-key-filtering)=
## Filter by a foreign key

A foreign key declared with
{class}`fr.MustExist[int, Post] <fastapi_restly.schemas.MustExist>` filters by
its own public name, the same name it has in JSON:

```text
GET /comments?post_id=1
GET /comments?post_id__in=1,2
GET /comments?post_id__ne=1
GET /comments?post_id__gte=10      # range suffixes work on int ids
GET /comments?post_id__isnull=true
```

:::{admonition} Details
:class: details

A `MustExist[pk, ...]` field gets the filters of its `pk` type. An `int` id
gets equality, `__in`, `__ne`, `__not_in` and the range suffixes. A `UUID` id
gets no range suffixes, as the [list of suffixes](#filter-a-list) says. Only an
id on a text column gets `__icontains`. A foreign key that can be NULL also gets
`__isnull`.

A relationship reference filters differently (see
[Choose a reference style](#reference-styles)):

- An {class}`IDSchema[Model] <fastapi_restly.schemas.IDSchema>` field filters
  by its id, as in `?author.id=1`, with the filters of the id's type. That key
  has a dot, so the view turns it on first, for example with
  `include_filters = ("*", "author.*")`.
- A flat {class}`IDRef[Model] <fastapi_restly.schemas.IDRef>` field on a
  relationship gets no filter. To filter by it, also put the foreign key
  column in the response schema, for example as `author_id: fr.ReadOnly[int]`.
:::

(public-names)=
## Use aliases in the URL

The keys of the list params use the **public name** of each field: the name
that a response shows. That is its `serialization_alias` or its alias when it
has one, and its Python name otherwise. The URL accepts only the public name.
`populate_by_name` changes how Pydantic reads a request body. It does not add
the Python names to the URL.

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

The key settings of a view, such as `include_filters` and `default_sort`, use
the Python names. Only the client uses the alias:

```python
class UserView(fr.AsyncRestView):
    ...
    include_filters = ("user_name__icontains",)
    default_sort = "user_name"
```

The client then sends `?userName__icontains=al`, and OpenAPI shows `userName`
as the default of `sort`. An entry with the public name, such as `"userName"`,
fails when the view registers, also when the view inherits it. The key of
[a filter of your own](#own-list-filter) is the key in the URL, because that
filter adds the key.

:::{admonition} Details
:class: details

A public name with `.` or `__` gets no filter and no sort key, because `__` splits
a field from an operator, and `.` walks to a related field. A public name that
starts with `_`, such as `_id`, gets none either, because Pydantic takes such a
name as a private attribute. The field still goes out in a response, and the view
starts. A setting that names such a key fails,
because it matches no key.
:::

(own-list-filter)=
## Add a filter of your own

For a filter that the generated keys do not give, decorate a view method
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

A key can also be a field and an operator, as the generated keys are. The
operator can be a new one:

```python
from sqlalchemy import func


@fr.list_filter("name__length")
def filter_name_length(self, value: int) -> ColumnElement[bool]:
    """Items whose name has this many characters."""
    return func.length(Item.name) == value
```

The client sends `?name__length=5`. Before the last `__` comes a field with
filters, such as `name` or `vendor.name`, so `__` always splits a field from
an operator. A key without `__` and `.`, such as `popular`, needs no field.

A filter on a column lets a client find out the values of that column, one
request at a time. Write a filter only on data that the client may see.

:::{admonition} Details
:class: details

`value: list[str]` reads a repeated key, as in `?tag=a&tag=b`. A plain type
takes one value, and a repeated key gets `422`.

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
fails when the view registers. To replace a key with a suffix, such as
`name__icontains`, leave it out with `exclude_filters`, and decorate your own.
To replace the plain key `name`, `exclude_filters = ("name",)` removes the whole
field. To keep its other keys, [list them](#choose-list-keys) in
`include_filters` instead.

A key with an operator looks like a generated key, but it works as your
method does. It has the type of the method's value, so a key of your own that
ends in `__in` does not split its value at commas.

A subclass inherits the key. It drops the key by setting the name of the
method to `None`, as in `filter_popular = None`. A type checker rejects
`None` in place of a method, so give it a type: `filter_popular: Any = None`.
An override without the decorator fails when the class is defined.

Restly runs the filter methods only for the view's own list params: those of
a list route, or a dict or a `QueryParams` that a method gets. A Pydantic
model of another class, such as one of your own, does not run them, and
Restly warns with
{class}`RestlySkippedFilterMethodsWarning <fastapi_restly.exc.RestlySkippedFilterMethodsWarning>`.

A key does not start with `_`. It has `__` at most once, with a field before
it and an operator after it, and a `.` only in the field. It is not the name
of the view's page or sort parameters, such as `page`, `page_size` or `sort`,
and not the name of a Pydantic model attribute, such as `copy`.

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

A route cannot read a key that its list params also read, such as a parameter
`limit` next to `list_params` on a view with `fr.OffsetPagination`. One value
would fill both, and OpenAPI would show the key twice, so the view fails when it
registers. Read the value from `list_params`, or give the parameter of the route
another name. This check does not see a dependency on the app or on a router.
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
{attr}`list_params_schema <fastapi_restly.views.BaseRestView.list_params_schema>`,
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

The values of a dict stay values, so `{"id": 5}` needs no text. A list, a tuple
or a set holds several values. An empty one matches no rows on `__in`, as
SQLAlchemy's `in_([])` does, and keeps every row on `__not_in`. So
`{"id__in": allowed_ids}` reads no rows when `allowed_ids` is empty. `None`
leaves the key out.

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
{attr}`list_params_schema <fastapi_restly.views.BaseRestView.list_params_schema>`,
and apply it to the statement in
{meth}`apply_filters <fastapi_restly.views.RestView.apply_filters>` and
{meth}`apply_list_params <fastapi_restly.views.RestView.apply_list_params>`:

```python
from typing import Literal


class ItemSearch(fr.query.PagedListParams):
    q: str | None = None
    order: Literal["name", "-name"] = "name"


class ItemView(fr.AsyncRestView):
    prefix = "/items"
    model = Item
    schema = ItemSchema
    list_params_schema = ItemSearch

    async def apply_filters(self, stmt, list_params: ItemSearch):
        if list_params.q:
            stmt = stmt.where(Item.name.icontains(list_params.q, autoescape=True))
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
applies, and `total_count` counts the rows that `apply_filters` keeps.

{class}`fr.query.PagedListParams <fastapi_restly.query.PagedListParams>`
gives the list params the page fields, `page` and `page_size`. Restly fills
them from the view's
{attr}`pagination <fastapi_restly.views.BaseRestView.pagination>`: their
names in the URL, the default page size and the bounds. Your code reads
`list_params.page` and `list_params.page_size`, also when the pagination
renames them, such as `page_size_query_param="size"`. The response reports
the page that the client gets.

For a list with `limit` and `offset`, use
{class}`fr.query.OffsetListParams <fastapi_restly.query.OffsetListParams>` and
{class}`fr.OffsetPagination <fastapi_restly.views.OffsetPagination>`. They work
in the same way. Your code reads `list_params.limit` and `list_params.offset`.

List params of your own on a view with pages need these fields, or the view
fails when it registers: its list would not page, while its response says that
it has pages. For a list that returns every row, subclass `pydantic.BaseModel`
and set `pagination = None`.

Do not use `pagination = None` for a list that limits its own rows. The default
envelope then has no `total_count`. In an envelope of your own with that field,
it is the number of rows in the response, not the number of matching rows, and
nothing warns you. Use a pagination, as above.

:::{admonition} Details
:class: details

On a sync view, the two methods are `def`. `apply_list_params` adds the
order and the page. Order by a unique column last, such as `Item.id`, so
that a row never moves to another page. In a base view for several models,
{func}`fr.query.order_by_primary_key <fastapi_restly.query.order_by_primary_key>`
adds the primary key of the view's model.

One class of list params serves views with other pagination: each view gets
its own page fields. A subclass cannot declare these fields again: the view
fails when it registers. Set the default and the bounds on the pagination.

Page fields that you write in a `pydantic.BaseModel` also work, when they have
the names of the view's page parameters. The settings of the pagination do not
apply to them, such as `max_limit` and `max_page_size`. So give them their own
bounds, with `ge` and `le`. The default of `limit` or `page_size` must be a
number: with `None`, the view fails when it registers, because a request without
the field would return every row.

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

(own-list-params-keys)=
### Filter and sort on the fields of the response

The list params of a client library often name the fields of the resource,
as in `filter={"category":"tools"}`. For each such key,
{class}`fr.query.FilterKey <fastapi_restly.query.FilterKey>` finds the field
and its column. As in the generated filters, a field that the response does
not show is an unknown key. Here it gets a `400`, not a `422`, because the
key is inside the value of `filter`; see
[Why a bad list request returns 422 or 400](#list-request-errors).
{meth}`parse() <fastapi_restly.query.FilterKey.parse>` gives a value the
validation of the field and the form of the column, as the generated filters
do. {class}`fr.query.SortKey <fastapi_restly.query.SortKey>` does the same
for a sort key:

```python
from typing import Any

import pydantic


class ItemListParams(fr.query.PagedListParams):
    filter: pydantic.Json[dict[str, Any]] | None = None
    sort: str | None = None


class ItemView(fr.AsyncRestView):
    prefix = "/items"
    model = Item
    schema = ItemSchema
    list_params_schema = ItemListParams

    async def apply_filters(self, stmt, list_params: ItemListParams):
        for name, value in (list_params.filter or {}).items():
            key = fr.query.FilterKey(self.model, self.response_schema, name)
            stmt = stmt.where(key.column == key.parse(value))
        return stmt

    async def apply_list_params(self, stmt, list_params: ItemListParams):
        stmt = await self.apply_filters(stmt, list_params)
        sorted_on = []
        if list_params.sort:
            name = list_params.sort.removeprefix("-")
            key = fr.query.SortKey(self.model, self.response_schema, name)
            descending = list_params.sort.startswith("-")
            stmt = stmt.order_by(
                key.term(descending=descending, sort_nulls=self.sort_nulls)
            )
            sorted_on.append(key.column)
        stmt = fr.query.order_by_primary_key(stmt, self.model, sorted_on=sorted_on)
        offset = (list_params.page - 1) * list_params.page_size
        return stmt.limit(list_params.page_size).offset(offset)
```

The client sends `GET /items/?filter={"category":"tools"}&sort=-name&page=2`.
The condition, such as `==`, stays yours. As in the example above, the list
params get the page fields from
{class}`PagedListParams <fastapi_restly.query.PagedListParams>`, and
`apply_list_params` applies the page.
{func}`order_by_primary_key() <fastapi_restly.query.order_by_primary_key>`
orders by the primary key last, so that a row never moves to another page;
`sorted_on` names the column that the list is sorted on already, which does
not come again. The [React Admin](react_admin.md) views use these classes
for their `filter` and `sort`.

:::{admonition} Details
:class: details

A key is the name of a field, or its alias when the field has one. The model
has an attribute with the name of the field that is one value in SQL: a
column, a `column_property`, or a hybrid property with an SQL expression. A
hybrid property that is plain Python, such as `self.deleted_at is not None`,
is an unknown key. A key with a dot, such as `vendor.name`, needs a join, so
these classes do not know it.

`parse()` takes text from a URL or a value from JSON, and a number becomes
text for a text field. The validators of the field get only this field, so
`info.data` is `{}`; see
[Filter values go through the field's validation](#filter-value-validation).
`None` stays `None` when the field allows it, so `key.column == None` gives
`IS NULL`. A value that fails gets a `400` with the reason. `term()` puts
NULLs where `sort_nulls` says.

Restly cannot see a value that a serializer hides, such as an email shown as
`a***@example.com`. A filter or a sort on such a field would let a client
find out the value. Type such a field as a subclass of
{class}`pydantic.Secret <pydantic.types.Secret>` with its own `_display()`,
so it is no key, as a `SecretStr` is not.
:::

## Internals

(list-parameters-lifecycle)=
### How Restly builds the list params

In {meth}`before_include_view() <fastapi_restly.views.BaseRestView.before_include_view>`,
Restly sets one class attribute,
{attr}`list_params_schema <fastapi_restly.views.BaseRestView.list_params_schema>`.
It is a Pydantic model that
{func}`derive_list_params_schema(cls.response_schema, cls.model, pagination=cls.pagination) <fastapi_restly.query.derive_list_params_schema>`
generates, with the view's list settings. It holds the pagination
parameters, the sort parameter, the filter keys that the settings keep, and
the view's own keys. Restly builds it once for each view.

A route reads the list params through a dependency, not as a FastAPI
query model. FastAPI splits a query model into separate parameters only when
it is the only query parameter of the route. Next to a dependency that reads
`?api_key=`, the filters would become one required object. So Restly
validates the model from the query string itself, and shows each key as its
own parameter in OpenAPI, next to the other parameters of the route.

Each generated filter key validates its value when Restly reads the query
string; see
[Filter values go through the field's validation](#filter-value-validation). So
{func}`apply_list_params <fastapi_restly.query.apply_list_params>` gets values
of the type of the field, and does not run the validators again. A key of your
own has the type of its method's value.

## See also

- [Responses](responses.md): the list envelope and its fields.
- [Errors](errors.md): the `422` and `400` answers to a bad list
  request.
- [Nest a resource under its parent](#nested-resources): a child resource under its parent,
  such as `/projects/{project_id}/tasks`.
