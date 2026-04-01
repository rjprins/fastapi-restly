"""Test query modifiers v1 functionality."""

from datetime import datetime
from typing import Any, Optional, Union
from unittest.mock import Mock, patch

import pydantic
import pytest
import sqlalchemy
from fastapi import HTTPException
from pydantic.fields import FieldInfo
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from starlette.datastructures import QueryParams

from fastapi_restly.models import DataclassBase
from fastapi_restly.query._v1 import (
    _get_int,
    _is_string_field,
    _make_where_clause,
    _parse_value,
    apply_filtering,
    apply_pagination,
    apply_query_modifiers,
    apply_sorting,
    create_query_param_schema,
)


class TestModelV1(DataclassBase):
    __tablename__ = "test_model_v1"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    age: Mapped[int] = mapped_column(Integer)
    email: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class TestSchemaV1(pydantic.BaseModel):
    id: int
    name: str
    age: int
    email: str
    created_at: datetime
    is_active: bool


class TestNestedSchemaV1(pydantic.BaseModel):
    user: TestSchemaV1


class TestNestedModelV1(DataclassBase):
    __tablename__ = "test_nested_model_v1"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer)


# Test models for relation filtering
class UserModel(DataclassBase):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    email: Mapped[str] = mapped_column(String(100))

    # Relationship
    posts: Mapped[list["PostModel"]] = relationship("PostModel", back_populates="author")


class PostModel(DataclassBase):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(100))
    content: Mapped[str] = mapped_column(String(500))
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))

    # Relationship
    author: Mapped[UserModel] = relationship("UserModel", back_populates="posts")


class UserSchema(pydantic.BaseModel):
    id: int
    name: str
    email: str


class PostSchema(pydantic.BaseModel):
    id: int
    title: str
    content: str
    author: UserSchema


@pytest.fixture
def select_query():
    return sqlalchemy.select(TestModelV1)


@pytest.fixture
def post_select_query():
    return sqlalchemy.select(PostModel)


@pytest.fixture
def mock_query_params():
    def _create_params(**kwargs):
        return QueryParams(kwargs)
    return _create_params


class TestCreateQueryParamSchema:
    def test_create_query_param_schema_basic(self):
        """Test creating a query param schema for basic fields."""
        schema = create_query_param_schema(TestSchemaV1)

        # Check that the schema was created
        assert schema.__name__ == "QueryParamTestSchemaV1"

        # Check that pagination fields exist
        assert "limit" in schema.model_fields
        assert "offset" in schema.model_fields
        assert "sort" in schema.model_fields

        # Check that filter fields exist
        assert "filter[name]" in schema.model_fields
        assert "filter[age]" in schema.model_fields
        assert "filter[email]" in schema.model_fields

        # Check that contains fields exist for string fields
        assert "contains[name]" in schema.model_fields
        assert "contains[email]" in schema.model_fields
        assert "contains[created_at]" not in schema.model_fields  # datetime field
        assert "contains[age]" not in schema.model_fields  # int field

    def test_create_query_param_schema_nested(self):
        """Test creating a query param schema for nested fields."""
        schema = create_query_param_schema(TestNestedSchemaV1)

        # Check that nested filter fields exist
        assert "filter[user.name]" in schema.model_fields
        assert "filter[user.age]" in schema.model_fields

        # Check that nested contains fields exist for string fields
        assert "contains[user.name]" in schema.model_fields
        assert "contains[user.email]" in schema.model_fields
        assert "contains[user.age]" not in schema.model_fields  # int field

    def test_create_query_param_schema_with_relations(self):
        """Test creating a query param schema for models with relations."""
        schema = create_query_param_schema(PostSchema)

        # Check that relation filter fields exist
        assert "filter[author.name]" in schema.model_fields
        assert "filter[author.email]" in schema.model_fields

        # Check that relation contains fields exist for string fields
        assert "contains[author.name]" in schema.model_fields
        assert "contains[author.email]" in schema.model_fields


def _sql(query) -> str:
    return str(query.compile(compile_kwargs={"literal_binds": True}))


