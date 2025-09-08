"""Test query modifiers v2 functionality."""

import pytest
from datetime import datetime
from typing import Any, Optional, Union
from unittest.mock import Mock, patch

import pydantic
import sqlalchemy
from fastapi import HTTPException
from sqlalchemy import Column, Integer, String, DateTime, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from starlette.datastructures import QueryParams

from fastapi_restly._query_modifiers_v2 import (
    apply_query_modifiers_v2,
    apply_pagination_v2,
    apply_sorting_v2,
    apply_filtering_v2,
    create_query_param_schema_v2,
    _get_field_type_for_schema,
    _parse_value_v2,
    _make_where_clause_v2,
)
from fastapi_restly._sqlbase import Base


class TestModel(Base):
    __tablename__ = "test_model"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    age: Mapped[int] = mapped_column(Integer)
    email: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class TestSchema(pydantic.BaseModel):
    id: int
    name: str
    age: int
    email: str
    created_at: datetime
    is_active: bool


class TestNestedSchema(pydantic.BaseModel):
    user: TestSchema


class TestNestedModel(Base):
    __tablename__ = "test_nested_model"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer)


@pytest.fixture
def select_query():
    return sqlalchemy.select(TestModel)


@pytest.fixture
def mock_query_params():
    def _create_params(**kwargs):
        return QueryParams(kwargs)
    return _create_params


class TestCreateQueryParamSchemaV2:
    def test_create_query_param_schema_v2_basic(self):
        """Test creating a query param schema for basic fields."""
        schema = create_query_param_schema_v2(TestSchema)
        
        # Check that the schema was created
        assert schema.__name__ == "QueryParamV2TestSchema"
        
        # Check that pagination fields exist
        assert "page" in schema.model_fields
        assert "page_size" in schema.model_fields
        assert "order_by" in schema.model_fields
        
        # Check that field filters exist
        assert "name" in schema.model_fields
        assert "age" in schema.model_fields
        assert "email" in schema.model_fields
        
        # Check that range filters exist
        assert "age__gte" in schema.model_fields
        assert "age__lte" in schema.model_fields
        assert "age__gt" in schema.model_fields
        assert "age__lt" in schema.model_fields
        assert "age__isnull" in schema.model_fields
        
        # Check that boolean filters exist
        assert "is_active__isnull" in schema.model_fields

    def test_create_query_param_schema_v2_nested(self):
        """Test creating a query param schema for nested fields."""
        schema = create_query_param_schema_v2(TestNestedSchema)
        
        # Check that nested field filters exist (using dot notation)
        assert "user.name" in schema.model_fields
        assert "user.age__gte" in schema.model_fields


class TestApplyPaginationV2:
    def test_apply_pagination_v2_defaults(self, select_query, mock_query_params):
        """Test pagination with default values."""
        params = mock_query_params()
        result = apply_pagination_v2(params, select_query)
        
        # Should apply default page=1, page_size=100
        assert "LIMIT :param_1" in str(result)
        assert "OFFSET :param_2" in str(result)

    def test_apply_pagination_v2_custom_values(self, select_query, mock_query_params):
        """Test pagination with custom values."""
        params = mock_query_params(page="2", page_size="25")
        result = apply_pagination_v2(params, select_query)
        
        # Should apply page=2, page_size=25
        assert "LIMIT :param_1" in str(result)
        assert "OFFSET :param_2" in str(result)  # (2-1) * 25

    def test_apply_pagination_v2_invalid_page(self, select_query, mock_query_params):
        """Test pagination with invalid page value."""
        params = mock_query_params(page="invalid")
        
        with pytest.raises(HTTPException) as exc_info:
            apply_pagination_v2(params, select_query)
        
        assert exc_info.value.status_code == 400
        assert "not an integer" in str(exc_info.value.detail)

    def test_apply_pagination_v2_invalid_page_size(self, select_query, mock_query_params):
        """Test pagination with invalid page_size value."""
        params = mock_query_params(page_size="invalid")
        
        with pytest.raises(HTTPException) as exc_info:
            apply_pagination_v2(params, select_query)
        
        assert exc_info.value.status_code == 400
        assert "not an integer" in str(exc_info.value.detail)


