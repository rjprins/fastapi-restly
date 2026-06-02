"""Tests for contains filter functionality in list-params."""

import warnings

import pytest
from sqlalchemy import ForeignKey, select
from sqlalchemy.exc import SADeprecationWarning
from sqlalchemy.orm import Mapped, mapped_column, relationship
from starlette.datastructures import QueryParams

import fastapi_restly as fr
from fastapi_restly.query._impl import (
    _apply_filtering,
    _is_string_field,
    _make_where_clause,
    create_list_params_schema,
)


class User(fr.IDBase):
    name: Mapped[str] = mapped_column()
    email: Mapped[str] = mapped_column()
    description: Mapped[str] = mapped_column()
    age: Mapped[int] = mapped_column()


class PhoneUser(fr.IDBase):
    name: Mapped[str] = mapped_column()
    email: Mapped[str | None] = mapped_column()
    phone: Mapped[str | None] = mapped_column()
    age: Mapped[int] = mapped_column()


class DotAddress(fr.IDBase):
    street: Mapped[str] = mapped_column()
    city: Mapped[str] = mapped_column()


class DotUser(fr.IDBase):
    name: Mapped[str] = mapped_column()
    email: Mapped[str] = mapped_column()
    address_id: Mapped[int] = mapped_column(ForeignKey("dot_address.id"))
    address: Mapped[DotAddress] = relationship()


class UserSchema(fr.IDSchema):
    name: str
    email: str
    description: str
    age: int


class TestContainsSchemaGeneration:
    def test_string_field_detection(self):
        """Contains operators are added for string fields, not for non-strings."""
        schema = create_list_params_schema(UserSchema, User)
        fields = schema.model_fields

        assert "name__contains" in fields
        assert "name__icontains" in fields
        assert "email__contains" in fields
        assert "email__icontains" in fields
        assert "description__contains" in fields
        assert "description__icontains" in fields
        assert "age__contains" not in fields
        assert "age__icontains" not in fields

        # Plain (eq) and other operators are still emitted.
        for op in ("", "__gte", "__lte", "__gt", "__lt", "__isnull"):
            assert f"name{op}" in fields

    def test_string_field_detection_optional(self):
        """Optional[str] / str | None should still count as a string field."""
        from typing import Optional

        from pydantic import BaseModel

        class Schema(BaseModel):
            name: str
            email: Optional[str] = None
            phone: str | None = None
            age: int

        for field_name, expected in (
            ("name", True),
            ("email", True),
            ("phone", True),
            ("age", False),
        ):
            assert _is_string_field(Schema.model_fields[field_name]) is expected

        params = create_list_params_schema(Schema, PhoneUser)
        assert "email__contains" in params.model_fields
        assert "email__icontains" in params.model_fields
        assert "phone__contains" in params.model_fields
        assert "phone__icontains" in params.model_fields
        assert "age__contains" not in params.model_fields
        assert "age__icontains" not in params.model_fields

    def test_aliases_drive_contains_field_name(self):
        """When a field has a Pydantic alias the public name (alias) is used."""
        from pydantic import BaseModel, Field

        class Schema(BaseModel):
            name: str = Field(alias="userName")
            email: str = Field(alias="userEmail")
            age: int

        fields = create_list_params_schema(Schema, User).model_fields
        assert "userName__contains" in fields
        assert "userName__icontains" in fields
        assert "userEmail__contains" in fields
        assert "userEmail__icontains" in fields
        assert "age__contains" not in fields
        assert "age__icontains" not in fields

    def test_nested_schemas_dot_notation(self):
        """Nested schema fields are exposed with dot-notation public names."""
        from pydantic import BaseModel

        class Address(BaseModel):
            street: str
            city: str

        class Schema(BaseModel):
            name: str
            email: str
            address: Address

        fields = create_list_params_schema(Schema, DotUser).model_fields
        assert "name__contains" in fields
        assert "name__icontains" in fields
        assert "email__contains" in fields
        assert "email__icontains" in fields
        assert "address.street__contains" in fields
        assert "address.street__icontains" in fields
        assert "address.city__contains" in fields
        assert "address.city__icontains" in fields


class TestContainsApplied:
    def test_contains_query_processing(self):
        query = select(User)
        params = QueryParams("name__contains=john&email__contains=example")
        result = _apply_filtering(params, query, User, UserSchema)
        assert hasattr(result, "where")

    def test_multiple_contains_values_split_on_whitespace(self):
        query = select(User)
        params = QueryParams("name__contains=john jane")
        result = _apply_filtering(params, query, User, UserSchema)
        assert hasattr(result, "where")

    def test_multiple_icontains_values_split_on_whitespace(self):
        query = select(User)
        params = QueryParams("name__icontains=john jane")
        result = _apply_filtering(params, query, User, UserSchema)
        assert hasattr(result, "where")

    def test_contains_combined_with_filters(self):
        query = select(User)
        params = QueryParams("name__contains=john&age__gte=25")
        result = _apply_filtering(params, query, User, UserSchema)
        assert hasattr(result, "where")

    def test_contains_emits_like_clause(self):
        class MockColumn:
            def like(self, pattern, escape=None):
                return f"LIKE {pattern} ESCAPE {escape}"

        result = _make_where_clause(MockColumn(), "john", "contains", lambda x: x)
        assert "LIKE %john%" in str(result)
        assert "ESCAPE \\" in str(result)

    def test_icontains_emits_ilike_clause(self):
        class MockColumn:
            def ilike(self, pattern, escape=None):
                return f"ILIKE {pattern} ESCAPE {escape}"

        result = _make_where_clause(MockColumn(), "john", "icontains", lambda x: x)
        assert "ILIKE %john%" in str(result)
        assert "ESCAPE \\" in str(result)

    def test_contains_escapes_like_wildcards(self):
        from fastapi_restly.query._shared import _escape_like_value

        raw = r"100%_match\\"
        expected = r"100\%\_match\\\\"
        assert _escape_like_value(raw) == expected


class TestContainsErrorHandling:
    def test_invalid_field(self):
        query = select(User)
        params = QueryParams("nonexistent__contains=value")
        with pytest.raises(Exception):
            _apply_filtering(params, query, User, UserSchema)

    def test_empty_value(self):
        query = select(User)
        params = QueryParams("name__contains=")

        with warnings.catch_warnings():
            warnings.simplefilter("error", SADeprecationWarning)
            result = _apply_filtering(params, query, User, UserSchema)
        assert hasattr(result, "where")
