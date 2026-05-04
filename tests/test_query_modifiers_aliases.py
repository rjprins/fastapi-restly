"""Tests for list-params filtering with Pydantic aliases (flat and nested)."""

from datetime import datetime
from typing import Any, Optional
from unittest.mock import Mock

import pydantic
import pytest
import sqlalchemy
from fastapi import HTTPException
from sqlalchemy import Boolean, Column, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from starlette.datastructures import QueryParams

from fastapi_restly.models import DataclassBase
from fastapi_restly.query._impl import (
    _apply_filtering,
    _apply_sorting,
    _iter_fields_including_nested,
    _parse_value,
    apply_list_params,
    create_list_params_schema,
)


class AliasModel(DataclassBase):
    __tablename__ = "test_model_aliases"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_name: Mapped[str] = mapped_column(String(50))
    user_email: Mapped[str] = mapped_column(String(100))
    age: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class SchemaWithAliases(pydantic.BaseModel):
    id: int
    user_name: str = pydantic.Field(alias="userName")
    user_email: str = pydantic.Field(alias="userEmail")
    age: int
    created_at: datetime = pydantic.Field(alias="createdAt")
    is_active: bool = pydantic.Field(alias="isActive")


class SchemaWithPopulateByName(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(populate_by_name=True, from_attributes=True)

    id: int
    user_name: str = pydantic.Field(alias="userName")
    user_email: str = pydantic.Field(alias="userEmail")
    age: int
    created_at: datetime = pydantic.Field(alias="createdAt")
    is_active: bool = pydantic.Field(alias="isActive")


class SchemaWithoutAliases(pydantic.BaseModel):
    id: int
    user_name: str
    user_email: str
    age: int
    created_at: datetime
    is_active: bool


@pytest.fixture
def select_query():
    return sqlalchemy.select(AliasModel)


@pytest.fixture
def mock_query_params():
    def _mock_query_params(**kwargs):
        params = {key: str(value) for key, value in kwargs.items()}
        return QueryParams(params)

    return _mock_query_params


class TestCreateListParamsSchemaWithAliases:
    def test_create_list_params_schema_with_aliases(self):
        """Test creating a query param schema with aliases."""
        schema = create_list_params_schema(SchemaWithAliases)

        # Check that the schema was created
        assert schema.__name__ == "ListParamsSchemaWithAliases"

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

    def test_create_list_params_schema_without_aliases(self):
        """Test creating a query param schema without aliases."""
        schema = create_list_params_schema(SchemaWithoutAliases)

        # Check that field filters use field names
        assert "user_name" in schema.model_fields
        assert "user_email" in schema.model_fields
        assert "age" in schema.model_fields
        assert "created_at" in schema.model_fields
        assert "is_active" in schema.model_fields


class TestIterFieldsIncludingNestedWithAliases:
    def test_iter_fields_including_nested_with_aliases(self):
        """Test field iteration with aliases."""
        fields = list(_iter_fields_including_nested(SchemaWithAliases))

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

    def test_iter_fields_including_nested_without_aliases(self):
        """Test field iteration without aliases."""
        fields = list(_iter_fields_including_nested(SchemaWithoutAliases))

        # Check that field names are used
        field_names = [name for name, _ in fields]
        assert "user_name" in field_names
        assert "user_email" in field_names
        assert "age" in field_names
        assert "created_at" in field_names
        assert "is_active" in field_names


class TestParseValueWithAliases:
    def test_parse_value_with_aliases(self):
        """Test value parsing with aliases."""
        # Test with alias
        result = _parse_value(SchemaWithAliases, "userName", "John Doe")
        assert result == "John Doe"

        # Test with field name (should fail)
        with pytest.raises(HTTPException) as exc_info:
            _parse_value(SchemaWithAliases, "user_name", "John Doe")
        assert exc_info.value.status_code == 400

    def test_parse_value_with_populate_by_name_uses_alias_only(self):
        """``populate_by_name=True`` does NOT extend the list-params URL surface
        with Python field names. The alias is the only public name."""
        # Alias resolves.
        result = _parse_value(SchemaWithPopulateByName, "userName", "John Doe")
        assert result == "John Doe"

        # Python field name is rejected even with populate_by_name=True —
        # generated FastAPI endpoints would not have declared this query
        # parameter, so accepting it via the raw helper would be a
        # contract divergence.
        with pytest.raises(HTTPException) as exc_info:
            _parse_value(SchemaWithPopulateByName, "user_name", "John Doe")
        assert exc_info.value.status_code == 400

    def test_parse_value_without_aliases(self):
        """Test value parsing without aliases."""
        # Test with field name
        result = _parse_value(SchemaWithoutAliases, "user_name", "John Doe")
        assert result == "John Doe"

        # Test with non-existent field
        with pytest.raises(HTTPException) as exc_info:
            _parse_value(SchemaWithoutAliases, "userName", "John Doe")
        assert exc_info.value.status_code == 400


class TestApplyFilteringWithAliases:
    def test__apply_filtering_with_aliases(self, select_query, mock_query_params):
        """Test filtering with aliases."""
        params = mock_query_params(userName="John Doe")
        result = _apply_filtering(params, select_query, AliasModel, SchemaWithAliases)

        assert "WHERE test_model_aliases.user_name = " in str(result)

    def test__apply_filtering_with_populate_by_name_uses_alias_only(
        self, select_query, mock_query_params
    ):
        """populate_by_name=True does not change the list-params URL surface."""
        # Alias resolves.
        params = mock_query_params(userName="John Doe")
        result = _apply_filtering(
            params, select_query, AliasModel, SchemaWithPopulateByName
        )
        assert "WHERE test_model_aliases.user_name = " in str(result)

        # Python field name is rejected.
        params = mock_query_params(user_name="John Doe")
        with pytest.raises(HTTPException) as exc_info:
            _apply_filtering(params, select_query, AliasModel, SchemaWithPopulateByName)
        assert exc_info.value.status_code == 400

    def test__apply_filtering_without_aliases(self, select_query, mock_query_params):
        """Test filtering without aliases."""
        params = mock_query_params(user_name="John Doe")
        result = _apply_filtering(
            params, select_query, AliasModel, SchemaWithoutAliases
        )

        assert "WHERE test_model_aliases.user_name = " in str(result)

    def test__apply_filtering_range_filters_with_aliases(
        self, select_query, mock_query_params
    ):
        """Test range filtering with aliases."""
        params = mock_query_params(age__gte="25", userEmail__isnull="false")
        result = _apply_filtering(params, select_query, AliasModel, SchemaWithAliases)

        assert "WHERE test_model_aliases.age >=" in str(result)
        assert "test_model_aliases.user_email IS NOT NULL" in str(result)

    def test__apply_filtering_range_filters_with_populate_by_name(
        self, select_query, mock_query_params
    ):
        """Range filters with populate_by_name=True still use the alias only."""
        params = mock_query_params(age__gte="25", userEmail__isnull="false")
        result = _apply_filtering(
            params, select_query, AliasModel, SchemaWithPopulateByName
        )
        assert "WHERE test_model_aliases.age >=" in str(result)
        assert "test_model_aliases.user_email IS NOT NULL" in str(result)

        # Python field name on an aliased field is rejected.
        params = mock_query_params(user_email__isnull="false")
        with pytest.raises(HTTPException) as exc_info:
            _apply_filtering(params, select_query, AliasModel, SchemaWithPopulateByName)
        assert exc_info.value.status_code == 400


class TestApplySortingWithAliases:
    def test__apply_sorting_with_aliases(self, select_query, mock_query_params):
        """Test sorting with aliases."""
        params = mock_query_params(order_by="userName,-age")
        result = _apply_sorting(params, select_query, AliasModel, SchemaWithAliases)

        assert (
            "ORDER BY test_model_aliases.user_name ASC, test_model_aliases.age DESC"
            in str(result)
        )

    def test__apply_sorting_with_populate_by_name_uses_alias_only(
        self, select_query, mock_query_params
    ):
        """Sorting honours the alias even when populate_by_name=True."""
        params = mock_query_params(order_by="userName,-age")
        result = _apply_sorting(
            params, select_query, AliasModel, SchemaWithPopulateByName
        )
        assert (
            "ORDER BY test_model_aliases.user_name ASC, test_model_aliases.age DESC"
            in str(result)
        )

        # Sorting by the Python field name on an aliased field is rejected.
        params = mock_query_params(order_by="user_name")
        with pytest.raises(HTTPException) as exc_info:
            _apply_sorting(params, select_query, AliasModel, SchemaWithPopulateByName)
        assert exc_info.value.status_code == 400

    def test__apply_sorting_without_aliases(self, select_query, mock_query_params):
        """Test sorting without aliases."""
        params = mock_query_params(order_by="user_name,-age")
        result = _apply_sorting(params, select_query, AliasModel, SchemaWithoutAliases)

        assert (
            "ORDER BY test_model_aliases.user_name ASC, test_model_aliases.age DESC"
            in str(result)
        )


class TestApplyListParamsWithAliases:
    def test_apply_list_params_with_aliases(self, select_query, mock_query_params):
        """Test full query modifiers with aliases."""
        params = mock_query_params(
            page="2",
            page_size="25",
            order_by="userName,-age",
            userName="John Doe",
            age__gte="25",
        )
        result = apply_list_params(params, select_query, AliasModel, SchemaWithAliases)

        # Should have pagination, sorting, and filtering
        assert "LIMIT :param_1" in str(result)
        assert "OFFSET :param_2" in str(result)
        assert (
            "ORDER BY test_model_aliases.user_name ASC, test_model_aliases.age DESC"
            in str(result)
        )
        assert "WHERE" in str(result)

    def test_apply_list_params_with_populate_by_name_uses_alias_only(
        self, select_query, mock_query_params
    ):
        """Full pipeline: populate_by_name=True does not extend the URL surface."""
        params = mock_query_params(
            page="2",
            page_size="25",
            order_by="userName,-age",
            userName="John Doe",
            age__gte="25",
        )
        result = apply_list_params(
            params, select_query, AliasModel, SchemaWithPopulateByName
        )

        assert "LIMIT :param_1" in str(result)
        assert "OFFSET :param_2" in str(result)
        assert (
            "ORDER BY test_model_aliases.user_name ASC, test_model_aliases.age DESC"
            in str(result)
        )
        assert "WHERE" in str(result)

        # Filtering by the Python field name on an aliased field is rejected.
        params = mock_query_params(user_name="John Doe")
        with pytest.raises(HTTPException) as exc_info:
            apply_list_params(
                params, select_query, AliasModel, SchemaWithPopulateByName
            )
        assert exc_info.value.status_code == 400


class TestRelationAliases:
    """Aliased relation segments and aliased nested fields are both honoured.

    Documented public contract: list-param keys follow the *response schema's
    public names* end-to-end. If a relation field has ``Field(alias="writer")``
    and the nested schema's column has ``Field(alias="authorName")``, the URL
    parameter key is ``writer.authorName`` — not ``author.authorName`` and not
    ``writer.name``.
    """

    def _build(self):
        from sqlalchemy import ForeignKey
        from sqlalchemy.orm import relationship

        class Author(DataclassBase):
            __tablename__ = "ra_author"
            id: Mapped[int] = mapped_column(primary_key=True)
            name: Mapped[str] = mapped_column(String(50))

        class Article(DataclassBase):
            __tablename__ = "ra_article"
            id: Mapped[int] = mapped_column(primary_key=True)
            title: Mapped[str] = mapped_column(String(50))
            author_id: Mapped[int] = mapped_column(ForeignKey("ra_author.id"))
            author: Mapped[Author] = relationship()

        class AuthorSchema(pydantic.BaseModel):
            id: int
            name: str = pydantic.Field(alias="authorName")

        class ArticleSchema(pydantic.BaseModel):
            id: int
            title: str
            author: AuthorSchema = pydantic.Field(alias="writer")

        return Article, ArticleSchema

    def test_schema_uses_aliases_for_both_segments(self):
        _Article, ArticleSchema = self._build()
        fields = create_list_params_schema(ArticleSchema).model_fields
        assert "writer.authorName" in fields
        assert "writer.authorName__contains" in fields
        assert "writer.authorName__icontains" in fields
        # Canonical (non-aliased) names must NOT leak into the public surface.
        assert "author.name" not in fields
        assert "author.authorName" not in fields
        assert "writer.name" not in fields

    def test_filter_resolves_aliased_relation_path_to_sql(self, mock_query_params):
        Article, ArticleSchema = self._build()
        params = mock_query_params(**{"writer.authorName": "Alice"})
        rendered = str(
            _apply_filtering(params, sqlalchemy.select(Article), Article, ArticleSchema)
        )
        assert "JOIN ra_author" in rendered
        assert "ra_author.name = " in rendered

    def test_sort_resolves_aliased_relation_path(self, mock_query_params):
        Article, ArticleSchema = self._build()
        params = mock_query_params(order_by="-writer.authorName")
        rendered = str(
            _apply_sorting(params, sqlalchemy.select(Article), Article, ArticleSchema)
        )
        assert "ORDER BY ra_author.name DESC" in rendered
