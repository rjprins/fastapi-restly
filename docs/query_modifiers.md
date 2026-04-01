# Query Modifiers

FastAPI-Restly ships two query parameter interfaces for list endpoints:

- **V1**: JSONAPI-style (`filter[name]=John`, `sort=-id`, `limit=20`)
- **V2**: standard HTTP-style (`name=John`, `order_by=-id`, `page=2&page_size=20`)

For the full operator reference, pagination rules, alias behavior, and examples, see
[How-To: Filter, Sort, and Paginate Lists](howto_query_modifiers.md).

## Relation Filtering

Both V1 and V2 support filtering on fields of a related model using dot notation.
The relation must be defined in both the SQLAlchemy model (as a `relationship`) and
the Pydantic schema (as a nested schema field).

Examples:

```text
GET /orders/?filter[user.name]=Alice    # V1
GET /orders/?user.name=Alice            # V2
GET /orders/?user.name__contains=ali    # V2 contains
```

Supported constraints:

- Nested schemas can be optional: `user: UserSchema | None`
- Deep nesting is supported
- Lists of nested schemas (`list[UserSchema]`) are not supported for relation filtering

V2 alias caveat:

- Flat aliased fields work as expected
- For relation filters, the relation segment must still use the schema/model field name
- Only the nested field segment may use an alias

Example:

```text
GET /articles/?author.authorName=Alice   # supported
GET /articles/?writer.authorName=Alice   # not supported
```