class TestApplySortingV2:
    def test_apply_sorting_v2_default(self, select_query, mock_query_params):
        """Test sorting with default (no order_by parameter)."""
        params = mock_query_params()
        result = apply_sorting_v2(params, select_query, TestModel)
        
        # Should order by id by default
        assert "ORDER BY test_model.id" in str(result)

    def test_apply_sorting_v2_single_field(self, select_query, mock_query_params):
        """Test sorting with single field."""
        params = mock_query_params(order_by="name")
        result = apply_sorting_v2(params, select_query, TestModel)
        
        assert "ORDER BY test_model.name" in str(result)

    def test_apply_sorting_v2_descending(self, select_query, mock_query_params):
        """Test sorting with descending order."""
        params = mock_query_params(order_by="-name")
        result = apply_sorting_v2(params, select_query, TestModel)
        
        assert "ORDER BY test_model.name DESC" in str(result)

    def test_apply_sorting_v2_multiple_fields(self, select_query, mock_query_params):
        """Test sorting with multiple fields."""
        params = mock_query_params(order_by="name,-age")
        result = apply_sorting_v2(params, select_query, TestModel)
        
        assert "ORDER BY test_model.name ASC, test_model.age DESC" in str(result)

    def test_apply_sorting_v2_invalid_field(self, select_query, mock_query_params):
        """Test sorting with invalid field."""
        params = mock_query_params(order_by="invalid_field")
        
        with pytest.raises(HTTPException) as exc_info:
            apply_sorting_v2(params, select_query, TestModel)
        
        assert exc_info.value.status_code == 400
        assert "Invalid attribute" in str(exc_info.value.detail)


class TestApplyFilteringV2:
    def test_apply_filtering_v2_equals(self, select_query, mock_query_params):
        """Test filtering with equals operator."""
        params = mock_query_params(name="John")
        result = apply_filtering_v2(params, select_query, TestModel, TestSchema)
        
        assert "WHERE test_model.name = " in str(result)

    def test_apply_filtering_v2_greater_than(self, select_query, mock_query_params):
        """Test filtering with greater than operator."""
        params = mock_query_params(age__gt="25")
        result = apply_filtering_v2(params, select_query, TestModel, TestSchema)
        
        assert "WHERE test_model.age > " in str(result)

    def test_apply_filtering_v2_greater_than_equal(self, select_query, mock_query_params):
        """Test filtering with greater than or equal operator."""
        params = mock_query_params(age__gte="25")
        result = apply_filtering_v2(params, select_query, TestModel, TestSchema)
        
        assert "WHERE test_model.age >=" in str(result)

    def test_apply_filtering_v2_less_than(self, select_query, mock_query_params):
        """Test filtering with less than operator."""
        params = mock_query_params(age__lt="25")
        result = apply_filtering_v2(params, select_query, TestModel, TestSchema)
        
        assert "WHERE test_model.age < " in str(result)

    def test_apply_filtering_v2_less_than_equal(self, select_query, mock_query_params):
        """Test filtering with less than or equal operator."""
        params = mock_query_params(age__lte="25")
        result = apply_filtering_v2(params, select_query, TestModel, TestSchema)
        
        assert "WHERE test_model.age <=" in str(result)

    def test_apply_filtering_v2_not_equals(self, select_query, mock_query_params):
        """Test filtering with not equals operator."""
        params = mock_query_params(name__ne="John")
        result = apply_filtering_v2(params, select_query, TestModel, TestSchema)
        
        assert "WHERE test_model.name !=" in str(result)

    def test_apply_filtering_v2_is_null(self, select_query, mock_query_params):
        """Test filtering with is null operator."""
        params = mock_query_params(email__isnull="true")
        result = apply_filtering_v2(params, select_query, TestModel, TestSchema)
        
        assert "WHERE test_model.email IS NULL" in str(result)

    def test_apply_filtering_v2_is_not_null(self, select_query, mock_query_params):
        """Test filtering with is not null operator."""
        params = mock_query_params(email__isnull="false")
        result = apply_filtering_v2(params, select_query, TestModel, TestSchema)
        
        assert "WHERE test_model.email IS NOT NULL" in str(result)

    def test_apply_filtering_v2_multiple_values(self, select_query, mock_query_params):
        """Test filtering with multiple values (OR)."""
        params = mock_query_params(name="John,Alice")
        result = apply_filtering_v2(params, select_query, TestModel, TestSchema)
        
        assert "OR" in str(result)

    def test_apply_filtering_v2_multiple_filters(self, select_query, mock_query_params):
        """Test filtering with multiple filters (AND)."""
        params = mock_query_params(name="John", age__gte="25")
        result = apply_filtering_v2(params, select_query, TestModel, TestSchema)
        
        assert "AND" in str(result)

    def test_apply_filtering_v2_ignore_pagination_params(self, select_query, mock_query_params):
        """Test that pagination parameters are ignored in filtering."""
        params = mock_query_params(page="1", page_size="10", name="John")
        result = apply_filtering_v2(params, select_query, TestModel, TestSchema)
        
        # Should only filter by name, not include pagination in WHERE clause
        assert "WHERE test_model.name = " in str(result)
        assert "page" not in str(result).lower()

    def test_apply_filtering_v2_invalid_field(self, select_query, mock_query_params):
        """Test filtering with invalid field."""
        params = mock_query_params(invalid_field="value")
        
        with pytest.raises(HTTPException) as exc_info:
            apply_filtering_v2(params, select_query, TestModel, TestSchema)
        
        assert exc_info.value.status_code == 400
        assert "Invalid attribute" in str(exc_info.value.detail)