class TestApplyPagination:
    def test_apply_pagination_with_limit(self, select_query, mock_query_params):
        result = apply_pagination(mock_query_params(limit="10"), select_query)
        assert "LIMIT 10" in _sql(result)

    def test_apply_pagination_with_offset(self, select_query, mock_query_params):
        result = apply_pagination(mock_query_params(offset="20"), select_query)
        assert "OFFSET 20" in _sql(result)

    def test_apply_pagination_with_both(self, select_query, mock_query_params):
        result = apply_pagination(mock_query_params(limit="10", offset="20"), select_query)
        sql = _sql(result)
        assert "LIMIT 10" in sql
        assert "OFFSET 20" in sql

    def test_apply_pagination_no_params(self, select_query, mock_query_params):
        result = apply_pagination(mock_query_params(), select_query)
        sql = _sql(result).upper()
        assert "LIMIT" not in sql
        assert "OFFSET" not in sql

    def test_apply_pagination_invalid_limit(self, select_query, mock_query_params):
        with pytest.raises(HTTPException) as exc_info:
            apply_pagination(mock_query_params(limit="invalid"), select_query)
        assert exc_info.value.status_code == 400
        assert "limit" in str(exc_info.value.detail)

    def test_apply_pagination_invalid_offset(self, select_query, mock_query_params):
        with pytest.raises(HTTPException) as exc_info:
            apply_pagination(mock_query_params(offset="invalid"), select_query)
        assert exc_info.value.status_code == 400
        assert "offset" in str(exc_info.value.detail)


class TestApplySorting:
    def test_apply_sorting_default(self, select_query, mock_query_params):
        """Default sort applies ORDER BY id."""
        result = apply_sorting(mock_query_params(), select_query, TestModelV1)
        sql = str(result)
        assert "ORDER BY" in sql.upper()
        assert "id" in sql

    def test_apply_sorting_single_field(self, select_query, mock_query_params):
        result = apply_sorting(mock_query_params(sort="name"), select_query, TestModelV1)
        sql = str(result)
        assert "ORDER BY" in sql.upper()
        assert "name" in sql

    def test_apply_sorting_descending(self, select_query, mock_query_params):
        result = apply_sorting(mock_query_params(sort="-name"), select_query, TestModelV1)
        sql = str(result).upper()
        assert "ORDER BY" in sql
        assert "DESC" in sql

    def test_apply_sorting_multiple_fields(self, select_query, mock_query_params):
        result = apply_sorting(mock_query_params(sort="name,-age"), select_query, TestModelV1)
        sql = str(result)
        assert "name" in sql
        assert "age" in sql
        assert "DESC" in sql.upper()

    def test_apply_sorting_invalid_field(self, select_query, mock_query_params):
        with pytest.raises(HTTPException) as exc_info:
            apply_sorting(mock_query_params(sort="invalid_field"), select_query, TestModelV1)
        assert exc_info.value.status_code == 400
        assert "Invalid attribute" in str(exc_info.value.detail)


class TestApplyFiltering:
    def test_apply_filtering_equals(self, select_query, mock_query_params):
        result = apply_filtering(
            mock_query_params(**{"filter[name]": "John"}), select_query, TestModelV1, TestSchemaV1
        )
        assert "name = 'John'" in _sql(result)

    def test_apply_filtering_greater_than(self, select_query, mock_query_params):
        result = apply_filtering(
            mock_query_params(**{"filter[age]": ">25"}), select_query, TestModelV1, TestSchemaV1
        )
        assert "age > 25" in _sql(result)

    def test_apply_filtering_less_than(self, select_query, mock_query_params):
        result = apply_filtering(
            mock_query_params(**{"filter[age]": "<30"}), select_query, TestModelV1, TestSchemaV1
        )
        assert "age < 30" in _sql(result)

    def test_apply_filtering_not_equals(self, select_query, mock_query_params):
        result = apply_filtering(
            mock_query_params(**{"filter[name]": "!John"}), select_query, TestModelV1, TestSchemaV1
        )
        sql = _sql(result)
        assert "name != 'John'" in sql

    def test_apply_filtering_is_null(self, select_query, mock_query_params):
        result = apply_filtering(
            mock_query_params(**{"filter[email]": "null"}), select_query, TestModelV1, TestSchemaV1
        )
        assert "IS NULL" in _sql(result).upper()

    def test_apply_filtering_is_not_null(self, select_query, mock_query_params):
        result = apply_filtering(
            mock_query_params(**{"filter[email]": "!null"}), select_query, TestModelV1, TestSchemaV1
        )
        assert "IS NOT NULL" in _sql(result).upper()

    def test_apply_filtering_multiple_values(self, select_query, mock_query_params):
        """Multiple comma-separated values become OR clauses."""
        result = apply_filtering(
            mock_query_params(**{"filter[age]": "25,30,35"}), select_query, TestModelV1, TestSchemaV1
        )
        sql = _sql(result)
        assert "25" in sql and "30" in sql and "35" in sql

    def test_apply_filtering_multiple_filters(self, select_query, mock_query_params):
        """Multiple filter params are ANDed together."""
        result = apply_filtering(
            mock_query_params(**{"filter[name]": "John", "filter[age]": "25"}),
            select_query, TestModelV1, TestSchemaV1,
        )
        sql = _sql(result)
        assert "name = 'John'" in sql
        assert "age = 25" in sql

    def test_apply_filtering_invalid_field(self, select_query, mock_query_params):
        with pytest.raises(HTTPException) as exc_info:
            apply_filtering(
                mock_query_params(**{"filter[invalid]": "value"}), select_query, TestModelV1, TestSchemaV1
            )
        assert exc_info.value.status_code == 400
        assert "Invalid attribute" in str(exc_info.value.detail)


