"""Tests for the list-params query layer (filtering, sorting, pagination)."""

from datetime import datetime
from typing import Any, Optional, Union
from unittest.mock import Mock, patch

import pydantic
import pytest
import sqlalchemy
from fastapi import HTTPException
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from starlette.datastructures import QueryParams

from fastapi_restly.models import DataclassBase
from fastapi_restly.query import apply_list_params, create_list_params_schema
from fastapi_restly.query._impl import (
    _apply_filtering,
    _apply_pagination,
    _apply_sorting,
    _make_where_clause,
    _parse_value,
)


class WidgetModel(DataclassBase):
    __tablename__ = "test_model"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    age: Mapped[int] = mapped_column(Integer)
    email: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class WidgetSchema(pydantic.BaseModel):
    id: int
    name: str
    age: int
    email: str
    created_at: datetime
    is_active: bool


class NestedWidgetSchema(pydantic.BaseModel):
    user: WidgetSchema


class NestedWidgetModel(DataclassBase):
    __tablename__ = "test_nested_model"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer)


class UserModel(DataclassBase):
    __tablename__ = "relation_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    email: Mapped[str] = mapped_column(String(100))

    posts: Mapped[list["PostModel"]] = relationship(
        "PostModel", back_populates="author"
    )


class PostModel(DataclassBase):
    __tablename__ = "relation_posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(100))
    content: Mapped[str] = mapped_column(String(500))
    author_id: Mapped[int] = mapped_column(ForeignKey("relation_users.id"))

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


class AuditUserModel(DataclassBase):
    __tablename__ = "audit_relation_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))


class AuditLogModel(DataclassBase):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("audit_relation_users.id"))
    updater_id: Mapped[int] = mapped_column(ForeignKey("audit_relation_users.id"))

    creator: Mapped[AuditUserModel] = relationship(foreign_keys=[creator_id])
    updater: Mapped[AuditUserModel] = relationship(foreign_keys=[updater_id])


class AuditUserSchema(pydantic.BaseModel):
    id: int
    name: str


class AuditLogSchema(pydantic.BaseModel):
    id: int
    creator: AuditUserSchema
    updater: AuditUserSchema


@pytest.fixture
def select_query():
    return sqlalchemy.select(WidgetModel)


@pytest.fixture
def mock_query_params():
    def _create_params(**kwargs):
        return QueryParams(kwargs)

    return _create_params


@pytest.fixture
def post_select_query():
    return sqlalchemy.select(PostModel)


@pytest.fixture
def audit_log_select_query():
    return sqlalchemy.select(AuditLogModel)


class TestCreateListParamsSchema:
    def test_create_list_params_schema_basic(self):
        """Test creating a query param schema for basic fields."""
        schema = create_list_params_schema(WidgetSchema)

        # Check that the schema was created
        assert schema.__name__ == "ListParamsWidgetSchema"

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
        isnull_annotation = schema.model_fields["age__isnull"].annotation
        schema_types = {
            item["type"]
            for item in pydantic.TypeAdapter(isnull_annotation).json_schema()["anyOf"]
        }
        assert schema_types == {"boolean", "null"}

    def test_create_list_params_schema_nested(self):
        """Test creating a query param schema for nested fields."""
        schema = create_list_params_schema(NestedWidgetSchema)

        # Check that nested field filters exist (using dot notation)
        assert "user.name" in schema.model_fields
        assert "user.age__gte" in schema.model_fields

    def test_create_list_params_schema_nested_pep604_optional(self):
        """Optional nested schemas using X | None should still expand nested filters."""

        class OptionalNestedSchema(pydantic.BaseModel):
            user: WidgetSchema | None = None

        schema = create_list_params_schema(OptionalNestedSchema)

        assert "user.name" in schema.model_fields
        assert "user.email__contains" in schema.model_fields
        assert "user.email__icontains" in schema.model_fields


