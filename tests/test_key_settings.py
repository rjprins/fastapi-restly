"""The key settings: ``include_filters``, ``exclude_filters``,
``include_sort_keys`` and ``exclude_sort_keys``.

An entry is a key as the client sends it, or a pattern. ``*`` matches any
characters except ``.``, and ``**`` matches any characters. By default a list
has the keys of the resource's own fields, and no dotted keys.
"""

import asyncio
import datetime
from typing import Any

import pydantic
import pytest
from fastapi import FastAPI
from sqlalchemy import ForeignKey, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from starlette.datastructures import QueryParams

import fastapi_restly as fr
from fastapi_restly.exc import BadQueryParam, RestlyConfigurationError
from fastapi_restly.query import apply_list_params, derive_schema_list_params
from fastapi_restly.query._impl import _record_of
from fastapi_restly.testing import RestlyTestClient


class Base(DeclarativeBase):
    pass


class Country(Base):
    __tablename__ = "keys_country"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]


class Vendor(Base):
    __tablename__ = "keys_vendor"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    country_id: Mapped[int] = mapped_column(ForeignKey(Country.id))
    country: Mapped[Country] = relationship()


class Item(Base):
    __tablename__ = "keys_item"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    username: Mapped[str]
    created_at: Mapped[datetime.datetime]
    active: Mapped[bool]
    vendor_id: Mapped[int] = mapped_column(ForeignKey(Vendor.id))
    vendor: Mapped[Vendor] = relationship()
    tags: Mapped[list["Tag"]] = relationship()


class Tag(Base):
    __tablename__ = "keys_tag"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    item_id: Mapped[int] = mapped_column(ForeignKey(Item.id))


class CountrySchema(fr.IDSchema):
    name: str


class VendorSchema(fr.IDSchema):
    name: str
    country: CountrySchema


class ItemSchema(fr.IDSchema):
    name: str
    username: str
    created_at: datetime.datetime
    active: bool
    vendor: VendorSchema


def params_model(**settings: Any) -> type[pydantic.BaseModel]:
    return derive_schema_list_params(ItemSchema, Item, pagination=None, **settings)


def filter_keys(**settings: Any) -> set[str]:
    return set(params_model(**settings).model_fields) - {"sort"}


def sort_keys(**settings: Any) -> tuple[str, ...]:
    record = _record_of(params_model(**settings))
    assert record is not None
    return record.sort_keys


OWN_FIELDS = ("id", "name", "username", "created_at", "active")


def test_the_default_is_the_keys_of_the_own_fields():
    keys = filter_keys()

    assert {"name", "name__icontains", "created_at__gte", "active__isnull"} <= keys
    assert not [key for key in keys if "." in key]
    assert sort_keys() == OWN_FIELDS


def test_two_stars_give_every_key():
    keys = filter_keys(include_filters=("**",))

    assert {"vendor.name__icontains", "vendor.country.name"} <= keys
    assert filter_keys() < keys
    assert sort_keys(include_sort_keys=("**",)) == (
        *OWN_FIELDS,
        "vendor.id",
        "vendor.name",
        "vendor.country.id",
        "vendor.country.name",
    )


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        ("vendor.*", {"vendor.id", "vendor.name"}),
        (
            "vendor.**",
            {"vendor.id", "vendor.name", "vendor.country.id", "vendor.country.name"},
        ),
        ("*.*", {"vendor.id", "vendor.name"}),
        (
            "*.**",
            {"vendor.id", "vendor.name", "vendor.country.id", "vendor.country.name"},
        ),
        # every "." in a pattern must be in the key
        ("**.name", {"vendor.name", "vendor.country.name"}),
        ("**name", {"name", "username", "vendor.name", "vendor.country.name"}),
        ("name*", {"name"}),
        ("*e", {"name", "username", "active"}),
    ],
)
def test_patterns_follow_the_rules_of_a_file_glob(pattern, expected):
    # the sort keys have no suffixes, so they show the levels plainly
    assert set(sort_keys(include_sort_keys=(pattern,))) == expected


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        (
            "created_at__*",
            {
                "created_at__in",
                "created_at__ne",
                "created_at__isnull",
                "created_at__gte",
                "created_at__lte",
                "created_at__gt",
                "created_at__lt",
            },
        ),
        ("*__icontains", {"name__icontains", "username__icontains"}),
        (
            "**__icontains",
            {
                "name__icontains",
                "username__icontains",
                "vendor.name__icontains",
                "vendor.country.name__icontains",
            },
        ),
        # also matches a field such as "names"; the docs teach ("name", "name__*")
        ("name*", {key for key in filter_keys() if key.startswith("name")}),
    ],
)
def test_filter_patterns_match_the_suffixes(pattern, expected):
    assert filter_keys(include_filters=(pattern,)) == expected


