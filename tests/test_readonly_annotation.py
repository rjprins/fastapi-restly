import pytest
from datetime import datetime
from typing import Annotated, get_origin, get_args

from fastapi_ding.schemas import (
    BaseSchema,
    ReadOnly,
    get_read_only_fields,
    create_model_without_read_only_fields,
    create_model_with_optional_fields,
    NOT_SET,
)


class TestReadOnlyAnnotation:
    """Test the ReadOnly annotation functionality."""

    def test_readonly_singleton_creation(self):
        """Test that ReadOnly is a singleton instance."""
        from fastapi_ding.schemas import ReadOnlyA
        
        # Test that ReadOnly is an instance of ReadOnlyA
        assert isinstance(ReadOnly, ReadOnlyA)
        
        # Test that it's a singleton
        readonly1 = ReadOnly
        readonly2 = ReadOnly
        assert readonly1 is readonly2

    def test_readonly_type_annotation(self):
        """Test that ReadOnly can be used in type annotations."""
        # Test basic usage
        result = ReadOnly[int]
        # Check that it's an Annotated type using get_origin
        assert get_origin(result) == Annotated
        args = get_args(result)
        assert args[0] == int
        assert "readonly" in args[1:]

        # Test with different types
        result = ReadOnly[str]
        assert get_origin(result) == Annotated
        args = get_args(result)
        assert args[0] == str
        assert "readonly" in args[1:]

        result = ReadOnly[datetime]
        assert get_origin(result) == Annotated
        args = get_args(result)
        assert args[0] == datetime
        assert "readonly" in args[1:]

    def test_readonly_in_pydantic_model(self):
        """Test that ReadOnly annotations work in Pydantic models."""
        class TestSchema(BaseSchema):
            id: ReadOnly[int]
            name: str
            email: str
            created_at: ReadOnly[datetime]

        # Test that the model can be instantiated
        schema = TestSchema(
            id=1,
            name="Test User",
            email="test@example.com",
            created_at=datetime.now()
        )
        
        assert schema.id == 1
        assert schema.name == "Test User"
        assert schema.email == "test@example.com"
        assert isinstance(schema.created_at, datetime)

    def test_get_read_only_fields(self):
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

    def test_create_model_without_read_only_fields(self):
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

    def test_create_model_with_optional_fields(self):
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
        # The default value is NOT_SET, not None
        update_schema = UpdateTestSchema()
        assert update_schema.name == NOT_SET
        assert update_schema.email == NOT_SET
        
        # Test that fields can be set
        update_schema = UpdateTestSchema(name="Updated", email="updated@example.com")
        assert update_schema.name == "Updated"
        assert update_schema.email == "updated@example.com"

    def test_readonly_with_inheritance(self):
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

    def test_readonly_with_generics(self):
        """Test that ReadOnly works with generic types."""
        from typing import List, Optional

        class TestSchema(BaseSchema):
            id: ReadOnly[int]
            tags: ReadOnly[List[str]]
            metadata: ReadOnly[Optional[dict]]

        # Test that the model can be instantiated
        schema = TestSchema(
            id=1,
            tags=["tag1", "tag2"],
            metadata={"key": "value"}
        )
        
        assert schema.id == 1
        assert schema.tags == ["tag1", "tag2"]
        assert schema.metadata == {"key": "value"}

    def test_readonly_field_validation(self):
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

    def test_readonly_in_timestamps_mixin(self):
        """Test that TimestampsSchemaMixin uses ReadOnly correctly."""
        from fastapi_ding.schemas import TimestampsSchemaMixin

        class TestSchema(TimestampsSchemaMixin, BaseSchema):
            name: str

        # Test that the mixin adds ReadOnly timestamp fields
        read_only_fields = get_read_only_fields(TestSchema)
        assert "created_at" in read_only_fields
        assert "updated_at" in read_only_fields
        assert "name" not in read_only_fields

    def test_readonly_in_idschema(self):
        """Test that IDSchema uses ReadOnly correctly."""
        from fastapi_ding.schemas import IDSchema
        from sqlalchemy.orm import DeclarativeBase

        class MockModel(DeclarativeBase):
            pass

        class TestIDSchema(IDSchema[MockModel]):
            pass

        # Test that IDSchema has ReadOnly id field
        read_only_fields = get_read_only_fields(TestIDSchema)
        assert "id" in read_only_fields 