class TestApplyPagination:
    def test__apply_pagination_defaults(self, select_query, mock_query_params):
        """Without ``page_size`` no LIMIT/OFFSET is applied (default is unlimited)."""
        params = mock_query_params()
        result = _apply_pagination(params, select_query)

        assert "LIMIT" not in str(result)
        assert "OFFSET" not in str(result)

    def test__apply_pagination_custom_values(self, select_query, mock_query_params):
        """Test pagination with custom values."""
        params = mock_query_params(page="2", page_size="25")
        result = _apply_pagination(params, select_query)

        # Should apply page=2, page_size=25
        assert "LIMIT :param_1" in str(result)
        assert "OFFSET :param_2" in str(result)  # (2-1) * 25

    def test__apply_pagination_invalid_page(self, select_query, mock_query_params):
        """Invalid ``page`` is rejected — but only when pagination is engaged."""
        params = mock_query_params(page="invalid", page_size="10")

        with pytest.raises(HTTPException) as exc_info:
            _apply_pagination(params, select_query)

        assert exc_info.value.status_code == 400
        assert "not an integer" in str(exc_info.value.detail)

    def test__apply_pagination_invalid_page_size(self, select_query, mock_query_params):
        """Test pagination with invalid page_size value."""
        params = mock_query_params(page_size="invalid")

        with pytest.raises(HTTPException) as exc_info:
            _apply_pagination(params, select_query)

        assert exc_info.value.status_code == 400
        assert "not an integer" in str(exc_info.value.detail)


class TestApplySorting:
    def test__apply_sorting_default(self, select_query, mock_query_params):
        """Test sorting with default (no order_by parameter)."""
        params = mock_query_params()
        result = _apply_sorting(params, select_query, WidgetModel, WidgetSchema)

        # Should order by id by default
        assert "ORDER BY test_model.id" in str(result)

    def test__apply_sorting_single_field(self, select_query, mock_query_params):
        """Test sorting with single field."""
        params = mock_query_params(order_by="name")
        result = _apply_sorting(params, select_query, WidgetModel, WidgetSchema)

        assert "ORDER BY test_model.name" in str(result)

    def test__apply_sorting_descending(self, select_query, mock_query_params):
        """Test sorting with descending order."""
        params = mock_query_params(order_by="-name")
        result = _apply_sorting(params, select_query, WidgetModel, WidgetSchema)

        assert "ORDER BY test_model.name DESC" in str(result)

    def test__apply_sorting_multiple_fields(self, select_query, mock_query_params):
        """Test sorting with multiple fields."""
        params = mock_query_params(order_by="name,-age")
        result = _apply_sorting(params, select_query, WidgetModel, WidgetSchema)

        assert "ORDER BY test_model.name ASC, test_model.age DESC" in str(result)


class TestApplyFilteringIsNull:
    def test__apply_filtering_isnull_valid_boolean(
        self, select_query, mock_query_params
    ):
        params = mock_query_params(age__isnull="true")
        result = _apply_filtering(params, select_query, WidgetModel, WidgetSchema)

        assert "test_model.age IS NULL" in str(result)

    def test__apply_filtering_isnull_rejects_invalid_values(
        self, select_query, mock_query_params
    ):
        params = mock_query_params(age__isnull="123")

        with pytest.raises(HTTPException) as exc_info:
            _apply_filtering(params, select_query, WidgetModel, WidgetSchema)

        assert exc_info.value.status_code == 400

    def test__apply_sorting_invalid_field(self, select_query, mock_query_params):
        """Test sorting with invalid field."""
        params = mock_query_params(order_by="invalid_field")

        with pytest.raises(HTTPException) as exc_info:
            _apply_sorting(params, select_query, WidgetModel, WidgetSchema)

        assert exc_info.value.status_code == 400
        assert "Invalid attribute" in str(exc_info.value.detail)