def test_exclude_applies_after_include():
    keys = filter_keys(
        include_filters=("name__*",), exclude_filters=("name__icontains",)
    )

    assert "name__contains" in keys
    assert "name__icontains" not in keys
    assert "name" not in keys
    assert filter_keys(include_filters=("**",), exclude_filters=("*.**",)) == (
        filter_keys()
    )
    assert sort_keys(exclude_sort_keys=("username", "created_at")) == (
        "id",
        "name",
        "active",
    )


def test_empty_settings_give_no_keys():
    model = params_model(include_filters=(), include_sort_keys=())

    assert set(model.model_fields) == set()


def test_a_sort_key_never_goes_through_a_to_many_relationship():
    class TagSchema(fr.IDSchema):
        name: str

    class TaggedItemSchema(fr.IDSchema):
        name: str
        # a list, typed as one schema
        tags: TagSchema

    model = derive_schema_list_params(
        TaggedItemSchema, Item, include_filters=("**",), include_sort_keys=("**",)
    )

    assert "tags.name" in model.model_fields
    record = _record_of(model)
    assert record is not None
    assert record.sort_keys == ("id", "name")
    with pytest.raises(BadQueryParam, match="Unknown sort key: 'tags.name'"):
        apply_list_params(
            select(Item), QueryParams("sort=tags.name"), Item, TaggedItemSchema
        )


def test_sort_accepts_only_its_keys():
    model = params_model()

    assert model.model_validate({"sort": "-name,id"}).sort == "-name,id"
    with pytest.raises(pydantic.ValidationError) as exc_info:
        model.model_validate({"sort": "name,-vendor.name"})
    [error] = exc_info.value.errors()
    assert error["type"] == "unknown_sort_key"
    assert error["msg"] == "Unknown sort key 'vendor.name'"


def test_the_description_of_sort_lists_its_keys():
    description = params_model().model_fields["sort"].description

    assert description is not None
    assert "``-id,name``" in description
    assert "``id``, ``name``, ``username``, ``created_at``, ``active``." in description


