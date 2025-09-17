import pytest
from datetime import datetime
from typing import Annotated, get_origin, get_args

from fastapi_restly._schemas import (
    BaseSchema,
    ReadOnly,
    WriteOnly,
    get_read_only_fields,
    get_write_only_fields,
    is_field_readonly,
    is_field_writeonly,
    create_model_without_read_only_fields,
    create_model_with_optional_fields,
    readonly_marker,
    writeonly_marker,
)


def test_readonly_type_annotation():
    """Test that ReadOnly can be used in type annotations."""
    # Test basic usage
    result = ReadOnly[int]
    # Check that it's an Annotated type using get_origin
    assert get_origin(result) == Annotated
    args = get_args(result)
    assert args[0] == int
    assert readonly_marker in args[1:]

    # Test with different types
    result = ReadOnly[str]
    assert get_origin(result) == Annotated
    args = get_args(result)
    assert args[0] == str
    assert readonly_marker in args[1:]

    result = ReadOnly[datetime]
    assert get_origin(result) == Annotated
    args = get_args(result)
    assert args[0] == datetime
    assert readonly_marker in args[1:]


def test_writeonly_type_annotation():
    """Test that WriteOnly can be used in type annotations."""
    # Test basic usage
    result = WriteOnly[int]
    # Check that it's an Annotated type using get_origin
    assert get_origin(result) == Annotated
    args = get_args(result)
    assert args[0] == int
    assert writeonly_marker in args[1:]

    # Test with different types
    result = WriteOnly[str]
    assert get_origin(result) == Annotated
    args = get_args(result)
    assert args[0] == str
    assert writeonly_marker in args[1:]

    result = WriteOnly[datetime]
    assert get_origin(result) == Annotated
    args = get_args(result)
    assert args[0] == datetime
    assert writeonly_marker in args[1:]


def test_readonly_in_pydantic_model():
    """Test that ReadOnly annotations work in Pydantic models."""

    class TestSchema(BaseSchema):
        id: ReadOnly[int]
        name: str
        email: str
        created_at: ReadOnly[datetime]

    # Test that the model can be instantiated
    schema = TestSchema(
        id=1, name="Test User", email="test@example.com", created_at=datetime.now()
    )

    assert schema.id == 1
    assert schema.name == "Test User"
    assert schema.email == "test@example.com"
    assert isinstance(schema.created_at, datetime)


def test_writeonly_in_pydantic_model():
    """Test that WriteOnly annotations work in Pydantic models."""

    class TestSchema(BaseSchema):
        id: WriteOnly[int]
        name: str
        email: str
        password: WriteOnly[str]

    # Test that the model can be instantiated
    schema = TestSchema(
        id=1, name="Test User", email="test@example.com", password="secret123"
    )

    assert schema.id == 1
    assert schema.name == "Test User"
    assert schema.email == "test@example.com"
    assert schema.password == "secret123"


def test_get_read_only_fields():
    """Test that get_read_only_fields correctly identifies ReadOnly fields."""

    class TestSchema(BaseSchema):
        id: ReadOnly[int]
        name: str
        email: str
        created_at: ReadOnly[datetime]
        updated_at: ReadOnly[datetime]

    read_only_fields = get_read_only_fields(TestSchema)

    # Should find all ReadOnly fields
    assert "id" in read_only_fields
    assert "created_at" in read_only_fields
    assert "updated_at" in read_only_fields

    # Should not include non-ReadOnly fields
    assert "name" not in read_only_fields
    assert "email" not in read_only_fields


def test_get_write_only_fields():
    """Test that get_write_only_fields correctly identifies WriteOnly fields."""

    class TestSchema(BaseSchema):
        id: WriteOnly[int]
        name: str
        email: str
        password: WriteOnly[str]
        secret_key: WriteOnly[str]

    write_only_fields = get_write_only_fields(TestSchema)

    # Should find all WriteOnly fields
    assert "id" in write_only_fields
    assert "password" in write_only_fields
    assert "secret_key" in write_only_fields

    # Should not include non-WriteOnly fields
    assert "name" not in write_only_fields
    assert "email" not in write_only_fields


