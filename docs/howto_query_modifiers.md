# Filter, Sort, and Paginate Lists

List endpoints (`GET /{prefix}/`) support filtering, sorting, and
pagination through URL query parameters out of the box. Filter parameters
are derived from the response schema; sort and pagination use a fixed set
of names.

Pagination is opt-in: lists return every matching row when no `page_size`
is supplied. Pass `page_size` (and optionally `page`) to enable
pagination, or set
{attr}`default_page_size <fastapi_restly.views.BaseRestView.default_page_size>`
on the view class if you want every request to be paginated by default.
For public endpoints, set `default_page_size` and
{attr}`max_page_size <fastapi_restly.views.BaseRestView.max_page_size>`;
without a default cap, a missing `page_size` scans the full table.

Unknown query keys are rejected with 422. Filters are narrowing controls,
so a typo or unsupported operator silently ignored could widen the result
set. Generated list endpoints therefore validate the request against the
schema's declared parameters and reject anything else; for which
malformed requests produce which status, see
[422 vs 400 on list endpoints](howto_error_responses.md#422-vs-400-on-list-endpoints).
To allow extra view-specific keys, see
[Extra query parameters](#extra-query-parameters).

## Filtering

Filter parameters compare a schema field against a value taken from the
URL. Plain equality uses the bare field name:

```text
GET /users/?name=John
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
`__icontains` are only generated for string fields.

### Comma logic on bare equality

Comma-separated values in a plain equality filter are OR-combined:

```text
GET /users/?status=active,pending
```

This produces `WHERE status = 'active' OR status = 'pending'`, which is
equivalent to SQL `IN`. Use `__in` when you want that same SQL `IN`
meaning to be explicit in the URL:

```text
GET /users/?status__in=active,pending
```

### Comma logic on `__ne` (NOT IN)

Comma-separated values in `__ne` are AND-combined, meaning the row must
differ from every listed value:

```text
GET /users/?status__ne=archived,deleted
```

This produces `WHERE status != 'archived' AND status != 'deleted'`, which
is equivalent to SQL `NOT IN`.

### AND-combining multiple contains terms

To require several substrings at once, the precise form is to repeat the
parameter: each predicate becomes its own `LIKE` / `ILIKE` clause and the
clauses are AND-combined:

```text
GET /users/?name__contains=john&name__contains=doe
GET /users/?name__icontains=john&name__icontains=doe
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
GET /users/?created_at__gte=2024-01-01&created_at__lt=2025-01-01
```

This produces
`WHERE created_at >= '2024-01-01' AND created_at < '2025-01-01'`.

## Sorting

Use the `sort` parameter with comma-separated field names, prefixing a
name with `-` for descending order:

```text
GET /users/?sort=-created_at,name
```

When no `sort` parameter is given and the model has an `id` column, the
framework automatically applies `ORDER BY id ASC`. Models without an `id`
column return results in an unspecified order.

## Pagination

Pagination is controlled by the `page` and `page_size` parameters:

```text
GET /users/?page=2&page_size=50
```

`page` is 1-based. `page_size` must be `>= 1` and `<= max_page_size`
(default 1000). When `page_size` is omitted, the endpoint returns every
matching row (no implicit cap). To enforce a default page size, set
{attr}`default_page_size <fastapi_restly.views.BaseRestView.default_page_size>`
on the view class:

```python
class UserView(fr.AsyncRestView):
    default_page_size = 25
    max_page_size = 200
```

Out-of-range pagination values produce a standard `422` response from
FastAPI.

### The pagination envelope

By default a list endpoint returns a plain JSON array. Set
`include_pagination_metadata = True` on the view to wrap the items with a
`total` and page metadata:

```python
class UserView(fr.AsyncRestView):
    include_pagination_metadata = True
```

[Response Envelopes and List Metadata](howto_response_schema.md) is
canonical for the envelope's shape and when each field is populated.

## Extra query parameters

A view that consumes a custom query parameter outside the schema-derived
grammar (read in an override via `self.request.query_params`) must
declare it, or the 422 validation rejects it as an unknown key:

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
GET /users/?userName=Alice        # supported
GET /users/?user_name=Alice       # rejected; the Python name is not exposed
```

If you want a different URL key, change the alias.

## Relation filtering

Filtering on a related model's field uses dot notation:

```text
GET /orders/?user.name=Alice
GET /orders/?user.name__contains=ali
```

The relation must be defined on both the SQLAlchemy model (as a
`relationship`) and the Pydantic schema (as a
[nested schema field](howto_relationship_idschema.md#nested-relationship-objects)).
Optional nested schemas (`UserRead | None`) and deep nesting
(`?blog.author.name=Alice`) are supported. Lists of nested schemas
(`list[UserRead]`) are not.

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
GET /articles/?writer.authorName=Alice    # supported (aliased segments)
GET /articles/?author.name=Alice          # rejected; use public aliases
```

## Foreign-key filtering

A scalar foreign key declared with
{class}`fr.MustExist[int, Post] <fastapi_restly.schemas.MustExist>` is
filterable by its own public name, the same name the wire format uses:

```text
GET /comments/?post_id=1
GET /comments/?post_id=1,2          # OR (SQL IN)
GET /comments/?post_id__in=1,2
GET /comments/?post_id__ne=1
GET /comments/?post_id__gte=10      # range operators apply to int ids
GET /comments/?post_id__isnull=true
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
GET /users/?name=John
GET /users/?status=active,pending
GET /users/?age__gte=18&age__lt=65
GET /users/?deleted_at__isnull=true
GET /users/?email__icontains=example
GET /users/?name__contains=john doe
GET /users/?sort=-id
GET /users/?page=2&page_size=50
```

## Overriding query logic per view

Everything above operates on a query that the view builds. Override
{meth}`build_query <fastapi_restly.views.RestView.build_query>` to inject
a base query before URL parameters are applied.
{meth}`get_many <fastapi_restly.views.RestView.get_many>`,
{meth}`count <fastapi_restly.views.RestView.count>`, and
{meth}`get_one <fastapi_restly.views.RestView.get_one>` all use this
query, so the filter applies to listings, totals, and single-row fetches:

```python
import fastapi_restly as fr

class UserView(fr.AsyncRestView):
    ...

    def build_query(self):
        return super().build_query().where(self.model.active.is_(True))
```

Calling `super().build_query()` and chaining `.where(...)` composes
cleanly with any base-class or mixin filter. See
[Composing views with mixins](howto_compose_views_with_mixins.md) for the
multi-layer pattern.

The `get_many` business method does not accept a separate `query` argument.
Keep SQL-level base query changes in `build_query()` so listing,
pagination totals, and single-row fetches all see the same visibility
rules.

For a different URL **grammar** (other parameter names, another dialect's
filter syntax), the seam is
{meth}`apply_query_params(query, query_params) <fastapi_restly.views.RestView.apply_query_params>`,
which owns translating URL parameters into the query;
[React Admin Integration](howto_react_admin.md) is the shipped worked
example of a view family overriding it. Reserve overriding `get_many()`
itself for a genuinely different *result* shape, where you construct the
query explicitly inside the method.

## See also

- [List-parameters lifecycle](technical_details.md#list-parameters-lifecycle):
  how the filter grammar is generated and frozen at registration time.
- [Patterns: nested resources](patterns.md#nested-resources-projectsidtasks):
  foreign-key filtering as the sub-resource idiom.