def test_apply_list_params_checks_a_sort_that_skipped_validation():
    params = params_model().model_validate({})
    params = params.model_copy(update={"sort": "vendor.name"})

    with pytest.raises(BadQueryParam, match="Unknown sort key: 'vendor.name'"):
        apply_list_params(select(Item), params, Item, ItemSchema)


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        (
            {"include_filters": ("active__icontains",)},
            "include_filters: 'active__icontains' matches no filter key of "
            "ItemSchema. The filter keys of 'active' are: active, active__in, "
            "active__ne, active__isnull. Only text fields have __contains and "
            "__icontains.",
        ),
        (
            {"exclude_filters": ("id__contains",)},
            "exclude_filters: 'id__contains' matches no filter key of "
            "ItemSchema. The filter keys of 'id' are: id, id__in, id__ne, "
            "id__isnull, id__gte, id__lte, id__gt, id__lt. Only text fields "
            "have __contains and __icontains.",
        ),
        (
            {"include_filters": ("active__gte",)},
            "Only fields with an order, such as numbers, text and dates, have "
            "__gte, __lte, __gt and __lt.",
        ),
        (
            {"include_filters": ("nope",)},
            "include_filters: 'nope' matches no filter key of ItemSchema. The "
            "fields with filters are: id, name, username, created_at, active, "
            "vendor.id, vendor.name, vendor.country.id, vendor.country.name.",
        ),
        (
            {"include_filters": ("nope.*",)},
            "include_filters: 'nope.*' matches no filter key of ItemSchema.",
        ),
        (
            {"include_sort_keys": ("name__in",)},
            "include_sort_keys: 'name__in' matches no sort key of ItemSchema. "
            "A sort key has no suffix. The sort keys are: id, name, username, "
            "created_at, active, vendor.id, vendor.name, vendor.country.id, "
            "vendor.country.name.",
        ),
        (
            {"include_filters": "name"},
            "include_filters must be a tuple of keys, got the string 'name'. "
            "For one key, write ('name',) with a comma.",
        ),
        (
            {"exclude_sort_keys": ("na?e",)},
            "exclude_sort_keys: 'na?e' uses '?' or '[...]'.",
        ),
        (
            {"include_filters": ("[nN]ame",)},
            "include_filters: '[nN]ame' uses '?' or '[...]'.",
        ),
        (
            {"include_filters": (None,)},
            "include_filters must hold keys as strings, got None.",
        ),
    ],
)
def test_an_entry_that_matches_no_key_fails(settings, message):
    with pytest.raises(RestlyConfigurationError) as exc_info:
        params_model(**settings)

    assert message in str(exc_info.value)


def test_a_dot_in_a_pattern_is_always_a_dot():
    keys = filter_keys(include_filters=("**",), exclude_filters=("**.name",))

    assert "name" in keys
    assert "vendor.name" not in keys
    assert "vendor.country.name" not in keys
    assert "vendor.name__in" in keys


# --- views ---------------------------------------------------------------


@pytest.fixture
def app() -> FastAPI:
    async def prepare() -> None:
        engine = fr.db.get_async_engine()
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(prepare())
    return FastAPI()


def item_view(base: type = fr.AsyncRestView, **attrs: Any) -> type:
    return type(
        "ItemView",
        (base,),
        {"prefix": "/items", "model": Item, "schema": ItemSchema, **attrs},
    )


def query_names(client: RestlyTestClient) -> set[str]:
    operation = client.get("/openapi.json").json()["paths"]["/items"]["get"]
    return {parameter["name"] for parameter in operation["parameters"]}


def test_a_view_without_settings_has_no_dotted_keys(app):
    fr.include_view(app, item_view())
    client = RestlyTestClient(app)

    client.get("/items/", params={"name": "a", "sort": "-created_at"})
    detail = client.get(
        "/items/", params={"vendor.name": "a"}, assert_status_code=422
    ).json()["detail"]
    assert detail[0]["msg"] == "Unknown query parameter 'vendor.name'"
    detail = client.get(
        "/items/", params={"sort": "vendor.name"}, assert_status_code=422
    ).json()["detail"]
    assert detail[0]["msg"] == "Unknown sort key 'vendor.name'"
    assert "vendor.name" not in query_names(client)


def test_a_view_keeps_only_the_keys_it_includes(app):
    fr.include_view(
        app,
        item_view(
            include_filters=("name", "vendor.name__icontains"),
            include_sort_keys=("name", "vendor.name"),
        ),
    )
    client = RestlyTestClient(app)

    assert query_names(client) == {
        "page",
        "page_size",
        "sort",
        "name",
        "vendor.name__icontains",
    }
    client.get("/items/", params={"vendor.name__icontains": "a"})
    client.get("/items/", params={"sort": "-vendor.name,name"})
    client.get("/items/", params={"name__in": "a"}, assert_status_code=422)
    client.get("/items/", params={"sort": "id"}, assert_status_code=422)