def test_is_field_readonly():
    """Test that is_field_readonly correctly identifies ReadOnly fields."""

    class TestSchema(BaseSchema):
        id: ReadOnly[int]
        name: str
        email: str
        created_at: ReadOnly[datetime]

    # Test ReadOnly fields
    assert is_field_readonly(TestSchema, "id") is True
    assert is_field_readonly(TestSchema, "created_at") is True

    # Test non-ReadOnly fields
    assert is_field_readonly(TestSchema, "name") is False
    assert is_field_readonly(TestSchema, "email") is False

    # Test non-existent field
    assert is_field_readonly(TestSchema, "non_existent") is False


def test_is_field_writeonly():
    """Test that is_field_writeonly correctly identifies WriteOnly fields."""

    class TestSchema(BaseSchema):
        id: WriteOnly[int]
        name: str
        email: str
        password: WriteOnly[str]

    # Test WriteOnly fields
    assert is_field_writeonly(TestSchema, "id") is True
    assert is_field_writeonly(TestSchema, "password") is True

    # Test non-WriteOnly fields
    assert is_field_writeonly(TestSchema, "name") is False
    assert is_field_writeonly(TestSchema, "email") is False

    # Test non-existent field
    assert is_field_writeonly(TestSchema, "non_existent") is False


def test_create_model_without_read_only_fields():
    """Test that create_model_without_read_only_fields removes ReadOnly fields."""

    class TestSchema(BaseSchema):
        id: ReadOnly[int]
        name: str
        email: str
        created_at: ReadOnly[datetime]

    # Create a model without read-only fields
    CreateTestSchema = create_model_without_read_only_fields(TestSchema)

    # Check that the new model has the correct name
    assert CreateTestSchema.__name__ == "CreateTestSchema"

    # Check that ReadOnly fields are removed
    assert "id" not in CreateTestSchema.model_fields
    assert "created_at" not in CreateTestSchema.model_fields

    # Check that non-ReadOnly fields are preserved
    assert "name" in CreateTestSchema.model_fields
    assert "email" in CreateTestSchema.model_fields

    # Test that the new model can be instantiated
    create_schema = CreateTestSchema(name="Test", email="test@example.com")
    assert create_schema.name == "Test"
    assert create_schema.email == "test@example.com"


def test_create_model_with_optional_fields():
    """Test that create_model_with_optional_fields makes fields optional."""

    class TestSchema(BaseSchema):
        id: ReadOnly[int]
        name: str
        email: str
        created_at: ReadOnly[datetime]

    # Create a model with optional fields
    UpdateTestSchema = create_model_with_optional_fields(TestSchema)

    # Check that the new model has the correct name
    assert UpdateTestSchema.__name__ == "UpdateTestSchema"

    # Check that ReadOnly fields are removed
    assert "id" not in UpdateTestSchema.model_fields
    assert "created_at" not in UpdateTestSchema.model_fields

    # Check that non-ReadOnly fields are made optional
    assert "name" in UpdateTestSchema.model_fields
    assert "email" in UpdateTestSchema.model_fields

    # Test that the new model can be instantiated with optional fields
    update_schema = UpdateTestSchema()
    assert update_schema.name == None
    assert update_schema.email == None

    # Test that fields can be set
    update_schema = UpdateTestSchema(name="Updated", email="updated@example.com")
    assert update_schema.name == "Updated"
    assert update_schema.email == "updated@example.com"


def test_readonly_with_inheritance():
    """Test that ReadOnly works correctly with inheritance."""

    class BaseTestSchema(BaseSchema):
        id: ReadOnly[int]
        name: str

    class DerivedTestSchema(BaseTestSchema):
        email: str
        created_at: ReadOnly[datetime]

    # Test that both base and derived ReadOnly fields are detected
    read_only_fields = get_read_only_fields(DerivedTestSchema)
    assert "id" in read_only_fields
    assert "created_at" in read_only_fields
    assert "name" not in read_only_fields
    assert "email" not in read_only_fields


def test_writeonly_with_inheritance():
    """Test that WriteOnly works correctly with inheritance."""

    class BaseTestSchema(BaseSchema):
        id: WriteOnly[int]
        name: str

    class DerivedTestSchema(BaseTestSchema):
        email: str
        password: WriteOnly[str]

    # Test that both base and derived WriteOnly fields are detected
    write_only_fields = get_write_only_fields(DerivedTestSchema)
    assert "id" in write_only_fields
    assert "password" in write_only_fields
    assert "name" not in write_only_fields
    assert "email" not in write_only_fields


