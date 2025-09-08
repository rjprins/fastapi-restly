"""Test query modifiers v2 with Pydantic aliases."""

import pytest
from datetime import datetime
from typing import Any, Optional
from unittest.mock import Mock

import pydantic
import sqlalchemy
from fastapi import HTTPException
from sqlalchemy import Column, Integer, String, DateTime, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from starlette.datastructures import QueryParams

from fastapi_restly._query_modifiers_v2 import (
    apply_query_modifiers_v2,
    apply_filtering_v2,
    apply_sorting_v2,
    create_query_param_schema_v2,
    _iter_fields_including_nested_v2,
    _parse_value_v2,
)
from fastapi_restly._sqlbase import Base


class TestModel(Base):
    __tablename__ = "test_model_aliases"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    user_name: Mapped[str] = mapped_column(String(50))
    user_email: Mapped[str] = mapped_column(String(100))
    age: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class TestSchemaWithAliases(pydantic.BaseModel):
    id: int
    user_name: str = pydantic.Field(alias="userName")
    user_email: str = pydantic.Field(alias="userEmail")
    age: int
    created_at: datetime = pydantic.Field(alias="createdAt")
    is_active: bool = pydantic.Field(alias="isActive")


class TestSchemaWithPopulateByName(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(populate_by_name=True, from_attributes=True)
    
    id: int
    user_name: str = pydantic.Field(alias="userName")
    user_email: str = pydantic.Field(alias="userEmail")
    age: int
    created_at: datetime = pydantic.Field(alias="createdAt")
    is_active: bool = pydantic.Field(alias="isActive")


class TestSchemaWithoutAliases(pydantic.BaseModel):
    id: int
    user_name: str
    user_email: str
    age: int
    created_at: datetime
    is_active: bool


@pytest.fixture
def select_query():
    return sqlalchemy.select(TestModel)


@pytest.fixture
def mock_query_params():
    def _mock_query_params(**kwargs):
        params = {}
        for key, value in kwargs.items():
            if isinstance(value, (list, tuple)):
                params[key] = value
            else:
                params[key] = [str(value)]
        return QueryParams(params)
    return _mock_query_params


class TestCreateQueryParamSchemaV2WithAliases:
    def test_create_query_param_schema_v2_with_aliases(self):
        """Test creating a query param schema with aliases."""
        schema = create_query_param_schema_v2(TestSchemaWithAliases)
        
        # Check that the schema was created
        assert schema.__name__ == "QueryParamV2TestSchemaWithAliases"
        
        # Check that pagination fields exist
        assert "page" in schema.model_fields
        assert "page_size" in schema.model_fields
        assert "order_by" in schema.model_fields
        
        # Check that field filters use aliases
        assert "userName" in schema.model_fields  # Alias
        assert "userEmail" in schema.model_fields  # Alias
        assert "age" in schema.model_fields  # No alias
        assert "createdAt" in schema.model_fields  # Alias
        assert "isActive" in schema.model_fields  # Alias
        
        # Check that range filters use aliases
        assert "userName__gte" in schema.model_fields
        assert "userName__lte" in schema.model_fields
        assert "userEmail__isnull" in schema.model_fields
        assert "age__gte" in schema.model_fields
        assert "createdAt__gte" in schema.model_fields
        assert "isActive__isnull" in schema.model_fields

    def test_create_query_param_schema_v2_without_aliases(self):
        """Test creating a query param schema without aliases."""
        schema = create_query_param_schema_v2(TestSchemaWithoutAliases)
        
        # Check that field filters use field names
        assert "user_name" in schema.model_fields
        assert "user_email" in schema.model_fields
        assert "age" in schema.model_fields
        assert "created_at" in schema.model_fields
        assert "is_active" in schema.model_fields


class TestIterFieldsIncludingNestedV2WithAliases:
    def test_iter_fields_including_nested_v2_with_aliases(self):
        """Test field iteration with aliases."""
        fields = list(_iter_fields_including_nested_v2(TestSchemaWithAliases))
        
        # Check that aliases are used
        field_names = [name for name, _ in fields]
        assert "userName" in field_names
        assert "userEmail" in field_names
        assert "age" in field_names  # No alias
        assert "createdAt" in field_names
        assert "isActive" in field_names
        
        # Check that field names are not used
        assert "user_name" not in field_names
        assert "user_email" not in field_names
        assert "created_at" not in field_names
        assert "is_active" not in field_names

    def test_iter_fields_including_nested_v2_without_aliases(self):
        """Test field iteration without aliases."""
        fields = list(_iter_fields_including_nested_v2(TestSchemaWithoutAliases))
        
        # Check that field names are used
        field_names = [name for name, _ in fields]
        assert "user_name" in field_names
        assert "user_email" in field_names
        assert "age" in field_names
        assert "created_at" in field_names
        assert "is_active" in field_names


class TestParseValueV2WithAliases:
    def test_parse_value_v2_with_aliases(self):
        """Test value parsing with aliases."""
        # Test with alias
        result = _parse_value_v2(TestSchemaWithAliases, "userName", "John Doe")
        assert result == "John Doe"
        
        # Test with field name (should fail)
        with pytest.raises(HTTPException) as exc_info:
            _parse_value_v2(TestSchemaWithAliases, "user_name", "John Doe")
        assert exc_info.value.status_code == 400

    def test_parse_value_v2_with_populate_by_name(self):
        """Test value parsing with populate_by_name=True."""
        # Test with alias
        result = _parse_value_v2(TestSchemaWithPopulateByName, "userName", "John Doe")
        assert result == "John Doe"
        
        # Test with field name (should work with populate_by_name=True)
        result = _parse_value_v2(TestSchemaWithPopulateByName, "user_name", "John Doe")
        assert result == "John Doe"

    def test_parse_value_v2_without_aliases(self):
        """Test value parsing without aliases."""
        # Test with field name
        result = _parse_value_v2(TestSchemaWithoutAliases, "user_name", "John Doe")
        assert result == "John Doe"
        
        # Test with non-existent field
        with pytest.raises(HTTPException) as exc_info:
            _parse_value_v2(TestSchemaWithoutAliases, "userName", "John Doe")
        assert exc_info.value.status_code == 400


class TestApplyFilteringV2WithAliases:
    def test_apply_filtering_v2_with_aliases(self, select_query, mock_query_params):
        """Test filtering with aliases."""
        params = mock_query_params(userName="John Doe")
        result = apply_filtering_v2(params, select_query, TestModel, TestSchemaWithAliases)
        
        assert "WHERE test_model_aliases.user_name = " in str(result)

    def test_apply_filtering_v2_with_populate_by_name(self, select_query, mock_query_params):
        """Test filtering with populate_by_name=True."""
        # Test with alias
        params = mock_query_params(userName="John Doe")
        result = apply_filtering_v2(params, select_query, TestModel, TestSchemaWithPopulateByName)
        assert "WHERE test_model_aliases.user_name = " in str(result)
        
        # Test with field name
        params = mock_query_params(user_name="John Doe")
        result = apply_filtering_v2(params, select_query, TestModel, TestSchemaWithPopulateByName)
        assert "WHERE test_model_aliases.user_name = " in str(result)

    def test_apply_filtering_v2_without_aliases(self, select_query, mock_query_params):
        """Test filtering without aliases."""
        params = mock_query_params(user_name="John Doe")
        result = apply_filtering_v2(params, select_query, TestModel, TestSchemaWithoutAliases)
        
        assert "WHERE test_model_aliases.user_name = " in str(result)

    def test_apply_filtering_v2_range_filters_with_aliases(self, select_query, mock_query_params):
        """Test range filtering with aliases."""
        params = mock_query_params(age__gte="25", userEmail__isnull="false")
        result = apply_filtering_v2(params, select_query, TestModel, TestSchemaWithAliases)
        
        assert "WHERE test_model_aliases.age >=" in str(result)
        assert "test_model_aliases.user_email IS NOT NULL" in str(result)

    def test_apply_filtering_v2_range_filters_with_populate_by_name(self, select_query, mock_query_params):
        """Test range filtering with populate_by_name=True."""
        # Test with alias
        params = mock_query_params(age__gte="25", userEmail__isnull="false")
        result = apply_filtering_v2(params, select_query, TestModel, TestSchemaWithPopulateByName)
        assert "WHERE test_model_aliases.age >=" in str(result)
        assert "test_model_aliases.user_email IS NOT NULL" in str(result)
        
        # Test with field name
        params = mock_query_params(age__gte="25", user_email__isnull="false")
        result = apply_filtering_v2(params, select_query, TestModel, TestSchemaWithPopulateByName)
        assert "WHERE test_model_aliases.age >=" in str(result)
        assert "test_model_aliases.user_email IS NOT NULL" in str(result)


class TestApplySortingV2WithAliases:
    def test_apply_sorting_v2_with_aliases(self, select_query, mock_query_params):
        """Test sorting with aliases."""
        params = mock_query_params(order_by="userName,-age")
        result = apply_sorting_v2(params, select_query, TestModel, TestSchemaWithAliases)
        
        assert "ORDER BY test_model_aliases.user_name ASC, test_model_aliases.age DESC" in str(result)

    def test_apply_sorting_v2_with_populate_by_name(self, select_query, mock_query_params):
        """Test sorting with populate_by_name=True."""
        # Test with alias
        params = mock_query_params(order_by="userName,-age")
        result = apply_sorting_v2(params, select_query, TestModel, TestSchemaWithPopulateByName)
        assert "ORDER BY test_model_aliases.user_name ASC, test_model_aliases.age DESC" in str(result)
        
        # Test with field name
        params = mock_query_params(order_by="user_name,-age")
        result = apply_sorting_v2(params, select_query, TestModel, TestSchemaWithPopulateByName)
        assert "ORDER BY test_model_aliases.user_name ASC, test_model_aliases.age DESC" in str(result)

    def test_apply_sorting_v2_without_aliases(self, select_query, mock_query_params):
        """Test sorting without aliases."""
        params = mock_query_params(order_by="user_name,-age")
        result = apply_sorting_v2(params, select_query, TestModel, TestSchemaWithoutAliases)
        
        assert "ORDER BY test_model_aliases.user_name ASC, test_model_aliases.age DESC" in str(result)


class TestApplyQueryModifiersV2WithAliases:
    def test_apply_query_modifiers_v2_with_aliases(self, select_query, mock_query_params):
        """Test full query modifiers with aliases."""
        params = mock_query_params(
            page="2",
            page_size="25",
            order_by="userName,-age",
            userName="John Doe",
            age__gte="25"
        )
        result = apply_query_modifiers_v2(params, select_query, TestModel, TestSchemaWithAliases)
        
        # Should have pagination, sorting, and filtering
        assert "LIMIT :param_1" in str(result)
        assert "OFFSET :param_2" in str(result)
        assert "ORDER BY test_model_aliases.user_name ASC, test_model_aliases.age DESC" in str(result)
        assert "WHERE" in str(result)

    def test_apply_query_modifiers_v2_with_populate_by_name(self, select_query, mock_query_params):
        """Test full query modifiers with populate_by_name=True."""
        # Test with aliases
        params = mock_query_params(
            page="2",
            page_size="25",
            order_by="userName,-age",
            userName="John Doe",
            age__gte="25"
        )
        result = apply_query_modifiers_v2(params, select_query, TestModel, TestSchemaWithPopulateByName)
        
        # Should have pagination, sorting, and filtering
        assert "LIMIT :param_1" in str(result)
        assert "OFFSET :param_2" in str(result)
        assert "ORDER BY test_model_aliases.user_name ASC, test_model_aliases.age DESC" in str(result)
        assert "WHERE" in str(result)
        
        # Test with field names
        params = mock_query_params(
            page="2",
            page_size="25",
            order_by="user_name,-age",
            user_name="John Doe",
            age__gte="25"
        )
        result = apply_query_modifiers_v2(params, select_query, TestModel, TestSchemaWithPopulateByName)
        
        # Should have pagination, sorting, and filtering
        assert "LIMIT :param_1" in str(result)
        assert "OFFSET :param_2" in str(result)
        assert "ORDER BY test_model_aliases.user_name ASC, test_model_aliases.age DESC" in str(result)
        assert "WHERE" in str(result) 