def test_a_view_without_sort_keys_has_no_sort(app):
    fr.include_view(app, item_view(include_sort_keys=()))
    client = RestlyTestClient(app)

    assert "sort" not in query_names(client)
    detail = client.get(
        "/items/", params={"sort": "name"}, assert_status_code=422
    ).json()["detail"]
    assert detail[0]["msg"] == "Unknown query parameter 'sort'"


def test_each_setting_is_inherited_on_its_own(app):
    class AppView(fr.AsyncRestView):
        include_filters = ()
        include_sort_keys = ("id",)

    plain = fr.include_view(app, item_view(AppView))
    assert set(plain.schema_list_params.model_fields) == {"page", "page_size", "sort"}

    class ItemView(AppView):
        prefix = "/all-items"
        model = Item
        schema = ItemSchema
        include_filters = ("*",)

    fr.include_view(app, ItemView)
    fields = set(ItemView.schema_list_params.model_fields)
    assert {"name", "name__icontains"} <= fields
    record = _record_of(ItemView.schema_list_params)
    assert record is not None
    assert record.sort_keys == ("id",)


def test_an_entry_that_the_view_sets_must_match_a_key(app):
    view = item_view(include_sort_keys=("name", "updated_at"))

    with pytest.raises(RestlyConfigurationError) as exc_info:
        fr.include_view(app, view)

    assert str(exc_info.value).startswith(
        "ItemView.include_sort_keys: 'updated_at' matches no sort key of ItemSchema."
    )


def test_an_inherited_entry_may_match_no_key(app):
    # a base view serves many schemas
    class AppView(fr.AsyncRestView):
        include_sort_keys = ("id", "updated_at")
        exclude_filters = ("*__icontains", "secret*")

    view = fr.include_view(app, item_view(AppView))

    record = _record_of(view.schema_list_params)
    assert record is not None
    assert record.sort_keys == ("id",)
    assert "name__icontains" not in view.schema_list_params.model_fields


def test_a_string_fails_when_the_class_is_defined():
    with pytest.raises(RestlyConfigurationError, match=r"write \('name',\)"):

        class ItemView(fr.AsyncRestView):
            include_filters = "name"  # type: ignore[assignment]


def test_hand_written_list_params_cannot_follow_the_settings(app):
    class ItemParams(pydantic.BaseModel):
        name: str | None = None

    class AppView(fr.AsyncRestView):
        exclude_filters = ("*__icontains",)

    view = item_view(AppView, schema_list_params=ItemParams)

    with pytest.raises(RestlyConfigurationError) as exc_info:
        fr.include_view(app, view)

    assert str(exc_info.value).startswith(
        "ItemView sets exclude_filters (from AppView), but its "
        "schema_list_params is written by hand"
    )


def test_declared_list_params_must_have_the_settings_of_the_view(app):
    declared = derive_schema_list_params(ItemSchema, Item, include_filters=("name",))

    view = item_view(schema_list_params=declared)
    with pytest.raises(RestlyConfigurationError, match="other include_filters"):
        fr.include_view(app, view)

    same = item_view(schema_list_params=declared, include_filters=("name",))
    fr.include_view(app, same)
    assert same.schema_list_params is declared


@pytest.mark.parametrize("base", [fr.AsyncReactAdminView, fr.ReactAdminView])
def test_react_admin_views_reject_the_settings(app, base):
    class KeysMixin:
        exclude_sort_keys = ("name",)

    view = type(
        "ItemAdmin",
        (KeysMixin, base),
        {"prefix": "/items", "model": Item, "schema": ItemSchema},
    )

    with pytest.raises(RestlyConfigurationError) as exc_info:
        fr.include_view(app, view)

    assert str(exc_info.value).startswith(
        "ItemAdmin sets exclude_sort_keys (from KeysMixin), which a react-admin "
        "view does not use."
    )
