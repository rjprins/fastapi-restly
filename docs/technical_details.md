# Technical Details

## Schema Generation Under the Hood

FastAPI-Restly builds request and response schemas from your declared schema class,
or auto-generates one from the SQLAlchemy model when `schema` is omitted on a
view.

### ReadOnly and WriteOnly

Field-level markers are implemented with `typing.Annotated` metadata:

```python
class UserSchema(IDSchema):
    id: ReadOnly[int]
    email: str
    password: WriteOnly[str]
```

- `ReadOnly[...]` fields are removed from generated create/update input schemas.
- `WriteOnly[...]` fields are accepted on input and omitted from serialized responses.

### Generated Input Schemas

For a view schema `MySchema`, Restly derives:

- `creation_schema`: removes `ReadOnly[...]` fields
- `update_schema`: removes `ReadOnly[...]` fields and makes the remaining fields optional

Those derived schemas inherit from the original schema so validators and model
config continue to apply to writable fields.

### Auto-Generated Schemas

`create_schema_from_model(...)` inspects SQLAlchemy model annotations and can
generate nested relationship fields when `include_relationships=True`.

`auto_generate_schema_for_view(...)` is more conservative: it excludes
relationship attributes by default and focuses on scalar fields and foreign-key
columns. This keeps generated CRUD views usable without requiring nested ORM
loading or nested write payloads.

## Query Modifier Lifecycle

Query modifiers have two versions:

- `QueryModifierVersion.V1`: `filter[...]`, `sort`, `limit`, `offset`
- `QueryModifierVersion.V2`: direct fields, `order_by`, `page`, `page_size`

Views capture the active query modifier version when `@include_view(...)`
registers them. That captured version is then used for both:

- generating the query parameter schema
- applying filtering, sorting, and pagination at runtime

This means:

- call `set_query_modifier_version(...)` before registering the view, or
- set `query_modifier_version = QueryModifierVersion.V1|V2` on the view class

## More Topics

```{toctree}
:maxdepth: 1

auto_schema
custom_endpoints
existing
query_modifiers
```