class TestApplyContainsFiltering:
    def test_apply_filtering_contains_single(self, select_query, mock_query_params):
        result = apply_filtering(
            mock_query_params(**{"contains[name]": "john"}), select_query, TestModelV1, TestSchemaV1
        )
        sql = _sql(result).upper()
        assert "LIKE" in sql
        assert "JOHN" in sql

    def test_apply_filtering_contains_multiple(self, select_query, mock_query_params):
        """Space-separated values become multiple LIKE OR clauses."""
        result = apply_filtering(
            mock_query_params(**{"contains[name]": "john jane"}), select_query, TestModelV1, TestSchemaV1
        )
        sql = _sql(result).upper()
        assert "LIKE" in sql
        assert "JOHN" in sql
        assert "JANE" in sql

    def test_apply_filtering_contains_with_filters(self, select_query, mock_query_params):
        result = apply_filtering(
            mock_query_params(**{"contains[name]": "john", "filter[age]": "25"}),
            select_query, TestModelV1, TestSchemaV1,
        )
        sql = _sql(result)
        assert "LIKE" in sql.upper()
        assert "age = 25" in sql

    def test_apply_filtering_contains_invalid_field(self, select_query, mock_query_params):
        with pytest.raises(HTTPException) as exc_info:
            apply_filtering(
                mock_query_params(**{"contains[invalid]": "value"}), select_query, TestModelV1, TestSchemaV1
            )
        assert exc_info.value.status_code == 400
        assert "Invalid attribute" in str(exc_info.value.detail)


class TestApplyQueryModifiers:
    def test_apply_query_modifiers_full(self, select_query, mock_query_params):
        """All modifiers (pagination, sort, filter, contains) applied together."""
        result = apply_query_modifiers(
            mock_query_params(
                limit="10", offset="20", sort="name,-age",
                **{"filter[name]": "John", "contains[email]": "example"},
            ),
            select_query, TestModelV1, TestSchemaV1,
        )
        sql = _sql(result)
        assert "LIMIT 10" in sql
        assert "OFFSET 20" in sql
        assert "ORDER BY" in sql.upper()
        assert "name = 'John'" in sql

    def test_apply_query_modifiers_order(self, select_query, mock_query_params):
        result = apply_query_modifiers(
            mock_query_params(limit="10", sort="name", **{"filter[name]": "John"}),
            select_query, TestModelV1, TestSchemaV1,
        )
        sql = _sql(result)
        assert "LIMIT 10" in sql
        assert "ORDER BY" in sql.upper()
        assert "name = 'John'" in sql


class TestRelationFiltering:
    """Test filtering and sorting on relation fields in query modifiers v1."""

    def test_apply_filtering_on_relation_field(self, post_select_query, mock_query_params):
        """Filtering on author.name adds a JOIN and WHERE clause referencing the users table."""
        result = apply_filtering(
            mock_query_params(**{"filter[author.name]": "John"}), post_select_query, PostModel, PostSchema
        )
        sql = _sql(result)
        assert "JOIN" in sql.upper()
        assert "name = 'John'" in sql

    def test_apply_filtering_on_relation_field_contains(self, post_select_query, mock_query_params):
        """Contains filter on a relation field adds a JOIN and LIKE clause."""
        result = apply_filtering(
            mock_query_params(**{"contains[author.name]": "john"}), post_select_query, PostModel, PostSchema
        )
        sql = _sql(result).upper()
        assert "JOIN" in sql
        assert "LIKE" in sql

    def test_apply_sorting_on_relation_field(self, post_select_query, mock_query_params):
        """Sorting on author.name adds a JOIN and ORDER BY clause."""
        result = apply_sorting(mock_query_params(sort="author.name"), post_select_query, PostModel)
        sql = str(result).upper()
        assert "ORDER BY" in sql
        assert "JOIN" in sql

    def test_apply_filtering_multiple_relation_filters(self, post_select_query, mock_query_params):
        """Multiple relation + direct filters produce JOIN and multiple WHERE conditions."""
        result = apply_filtering(
            mock_query_params(**{"filter[author.name]": "John", "filter[title]": "Test"}),
            post_select_query, PostModel, PostSchema,
        )
        sql = _sql(result)
        assert "JOIN" in sql.upper()
        assert "name = 'John'" in sql
        assert "title = 'Test'" in sql

    def test_apply_filtering_relation_field_email(self, post_select_query, mock_query_params):
        """Filtering on a different relation field (author.email) also adds JOIN."""
        result = apply_filtering(
            mock_query_params(**{"filter[author.email]": "john@example.com"}),
            post_select_query, PostModel, PostSchema,
        )
        sql = _sql(result)
        assert "JOIN" in sql.upper()
        assert "john@example.com" in sql


