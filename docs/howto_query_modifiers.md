# How-To: Filter, Sort, and Paginate Lists

List endpoints (`GET /{prefix}/`) support query modifiers out of the box. Two query
modifier styles are available: V1 (JSONAPI-inspired, the default) and V2 (standard
HTTP-style). Choose one style per project and configure it globally or per view.

---

## Choosing a Version

### Global configuration

```python
from fastapi_restly import QueryModifierVersion, set_query_modifier_version

# Call before @fr.include_view(...) so the generated query schema stays aligned.
set_query_modifier_version(QueryModifierVersion.V2)
```

### Temporary override (context manager)

`use_query_modifier_version` is a context manager that temporarily switches the active
version and resets it on exit. It is mainly useful in tests and when calling low-level
helpers like `fastapi_restly.query.create_query_param_schema(...)` or
`fr.apply_query_modifiers(...)` directly.
Already-registered views keep the version they captured during `@fr.include_view(...)`.

```python
from fastapi_restly import QueryModifierVersion, use_query_modifier_version

with use_query_modifier_version(QueryModifierVersion.V2):
    ...  # V2 is active only inside this block
```

### Per-view fixed version

```python
import fastapi_restly as fr

class UserView(fr.AsyncRestView):
    query_modifier_version = fr.QueryModifierVersion.V2
    ...
```

---

## V1 Style (default)

V1 follows a JSONAPI-inspired convention: filter fields are bracketed and operators
are embedded in the value.

### Filtering

The `filter[field]` parameter accepts a value with an optional operator prefix. The
operator is the leading character(s) of the value — not part of the URL syntax:

| Operator | Value prefix | SQL equivalent | Full example |
|---|---|---|---|
| Equals | *(none)* | `field = 'value'` | `?filter[status]=active` |
| Not equals | `!` | `field != 'value'` | `?filter[status]=!inactive` |
| Greater than | `>` | `field > value` | `?filter[age]=>18` |
| Less than | `<` | `field < value` | `?filter[age]=<65` |
| Greater or equal | `>=` | `field >= value` | `?filter[age]=>=18` |
| Less or equal | `<=` | `field <= value` | `?filter[age]=<=64` |
| Is null | `null` | `field IS NULL` | `?filter[deleted_at]=null` |
| Is not null | `!null` | `field IS NOT NULL` | `?filter[deleted_at]=!null` |

Values are validated against the Pydantic schema field type. An invalid value returns
HTTP 400.

#### OR logic with comma-separated values

Multiple comma-separated values in a single `filter[field]` parameter are combined
with OR:

```text
GET /users/?filter[id]=1,2,3
```

Produces `WHERE id = 1 OR id = 2 OR id = 3`. This is equivalent to an `IN` filter.

#### Multiple filters on the same field

Repeat the parameter to add AND conditions:

```text
GET /users/?filter[created_at]=>=2024-01-01&filter[created_at]=<2025-01-01
```

Produces `WHERE created_at >= '2024-01-01' AND created_at < '2025-01-01'`.

### Contains (case-insensitive substring search)

The `contains[field]` parameter performs a case-insensitive `ILIKE '%value%'` search.
It is only available for string fields.

```text
GET /users/?contains[email]=example
```

#### AND logic with whitespace-separated terms

Multiple words in a single `contains[field]` value are split on whitespace and
combined with AND — each word must appear somewhere in the field:

```text
GET /users/?contains[name]=john doe
```

Produces `WHERE name ILIKE '%john%' AND name ILIKE '%doe%'`. This is intentionally
different from the comma OR logic in `filter[...]`.