class TestApplyFiltering:
    def test__apply_filtering_equals(self, select_query, mock_query_params):
        """Test filtering with equals operator."""
        params = mock_query_params(name="John")
        result = _apply_filtering(params, select_query, WidgetModel, WidgetSchema)

        assert "WHERE test_model.name = " in str(result)

    def test__apply_filtering_greater_than(self, select_query, mock_query_params):
        """Test filtering with greater than operator."""
        params = mock_query_params(age__gt="25")
        result = _apply_filtering(params, select_query, WidgetModel, WidgetSchema)

        assert "WHERE test_model.age > " in str(result)

    def test__apply_filtering_greater_than_equal(self, select_query, mock_query_params):
        """Test filtering with greater than or equal operator."""
        params = mock_query_params(age__gte="25")
        result = _apply_filtering(params, select_query, WidgetModel, WidgetSchema)

        assert "WHERE test_model.age >=" in str(result)

    def test__apply_filtering_less_than(self, select_query, mock_query_params):
        """Test filtering with less than operator."""
        params = mock_query_params(age__lt="25")
        result = _apply_filtering(params, select_query, WidgetModel, WidgetSchema)

        assert "WHERE test_model.age < " in str(result)

    def test__apply_filtering_less_than_equal(self, select_query, mock_query_params):
        """Test filtering with less than or equal operator."""
        params = mock_query_params(age__lte="25")
        result = _apply_filtering(params, select_query, WidgetModel, WidgetSchema)

        assert "WHERE test_model.age <=" in str(result)

    def test__apply_filtering_not_equals(self, select_query, mock_query_params):
        """Test filtering with not equals operator."""
        params = mock_query_params(name__ne="John")
        result = _apply_filtering(params, select_query, WidgetModel, WidgetSchema)

        assert "WHERE test_model.name !=" in str(result)

    def test__apply_filtering_is_null(self, select_query, mock_query_params):
        """Test filtering with is null operator."""
        params = mock_query_params(email__isnull="true")
        result = _apply_filtering(params, select_query, WidgetModel, WidgetSchema)

        assert "WHERE test_model.email IS NULL" in str(result)

    def test__apply_filtering_is_not_null(self, select_query, mock_query_params):
        """Test filtering with is not null operator."""
        params = mock_query_params(email__isnull="false")
        result = _apply_filtering(params, select_query, WidgetModel, WidgetSchema)

        assert "WHERE test_model.email IS NOT NULL" in str(result)

    def test__apply_filtering_multiple_values(self, select_query, mock_query_params):
        """Test filtering with multiple values (OR)."""
        params = mock_query_params(name="John,Alice")
        result = _apply_filtering(params, select_query, WidgetModel, WidgetSchema)

        assert "OR" in str(result)

    def test__apply_filtering_multiple_filters(self, select_query, mock_query_params):
        """Test filtering with multiple filters (AND)."""
        params = mock_query_params(name="John", age__gte="25")
        result = _apply_filtering(params, select_query, WidgetModel, WidgetSchema)

        assert "AND" in str(result)

    def test__apply_filtering_ignore_pagination_params(
        self, select_query, mock_query_params
    ):
        """Test that pagination parameters are ignored in filtering."""
        params = mock_query_params(page="1", page_size="10", name="John")
        result = _apply_filtering(params, select_query, WidgetModel, WidgetSchema)

        # Should only filter by name, not include pagination in WHERE clause
        assert "WHERE test_model.name = " in str(result)
        assert "page" not in str(result).lower()

    def test__apply_filtering_invalid_field(self, select_query, mock_query_params):
        """Test filtering with invalid field."""
        params = mock_query_params(invalid_field="value")

        with pytest.raises(HTTPException) as exc_info:
            _apply_filtering(params, select_query, WidgetModel, WidgetSchema)

        assert exc_info.value.status_code == 400
        assert "Invalid attribute" in str(exc_info.value.detail)

    def test__apply_filtering_relation_field_uses_join(
        self, post_select_query, mock_query_params
    ):
        """Relation filtering should join through the relationship instead of cross joining."""
        params = mock_query_params(**{"author.name": "Alice"})
        result = _apply_filtering(params, post_select_query, PostModel, PostSchema)

        rendered = str(result)
        assert (
            "JOIN relation_users ON relation_users.id = relation_posts.author_id"
            in rendered
        )
        assert "FROM relation_posts, relation_users" not in rendered
        assert "WHERE relation_users.name = " in rendered

    def test__apply_filtering_relation_field_handles_ambiguous_foreign_keys(
        self, audit_log_select_query, mock_query_params
    ):
        params = mock_query_params(**{"creator.name": "Alice"})
        result = _apply_filtering(
            params, audit_log_select_query, AuditLogModel, AuditLogSchema
        )

        rendered = str(result)
        assert (
            "JOIN audit_relation_users ON audit_relation_users.id = audit_logs.creator_id"
            in rendered
        )
        assert "WHERE audit_relation_users.name = " in rendered

    def test__apply_sorting_relation_field_handles_ambiguous_foreign_keys(
        self, audit_log_select_query, mock_query_params
    ):
        params = mock_query_params(order_by="creator.name,-id")
        result = _apply_sorting(
            params, audit_log_select_query, AuditLogModel, AuditLogSchema
        )

        rendered = str(result)
        assert (
            "JOIN audit_relation_users ON audit_relation_users.id = audit_logs.creator_id"
            in rendered
        )
        assert "ORDER BY audit_relation_users.name ASC, audit_logs.id DESC" in rendered