def test_readonly_with_generics():
    """Test that ReadOnly works with generic types."""
    from typing import List, Optional

    class TestSchema(BaseSchema):
        id: ReadOnly[int]
        tags: ReadOnly[List[str]]
        metadata: ReadOnly[Optional[dict]]

    # Test that the model can be instantiated
    schema = TestSchema(id=1, tags=["tag1", "tag2"], metadata={"key": "value"})

    assert schema.id == 1
    assert schema.tags == ["tag1", "tag2"]
    assert schema.metadata == {"key": "value"}


def test_writeonly_with_generics():
    """Test that WriteOnly works with generic types."""
    from typing import List, Optional

    class TestSchema(BaseSchema):
        id: WriteOnly[int]
        tags: WriteOnly[List[str]]
        metadata: WriteOnly[Optional[dict]]

    # Test that the model can be instantiated
    schema = TestSchema(id=1, tags=["tag1", "tag2"], metadata={"key": "value"})

    assert schema.id == 1
    assert schema.tags == ["tag1", "tag2"]
    assert schema.metadata == {"key": "value"}


def test_readonly_field_validation():
    """Test that ReadOnly fields are properly validated."""

    class TestSchema(BaseSchema):
        id: ReadOnly[int]
        name: str

    # Test that validation works for ReadOnly fields
    schema = TestSchema(id=42, name="Test")
    assert schema.id == 42

    # Test that invalid types are caught
    with pytest.raises(ValueError):
        TestSchema(id="not_an_int", name="Test")


def test_writeonly_field_validation():
    """Test that WriteOnly fields are properly validated."""

    class TestSchema(BaseSchema):
        id: WriteOnly[int]
        name: str

    # Test that validation works for WriteOnly fields
    schema = TestSchema(id=42, name="Test")
    assert schema.id == 42

    # Test that invalid types are caught
    with pytest.raises(ValueError):
        TestSchema(id="not_an_int", name="Test")


def test_readonly_in_timestamps_mixin():
    """Test that TimestampsSchemaMixin uses ReadOnly correctly."""
    from fastapi_restly._schemas import TimestampsSchemaMixin

    class TestSchema(TimestampsSchemaMixin, BaseSchema):
        name: str

    # Test that the mixin adds ReadOnly timestamp fields
    read_only_fields = get_read_only_fields(TestSchema)
    assert "created_at" in read_only_fields
    assert "updated_at" in read_only_fields
    assert "name" not in read_only_fields


def test_readonly_in_idschema():
    """Test that IDSchema uses ReadOnly correctly."""
    from fastapi_restly._schemas import IDSchema
    from sqlalchemy.orm import DeclarativeBase

    class MockModel(DeclarativeBase):
        pass

    class TestIDSchema(IDSchema[MockModel]):
        pass

    # Test that IDSchema has ReadOnly id field
    read_only_fields = get_read_only_fields(TestIDSchema)
    assert "id" in read_only_fields


def test_mixed_readonly_writeonly_fields():
    """Test that ReadOnly and WriteOnly fields can coexist in the same model."""

    class TestSchema(BaseSchema):
        id: ReadOnly[int]
        name: str
        password: WriteOnly[str]
        created_at: ReadOnly[datetime]
        secret_token: WriteOnly[str]

    # Test field detection
    read_only_fields = get_read_only_fields(TestSchema)
    write_only_fields = get_write_only_fields(TestSchema)

    assert "id" in read_only_fields
    assert "created_at" in read_only_fields
    assert "password" not in read_only_fields
    assert "secret_token" not in read_only_fields

    assert "password" in write_only_fields
    assert "secret_token" in write_only_fields
    assert "id" not in write_only_fields
    assert "created_at" not in write_only_fields

    # Test that the model can be instantiated
    schema = TestSchema(
        id=1,
        name="Test User",
        password="secret123",
        created_at=datetime.now(),
        secret_token="abc123"
    )

    assert schema.id == 1
    assert schema.name == "Test User"
    assert schema.password == "secret123"
    assert isinstance(schema.created_at, datetime)
    assert schema.secret_token == "abc123"