Literal `%`, `_`, and `\` characters are escaped before building the SQL `ILIKE`, so
contains searches behave like literal substring matching rather than wildcard matching.

### Sorting

Use the `sort` parameter with comma-separated field names. Prefix a field name with
`-` for descending order:

```text
GET /users/?sort=-created_at,name
```

Produces `ORDER BY created_at DESC, name ASC`.

> **Default ordering:** When no `sort` parameter is given and the model has an `id`
> column, V1 automatically applies `ORDER BY id ASC`. Models without an `id` column
> return results in an unspecified order.

### Pagination

Use `limit` and `offset` to page through results. Both are optional — omitting them
returns all matching rows (no automatic pagination in V1).

```text
GET /users/?limit=20&offset=40
```

Negative values return HTTP 400.

### Relation filtering

Filtering on a related model's field uses dot notation:

```text
GET /orders/?filter[user.name]=Alice
```

This automatically joins the `user` table and filters on `user.name`. The relation
must be defined on both the SQLAlchemy model (as a `relationship`) and the Pydantic
schema (as a nested schema field). Optional nested schemas (`UserSchema | None`) and
deep nesting (`filter[blog.author.name]=Alice`) are supported. Lists of nested
schemas (`list[UserSchema]`) are **not** supported.

### Quick reference

```text
GET /users/?filter[name]=John
GET /users/?filter[id]=1,2,3
GET /users/?filter[age]=>=18&filter[age]=<65
GET /users/?filter[deleted_at]=null
GET /users/?filter[status]=!inactive
GET /users/?contains[email]=example
GET /users/?contains[name]=john doe
GET /users/?sort=-id,name
GET /users/?limit=20&offset=0
```

---

## V2 Style

V2 uses direct field names and double-underscore suffixes for operators, similar to
Django or other mainstream frameworks.

> **Always-on pagination:** V2 always paginates. If you supply no `page` or
> `page_size` parameters, V2 defaults to `page=1, page_size=100`, adding
> `LIMIT 100 OFFSET 0` to every query. V1 does not paginate unless you explicitly
> pass `limit`/`offset`.

### Filtering

Plain equality uses the bare field name:

```text
GET /users/?name=John
```

Suffixes add other operators:

| Suffix | SQL equivalent | Example |
|---|---|---|
| *(none)* | `field = value` | `?name=John` |
| `__gte` | `field >= value` | `?age__gte=18` |
| `__lte` | `field <= value` | `?age__lte=64` |
| `__gt` | `field > value` | `?age__gt=17` |
| `__lt` | `field < value` | `?age__lt=65` |
| `__ne` | `field != value` | `?status__ne=archived` |
| `__contains` | `field ILIKE '%value%'` | `?email__contains=example` |
| `__isnull` | `field IS NULL` / `IS NOT NULL` | `?deleted_at__isnull=true` |

`__isnull` accepts a boolean value (`true` or `false`), **not** the string `"null"`.

#### OR logic with comma-separated values

Comma-separated values in a plain equality filter are combined with OR:

```text
GET /users/?status=active,pending
```

Produces `WHERE status = 'active' OR status = 'pending'`.

#### AND logic with whitespace-separated terms (contains)

Like V1, `__contains` splits on whitespace and ANDs the terms:

```text
GET /users/?name__contains=john doe
```

Produces `WHERE name ILIKE '%john%' AND name ILIKE '%doe%'`.

> **Comma vs. space:** For both V1 and V2, comma means OR on equality filters
> (`?id=1,2,3`), and space means AND on contains (`?name__contains=john doe`).
> These are intentionally opposite and apply consistently across both versions.

### Sorting

Use the `order_by` parameter with comma-separated field names. Prefix with `-` for
descending:

```text
GET /users/?order_by=-created_at,name
```

> **Default ordering:** When no `order_by` parameter is given and the model has an
> `id` column, V2 automatically applies `ORDER BY id ASC`, same as V1.

### Pagination

```text
GET /users/?page=2&page_size=50
```

`page` is 1-based. `page_size` must be > 0. When omitted, defaults are `page=1` and
`page_size=100`.

### Alias support

For flat fields, V2 query parameter names follow the Pydantic schema field **aliases**.
If a schema field defines `alias="userName"`, the query parameter key is `userName`,
not `user_name`:

```python
class UserSchema(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_name: Annotated[str, Field(alias="userName")]
```

```text
GET /users/?userName=Alice        # uses the alias — always works
GET /users/?user_name=Alice       # works only if populate_by_name=True
```

When `populate_by_name=True` is set on the schema config, both the alias and the
field name are accepted as query parameters for flat fields.

### Relation filtering

V2 uses the same dot notation as V1:

```text
GET /orders/?user.name=Alice
GET /orders/?user.name__contains=ali
```

The same requirements apply: the relation must be defined in both the SQLAlchemy
model and the Pydantic schema. For aliased nested fields, only the **nested field**
segment uses the alias. The relation segment itself must still use the schema/model
field name.

Example:

```python
class AuthorSchema(BaseModel):
    name: str = Field(alias="authorName")

class ArticleSchema(BaseModel):
    author: AuthorSchema
```

```text
GET /articles/?author.authorName=Alice   # supported
GET /articles/?writer.authorName=Alice   # not supported
```

### Quick reference

```text
GET /users/?name=John
GET /users/?status=active,pending
GET /users/?age__gte=18&age__lt=65
GET /users/?deleted_at__isnull=true
GET /users/?email__contains=example
GET /users/?name__contains=john doe
GET /users/?order_by=-id
GET /users/?page=2&page_size=50
```

---

## V1 vs V2 at a glance

| Feature | V1 | V2 |
|---|---|---|
| Equality | `?filter[name]=John` | `?name=John` |
| Range (≥) | `?filter[age]=>=18` | `?age__gte=18` |
| Not equals | `?filter[status]=!inactive` | `?field__ne=value` *(see note)* |
| Null check | `?filter[x]=null` | `?x__isnull=true` |
| Not null | `?filter[x]=!null` | `?x__isnull=false` |
| Contains | `?contains[email]=ex` | `?email__contains=ex` |
| Sort | `?sort=-id` | `?order_by=-id` |
| Pagination | `?limit=20&offset=0` *(optional)* | `?page=1&page_size=100` *(always applied)* |
| OR values | `?filter[id]=1,2,3` | `?id=1,2,3` |
| AND contains | `?contains[n]=a b` | `?n__contains=a b` |
| Relation filter | `?filter[user.name]=Alice` | `?user.name=Alice` |
| Default order | `ORDER BY id` (if id exists) | `ORDER BY id` (if id exists) |

---

## Overriding query logic per view

Override `on_list` to inject a base query before the framework applies query
modifiers:

```python
import sqlalchemy
import fastapi_restly as fr

class UserView(fr.AsyncRestView):
    ...

    async def on_list(self, query_params, query=None):
        query = sqlalchemy.select(self.model).where(self.model.active.is_(True))
        return await super().on_list(query_params, query=query)
```

`super().on_list(query_params, query=query)` passes your base query into the
normal modifier pipeline, so all the filter/sort/paginate parameters still work on
top of your pre-filtered result set.