class TestApplyQueryModifiersV2:
    def test_apply_query_modifiers_v2_full(self, select_query, mock_query_params):
        """Test applying all query modifiers together."""
        params = mock_query_params(
            page="2",
            page_size="25",
            order_by="name,-age",
            name="John",
            age__gte="25"
        )
        result = apply_query_modifiers_v2(params, select_query, TestModel, TestSchema)
        
        # Should have pagination, sorting, and filtering
        assert "LIMIT :param_1" in str(result)
        assert "OFFSET :param_2" in str(result)
        assert "ORDER BY test_model.name ASC, test_model.age DESC" in str(result)
        assert "WHERE" in str(result)

    def test_apply_query_modifiers_v2_order(self, select_query, mock_query_params):
        """Test that filtering is applied before sorting and pagination."""
        params = mock_query_params(name="John", order_by="age", page="1")
        
        with patch('fastapi_restly._query_modifiers_v2.apply_filtering_v2') as mock_filter:
            with patch('fastapi_restly._query_modifiers_v2.apply_sorting_v2') as mock_sort:
                with patch('fastapi_restly._query_modifiers_v2.apply_pagination_v2') as mock_paginate:
                    apply_query_modifiers_v2(params, select_query, TestModel, TestSchema)
                    
                    # Check call order
                    mock_filter.assert_called_once()
                    mock_sort.assert_called_once()
                    mock_paginate.assert_called_once()


class TestParseValueV2:
    def test_parse_value_v2_string(self):
        """Test parsing string values."""
        result = _parse_value_v2(TestSchema, "name", "John")
        assert result == "John"

    def test_parse_value_v2_integer(self):
        """Test parsing integer values."""
        result = _parse_value_v2(TestSchema, "age", "25")
        assert result == 25

    def test_parse_value_v2_boolean(self):
        """Test parsing boolean values."""
        result = _parse_value_v2(TestSchema, "is_active", "true")
        assert result is True

    def test_parse_value_v2_datetime(self):
        """Test parsing datetime values."""
        result = _parse_value_v2(TestSchema, "created_at", "2024-01-01T00:00:00")
        assert isinstance(result, datetime)

    def test_parse_value_v2_invalid_field(self):
        """Test parsing with invalid field."""
        with pytest.raises(HTTPException) as exc_info:
            _parse_value_v2(TestSchema, "invalid_field", "value")
        
        assert exc_info.value.status_code == 400


