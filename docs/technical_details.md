# Technical Details

## Schema Generation Under the Hood

### Read-Only Field Detection

FastAPI-Alchemy supports two approaches for marking fields as read-only:

1. **Class-level approach**: Using `read_only_fields: ClassVar = ["field1", "field2"]`
2. **Field-level approach**: Using `ReadOnly(type)` annotation

The field-level approach uses Python's `typing.Annotated` to attach metadata to type annotations:

```python
class ReadOnly:
    def __getitem__(self, t: type[T]) -> type[T]:
        return Annotated[t, "readonly"]

# Create a singleton instance
ReadOnly = ReadOnly()
```

### Creation Schema Generation

When you define a schema with read-only fields, the framework automatically generates a creation schema using an **inheritance + override** approach:

1. **Inheritance**: The creation schema inherits from your original schema, preserving all validators, config, methods, and other functionality
2. **Field Override**: Read-only fields are overridden to be optional with `None` as default
3. **Input Filtering**: The `model_validate` method is overridden to filter out read-only fields from input data

#### Side Effects to Be Aware Of

- **Read-only fields are absent**: They cannot be set and do not appear in creation endpoints
- **Validators for writable fields are preserved**: All validation logic for included fields works as expected
- **No validation for read-only fields**: Any validators or logic depending on read-only fields will not run during creation

---

### Update Schema Creation

When you define a schema with `read_only_fields`, the framework automatically generates an update schema using an **inheritance + ignore** approach:

1. **Inheritance**: The update schema inherits from your original schema, preserving all validators, config, methods, and other functionality
2. **Field Override**: Read-only fields are overridden to be optional with `None` as default
3. **Input Filtering**: The `model_validate` method is overridden to filter out read-only fields from input data

### How It Works

1. Your original schema defines fields and validators
2. The framework creates a new schema class that inherits from yours
3. Read-only fields are redefined as optional (`field: type | None = None`)
4. The validation method ignores read-only fields in incoming data
5. Result: A schema that preserves all functionality but ignores read-only fields

### Side Effects to Be Aware Of

- **Read-only fields become optional**: They default to `None` when not provided
- **Input filtering is transparent**: Read-only fields in request data are silently ignored
- **Validators work normally**: All field and model validators function as expected
- **Type hints reflect reality**: The generated schema accurately represents what fields are actually used

### Example

```python
class UserSchema(IDSchema[User]):
    name: str
    email: str
    
    @field_validator('email')
    def validate_email(cls, v):
        # This validator works in both original and update schemas
        return v.lower()
```

The update schema will:
- Inherit the email validator
- Make `id` optional (defaults to `None`)
- Ignore `id` if provided in input data
- Keep `name` and `email` as optional fields for partial updates

## Field-Level Read-Only Implementation

The `ReadOnly` function uses Python's `typing.Annotated` to attach metadata to type annotations:

```python
def ReadOnly(t: type[T]) -> type[T]:
    return Annotated[t, "readonly"]
```

### Detection Logic

The framework detects read-only fields by checking field metadata:

```python
def get_read_only_fields(model_cls: type[pydantic.BaseModel]) -> set[str]:
    read_only_fields: set[str] = set()
    
    # Get class-level read-only fields
    for cls in model_cls.mro():
        if "read_only_fields" in cls.__dict__:
            read_only_fields.update(cls.__dict__["read_only_fields"])
    
    # Get field-level read-only fields
    for field_name, field_info in model_cls.model_fields.items():
        if getattr(field_info, "metadata", None) and "readonly" in field_info.metadata:
            read_only_fields.add(field_name)
    
    return read_only_fields
```

### Benefits of This Approach

- **Type-safe**: Works with static type checkers and IDEs
- **Introspectable**: Metadata is preserved and can be accessed at runtime
- **Future-proof**: Uses standard Python typing mechanisms
- **Extensible**: Easy to add other field-level metadata in the future

## Read-Only Field Behavior

### Default Behavior: Silent Ignore

By default, read-only fields are **silently ignored** when provided in requests:

```python
# This request will succeed, with internal_id being ignored
POST /products/
{
    "name": "Test Product",
    "price": 29.99,
    "internal_id": "IGNORED"  # This field is ignored
}
```

### Why Silent Ignore?

This approach is chosen because:

1. **User-Friendly**: Clients can send full objects without filtering
2. **Backward Compatible**: Existing code won't break
3. **Flexible**: Common pattern in many APIs (Django REST Framework, etc.)
4. **Non-Breaking**: Prevents client errors from accidental field inclusion

### Optional Error-Raising

For development or debugging, you can enable strict validation by setting `raise_on_readonly=True` in the schema generation functions:

```python
# This would raise a ValueError if read-only fields are provided
create_model_without_read_only_fields(MySchema, raise_on_readonly=True)
create_model_with_optional_fields(MySchema, raise_on_readonly=True)
```

This is useful for:
- **Development**: Catching client bugs early
- **Debugging**: Understanding what fields are being sent
- **Strict APIs**: When you want explicit feedback about invalid requests 