class TestApplyListParams:
    def test_apply_list_params_full(self, select_query, mock_query_params):
        """Test applying all query modifiers together."""
        params = mock_query_params(
            page="2", page_size="25", order_by="name,-age", name="John", age__gte="25"
        )
        result = apply_list_params(params, select_query, WidgetModel, WidgetSchema)

        # Should have pagination, sorting, and filtering
        assert "LIMIT :param_1" in str(result)
        assert "OFFSET :param_2" in str(result)
        assert "ORDER BY test_model.name ASC, test_model.age DESC" in str(result)
        assert "WHERE" in str(result)

    def test_apply_list_params_order(self, select_query, mock_query_params):
        """Test that filtering is applied before sorting and pagination."""
        params = mock_query_params(name="John", order_by="age", page="1")

        with patch("fastapi_restly.query._impl._apply_filtering") as mock_filter:
            with patch("fastapi_restly.query._impl._apply_sorting") as mock_sort:
                with patch(
                    "fastapi_restly.query._impl._apply_pagination"
                ) as mock_paginate:
                    apply_list_params(params, select_query, WidgetModel, WidgetSchema)

                    # Check call order
                    mock_filter.assert_called_once()
                    mock_sort.assert_called_once()
                    mock_paginate.assert_called_once()


class TestParseValue:
    def test_parse_value_string(self):
        """Test parsing string values."""
        result = _parse_value(WidgetSchema, "name", "John")
        assert result == "John"

    def test_parse_value_integer(self):
        """Test parsing integer values."""
        result = _parse_value(WidgetSchema, "age", "25")
        assert result == 25

    def test_parse_value_boolean(self):
        """Test parsing boolean values."""
        result = _parse_value(WidgetSchema, "is_active", "true")
        assert result is True

    def test_parse_value_datetime(self):
        """Test parsing datetime values."""
        result = _parse_value(WidgetSchema, "created_at", "2024-01-01T00:00:00")
        assert isinstance(result, datetime)

    def test_parse_value_invalid_field(self):
        """Test parsing with invalid field."""
        with pytest.raises(HTTPException) as exc_info:
            _parse_value(WidgetSchema, "invalid_field", "value")

        assert exc_info.value.status_code == 400


def _render_sql(query) -> str:
    return str(query.compile(compile_kwargs={"literal_binds": True}))


class TestMakeWhereClause:
    """Test _make_where_clause by compiling real SQL."""

    def test_make_where_clause_equals(self):
        clause = _make_where_clause(WidgetModel.name, "John", "eq", str)
        assert "name = 'John'" in _render_sql(
            sqlalchemy.select(WidgetModel).where(clause)
        )

    def test_make_where_clause_greater_than(self):
        clause = _make_where_clause(WidgetModel.age, "25", "gt", int)
        assert "age > 25" in _render_sql(sqlalchemy.select(WidgetModel).where(clause))

    def test_make_where_clause_greater_than_equal(self):
        clause = _make_where_clause(WidgetModel.age, "18", "gte", int)
        assert "age >= 18" in _render_sql(sqlalchemy.select(WidgetModel).where(clause))

    def test_make_where_clause_less_than(self):
        clause = _make_where_clause(WidgetModel.age, "30", "lt", int)
        assert "age < 30" in _render_sql(sqlalchemy.select(WidgetModel).where(clause))

    def test_make_where_clause_less_than_equal(self):
        clause = _make_where_clause(WidgetModel.age, "65", "lte", int)
        assert "age <= 65" in _render_sql(sqlalchemy.select(WidgetModel).where(clause))

    def test_make_where_clause_not_equals(self):
        clause = _make_where_clause(WidgetModel.name, "John", "ne", str)
        assert "name != 'John'" in _render_sql(
            sqlalchemy.select(WidgetModel).where(clause)
        )

    def test_make_where_clause_contains(self):
        """contains operator should compile to LIKE with wildcards."""
        clause = _make_where_clause(WidgetModel.name, "oh", "contains", str)
        sql = _render_sql(sqlalchemy.select(WidgetModel).where(clause))
        assert "%oh%" in sql or "%%oh%%" in sql
        assert "LIKE" in sql
        assert "lower" not in sql.lower()

    def test_make_where_clause_icontains(self):
        """icontains operator should compile to case-insensitive LIKE."""
        clause = _make_where_clause(WidgetModel.name, "oh", "icontains", str)
        sql = _render_sql(sqlalchemy.select(WidgetModel).where(clause))
        # SQLite renders ILIKE as LIKE LOWER(...).
        assert "%oh%" in sql or "%%oh%%" in sql
        assert "lower" in sql.lower() or "ILIKE" in sql