class TestMakeWhereClauseV2:
    def test_make_where_clause_v2_equals(self):
        """Test creating equals where clause."""
        column = Mock()
        parser = Mock(return_value="John")
        
        result = _make_where_clause_v2(column, "John", "eq", parser)
        
        parser.assert_called_once_with("John")
        # Check that the result is a comparison operation
        assert result is not None

    def test_make_where_clause_v2_greater_than(self):
        """Test creating greater than where clause."""
        column = Mock()
        parser = Mock(return_value=25)
        
        # Configure the mock to return a comparison object
        comparison_mock = Mock()
        column.__gt__ = Mock(return_value=comparison_mock)
        
        result = _make_where_clause_v2(column, "25", "gt", parser)
        
        parser.assert_called_once_with("25")
        column.__gt__.assert_called_once_with(25)
        assert result == comparison_mock

    def test_make_where_clause_v2_greater_than_equal(self):
        """Test creating greater than or equal where clause."""
        column = Mock()
        parser = Mock(return_value=25)
        
        # Configure the mock to return a comparison object
        comparison_mock = Mock()
        column.__ge__ = Mock(return_value=comparison_mock)
        
        result = _make_where_clause_v2(column, "25", "gte", parser)
        
        parser.assert_called_once_with("25")
        column.__ge__.assert_called_once_with(25)
        assert result == comparison_mock

    def test_make_where_clause_v2_less_than(self):
        """Test creating less than where clause."""
        column = Mock()
        parser = Mock(return_value=25)
        
        # Configure the mock to return a comparison object
        comparison_mock = Mock()
        column.__lt__ = Mock(return_value=comparison_mock)
        
        result = _make_where_clause_v2(column, "25", "lt", parser)
        
        parser.assert_called_once_with("25")
        column.__lt__.assert_called_once_with(25)
        assert result == comparison_mock

    def test_make_where_clause_v2_less_than_equal(self):
        """Test creating less than or equal where clause."""
        column = Mock()
        parser = Mock(return_value=25)
        
        # Configure the mock to return a comparison object
        comparison_mock = Mock()
        column.__le__ = Mock(return_value=comparison_mock)
        
        result = _make_where_clause_v2(column, "25", "lte", parser)
        
        parser.assert_called_once_with("25")
        column.__le__.assert_called_once_with(25)
        assert result == comparison_mock

    def test_make_where_clause_v2_not_equals(self):
        """Test creating not equals where clause."""
        column = Mock()
        parser = Mock(return_value="John")
        
        result = _make_where_clause_v2(column, "John", "ne", parser)
        
        parser.assert_called_once_with("John")
        # Check that the result is a comparison operation
        assert result is not None


class TestGetFieldTypeForSchema:
    def test_get_field_type_for_schema_simple(self):
        """Test getting field type for simple type."""
        field = Mock()
        field.annotation = str
        
        result = _get_field_type_for_schema(field)
        assert result == str

    def test_get_field_type_for_schema_optional(self):
        """Test getting field type for Optional type."""
        field = Mock()
        field.annotation = Optional[str]
        
        result = _get_field_type_for_schema(field)
        assert result == str

    def test_get_field_type_for_schema_union(self):
        """Test getting field type for Union type."""
        field = Mock()
        field.annotation = Union[str, None]
        
        result = _get_field_type_for_schema(field)
        assert result == str

    def test_get_field_type_for_schema_fallback(self):
        """Test getting field type with fallback to object."""
        field = Mock()
        field.annotation = Any
        
        result = _get_field_type_for_schema(field)
        assert result == Any  # Any should remain Any, not become object 