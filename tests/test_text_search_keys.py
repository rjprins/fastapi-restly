"""``__icontains``: a search in text, on each field whose column holds text."""

import warnings
from typing import Literal, NewType, Optional

import pydantic
import pytest
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import Enum, ForeignKey, select
from sqlalchemy.exc import SADeprecationWarning
from sqlalchemy.orm import Mapped, column_property, mapped_column, relationship
from starlette.datastructures import QueryParams

import fastapi_restly as fr
from fastapi_restly.exc import BadQueryParam
from fastapi_restly.query._impl import (
    _apply_filtering,
    _filter_clause,
    derive_list_params_schema,
)
from fastapi_restly.query._shared import _escape_like_value

from ._models import SharedBase


class User(fr.IDMixin, SharedBase):
    name: Mapped[str] = mapped_column()
    email: Mapped[str] = mapped_column()
    description: Mapped[str] = mapped_column()
    age: Mapped[int] = mapped_column()


class PhoneUser(fr.IDMixin, SharedBase):
    name: Mapped[str] = mapped_column()
    email: Mapped[str | None] = mapped_column()
    phone: Mapped[str | None] = mapped_column()
    age: Mapped[int] = mapped_column()


class DotAddress(fr.IDMixin, SharedBase):
    street: Mapped[str] = mapped_column()
    city: Mapped[str] = mapped_column()


class DotUser(fr.IDMixin, SharedBase):
    name: Mapped[str] = mapped_column()
    email: Mapped[str] = mapped_column()
    address_id: Mapped[int] = mapped_column(ForeignKey("dot_address.id"))
    address: Mapped[DotAddress] = relationship()


class UserSchema(fr.IDSchema):
    name: str
    email: str
    description: str
    age: int


class TestWhichFieldsHaveIt:
    def test_a_text_column_has_it(self):
        fields = derive_list_params_schema(UserSchema, User).model_fields

        for name in ("name", "email", "description"):
            assert f"{name}__icontains" in fields
        assert "age__icontains" not in fields

    def test_the_column_decides_not_the_annotation(self):
        Slug = NewType("Slug", str)

        class Page(fr.IDMixin, SharedBase):
            url: Mapped[str]
            slug: Mapped[str]
            amount: Mapped[str]
            optional: Mapped[str | None]
            number: Mapped[int]
            status: Mapped[str] = mapped_column(Enum("a", "b", name="page_status"))
            kind: Mapped[str]
            flag: Mapped[str]

        class PageSchema(fr.IDSchema):
            url: HttpUrl
            slug: Slug
            amount: int
            optional: Optional[pydantic.AnyUrl] = None
            number: str  # text in a response, a number in the column
            status: str
            kind: Literal["a", "b"]
            flag: bool

        fields = derive_list_params_schema(PageSchema, Page).model_fields

        for name in ("url", "slug", "amount", "optional"):
            assert f"{name}__icontains" in fields, name
        for name in ("number", "status", "kind", "flag"):
            assert f"{name}__icontains" not in fields, name

    def test_an_expression_without_a_type_leaves_it_to_the_field(self):
        from sqlalchemy import literal_column

        class Tagged(fr.IDMixin, SharedBase):
            name: Mapped[str]
            label = column_property(literal_column("'x'"))
            count = column_property(literal_column("1"))

        class TaggedSchema(fr.IDSchema):
            name: str
            label: str
            count: int

        fields = derive_list_params_schema(TaggedSchema, Tagged).model_fields
        assert "label__icontains" in fields
        assert "count__icontains" not in fields

    def test_an_alias_gives_the_name_of_the_key(self):
        class Schema(BaseModel):
            name: str = Field(alias="userName")
            age: int

        fields = derive_list_params_schema(Schema, User).model_fields
        assert "userName__icontains" in fields
        assert "name__icontains" not in fields

    def test_a_nested_field_has_a_dotted_key(self):
        class Address(BaseModel):
            street: str
            city: str

        class Schema(BaseModel):
            name: str
            address: Address

        fields = derive_list_params_schema(
            Schema, DotUser, include_filters=("*", "*.*")
        ).model_fields
        assert "name__icontains" in fields
        assert "address.street__icontains" in fields
        assert "address.city__icontains" in fields

    def test_contains_is_gone(self):
        fields = derive_list_params_schema(UserSchema, User).model_fields
        assert "name__contains" not in fields
        with pytest.raises(BadQueryParam, match="Unsupported filter operator"):
            _apply_filtering(
                QueryParams("name__contains=a"), select(User), User, UserSchema
            )


class TestWhatItFinds:
    def test_it_takes_the_whole_text(self):
        stmt = _apply_filtering(
            QueryParams("name__icontains=john doe&age__gte=25"),
            select(User),
            User,
            UserSchema,
        )
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert sql.count("LIKE") == 1
        assert "john doe" in sql

    def test_it_is_an_ilike_with_an_escape(self):
        class MockColumn:
            def ilike(self, pattern, escape=None):
                return f"ILIKE {pattern} ESCAPE {escape}"

        result = _filter_clause(MockColumn(), "icontains", "john", str, "key")
        assert "ILIKE %john%" in str(result)
        assert "ESCAPE \\" in str(result)

    def test_wildcards_are_escaped(self):
        raw = r"100%_match\\"
        expected = r"100\%\_match\\\\"
        assert _escape_like_value(raw) == expected

    def test_an_unknown_field_fails(self):
        with pytest.raises(BadQueryParam):
            _apply_filtering(
                QueryParams("nonexistent__icontains=value"),
                select(User),
                User,
                UserSchema,
            )

    def test_an_empty_value_is_no_filter(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error", SADeprecationWarning)
            stmt = _apply_filtering(
                QueryParams("name__icontains="), select(User), User, UserSchema
            )
        assert "WHERE" not in str(stmt)