class TestParseValue:
    def test_parse_value_string(self):
        """Test parsing string values."""
        result = _parse_value(TestSchemaV1, "name", "John")
        assert result == "John"

    def test_parse_value_integer(self):
        """Test parsing integer values."""
        result = _parse_value(TestSchemaV1, "age", "25")
        assert result == 25

    def test_parse_value_boolean(self):
        """Test parsing boolean values."""
        result = _parse_value(TestSchemaV1, "is_active", "true")
        assert result is True

    def test_parse_value_datetime(self):
        """Test parsing datetime values."""
        result = _parse_value(TestSchemaV1, "created_at", "2024-01-01T00:00:00")
        assert isinstance(result, datetime)

    def test_parse_value_invalid_field(self):
        """Test parsing value with invalid field raises an exception."""
        with pytest.raises(Exception):
            _parse_value(TestSchemaV1, "invalid", "value")

    def test_parse_value_relation_field(self):
        """Test parsing value for a relation field."""
        result = _parse_value(PostSchema, "author.name", "John")
        assert result == "John"


class TestMakeWhereClause:
    """Test _make_where_clause using real SQLAlchemy model attributes."""

    def test_make_where_clause_equals(self):
        clause = _make_where_clause(TestModelV1.name, "John", str)
        assert "name = 'John'" in _sql(sqlalchemy.select(TestModelV1).where(clause))

    def test_make_where_clause_greater_than(self):
        clause = _make_where_clause(TestModelV1.age, ">25", int)
        assert "age > 25" in _sql(sqlalchemy.select(TestModelV1).where(clause))

    def test_make_where_clause_less_than(self):
        clause = _make_where_clause(TestModelV1.age, "<30", int)
        assert "age < 30" in _sql(sqlalchemy.select(TestModelV1).where(clause))

    def test_make_where_clause_greater_than_equal(self):
        clause = _make_where_clause(TestModelV1.age, ">=18", int)
        assert "age >= 18" in _sql(sqlalchemy.select(TestModelV1).where(clause))

    def test_make_where_clause_less_than_equal(self):
        clause = _make_where_clause(TestModelV1.age, "<=65", int)
        assert "age <= 65" in _sql(sqlalchemy.select(TestModelV1).where(clause))

    def test_make_where_clause_not_equals(self):
        clause = _make_where_clause(TestModelV1.name, "!John", str)
        assert "name != 'John'" in _sql(sqlalchemy.select(TestModelV1).where(clause))

    def test_make_where_clause_is_null(self):
        null_parser = lambda x: None if x == "null" else x
        clause = _make_where_clause(TestModelV1.email, "null", null_parser)
        assert "IS NULL" in _sql(sqlalchemy.select(TestModelV1).where(clause)).upper()


class TestIsStringField:
    def test_is_string_field_simple(self):
        """Test string field detection for simple string."""
        field = Mock(spec=FieldInfo)
        field.annotation = str
        assert _is_string_field(field) is True

    def test_is_string_field_optional(self):
        """Test string field detection for Optional[str]."""
        field = Mock(spec=FieldInfo)
        field.annotation = Optional[str]
        assert _is_string_field(field) is True

    def test_is_string_field_non_string(self):
        """Test string field detection for non-string field."""
        field = Mock(spec=FieldInfo)
        field.annotation = int
        assert _is_string_field(field) is False

    def test_is_string_field_optional_non_string(self):
        """Test string field detection for Optional[int]."""
        field = Mock(spec=FieldInfo)
        field.annotation = Optional[int]
        assert _is_string_field(field) is False


class TestGetInt:
    def test_get_int_valid(self):
        """Test getting integer from valid string."""
        result = _get_int(QueryParams({"limit": "10"}), "limit")
        assert result == 10

    def test_get_int_none(self):
        """Test getting integer when parameter is absent."""
        assert _get_int(QueryParams({}), "limit") is None

    def test_get_int_invalid(self):
        """Test getting integer from invalid string raises 400."""
        with pytest.raises(HTTPException) as exc_info:
            _get_int(QueryParams({"limit": "invalid"}), "limit")

        assert exc_info.value.status_code == 400
        assert "limit" in str(exc_info.value.detail)
