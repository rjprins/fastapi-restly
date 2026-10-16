"""The key settings: ``include_filters``, ``exclude_filters``,
``include_sort_keys`` and ``exclude_sort_keys``.

An entry is a key as the client sends it, or a pattern. ``*`` matches any
characters except ``.``, and ``**`` matches any characters. By default a list
has the keys of the resource's own fields, and no dotted keys.
"""

import asyncio
import datetime
import inspect
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
from fastapi_restly.views._base import _LIST_SETTING_DEFAULTS


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
            {"exclude_filters": iter(("name",))},
            "exclude_filters must be a tuple of keys, got an iterator, which "
            "can be read only once.",
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

    assert str(exc_info.value) == (
        "ItemView inherits exclude_filters = ('*__icontains',) from AppView, "
        "but its schema_list_params is written by hand, so Restly cannot "
        "apply the setting. Set exclude_filters = () on ItemView, or leave "
        "schema_list_params out to have it generated."
    )


def test_a_subclass_cannot_change_the_keys_of_hand_written_list_params(app):
    class ItemParams(pydantic.BaseModel):
        name: str | None = None

    base = item_view(schema_list_params=ItemParams)
    view = type("UserView", (base,), {"include_filters": ("username",)})

    with pytest.raises(RestlyConfigurationError) as exc_info:
        fr.include_view(app, view)

    assert str(exc_info.value) == (
        "UserView sets include_filters = ('username',), but its schema_list_params "
        "(from ItemView) is written by hand, so Restly cannot apply the "
        "setting. Remove the setting, or declare schema_list_params on "
        "UserView as well, created by fr.query.derive_schema_list_params() "
        "with include_filters=('username',)."
    )


def test_declared_list_params_must_have_the_settings_of_the_view(app):
    declared = derive_schema_list_params(ItemSchema, Item, include_filters=("name",))

    view = item_view(schema_list_params=declared)
    with pytest.raises(RestlyConfigurationError) as exc_info:
        fr.include_view(app, view)
    assert str(exc_info.value) == (
        "ItemView has include_filters = ('*',), but its schema_list_params "
        "was created with include_filters=('name',). A view's settings must "
        "be the ones that its list params were created with. Set "
        "include_filters = ('name',) on ItemView, or leave schema_list_params "
        "out to have it generated."
    )

    same = item_view(schema_list_params=declared, include_filters=("name",))
    fr.include_view(app, same)
    assert same.schema_list_params is declared


def test_declared_list_params_cannot_hide_an_inherited_setting(app):
    class AppView(fr.AsyncRestView):
        exclude_filters = ("*__icontains",)

    declared = derive_schema_list_params(ItemSchema, Item)
    view = item_view(AppView, schema_list_params=declared)

    with pytest.raises(RestlyConfigurationError) as exc_info:
        fr.include_view(app, view)

    assert str(exc_info.value) == (
        "ItemView inherits exclude_filters = ('*__icontains',) from AppView, "
        "but its schema_list_params was created with exclude_filters=(). A "
        "view's settings must be the ones that its list params were created "
        "with. Set exclude_filters = () on ItemView, or leave "
        "schema_list_params out to have it generated."
    )

    reset = item_view(AppView, schema_list_params=declared, exclude_filters=())
    fr.include_view(app, reset)
    assert reset.schema_list_params is declared


def test_a_subclass_with_other_settings_gets_new_list_params(app):
    declared = derive_schema_list_params(ItemSchema, Item, include_filters=("name",))
    base = item_view(schema_list_params=declared, include_filters=("name",))

    same = fr.include_view(app, type("SameView", (base,), {"prefix": "/same"}))
    user = fr.include_view(
        app,
        type(
            "UserView", (base,), {"prefix": "/users", "include_filters": ("username",)}
        ),
    )

    assert same.schema_list_params is declared
    assert "username" in user.schema_list_params.model_fields
    assert "name" not in user.schema_list_params.model_fields


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

    assert str(exc_info.value) == (
        "ItemAdmin inherits exclude_sort_keys = ('name',) from KeysMixin, which "
        "a react-admin view does not use. React-admin sends its filters and "
        "sort order in the 'filter' and 'sort' parameters, as JSON. Set "
        "exclude_sort_keys = () on ItemAdmin."
    )


@pytest.mark.parametrize("base", [fr.AsyncReactAdminView, fr.ReactAdminView])
def test_a_react_admin_view_sets_an_inherited_setting_back(app, base):
    class KeysMixin:
        include_filters = ("name",)

    attrs = {"prefix": "/items", "model": Item, "schema": ItemSchema}
    own = type("ItemAdmin", (base,), {**attrs, "include_filters": ("name",)})
    with pytest.raises(RestlyConfigurationError, match="ItemAdmin sets include_"):
        fr.include_view(app, own)

    reset = type("ItemAdmin", (KeysMixin, base), {**attrs, "include_filters": ["*"]})
    fr.include_view(app, reset)


def test_a_field_with_a_reserved_name_can_be_left_out():
    class PageBase(DeclarativeBase):
        pass

    class Book(PageBase):
        __tablename__ = "keys_book"
        id: Mapped[int] = mapped_column(primary_key=True)
        page: Mapped[int]

    class BookSchema(fr.IDSchema):
        page: int

    with pytest.raises(ValueError, match=r"exclude_filters = \('page',\)"):
        derive_schema_list_params(BookSchema, Book)

    model = derive_schema_list_params(BookSchema, Book, exclude_filters=("page",))
    assert model.model_fields["page"].annotation is int
    assert "page__gte" in model.model_fields
    record = _record_of(model)
    assert record is not None
    assert "page" in record.sort_keys


def test_a_view_renames_the_sort_parameter(app):
    fr.include_view(app, item_view(sort_query_param="order_by"))
    client = RestlyTestClient(app)

    names = query_names(client)
    assert "order_by" in names
    assert "sort" not in names
    client.get("/items/", params={"order_by": "-name"})
    detail = client.get(
        "/items/", params={"order_by": "nope"}, assert_status_code=422
    ).json()["detail"]
    assert (detail[0]["loc"], detail[0]["msg"]) == (
        ["query", "order_by"],
        "Unknown sort key 'nope'",
    )
    detail = client.get(
        "/items/", params={"sort": "name"}, assert_status_code=422
    ).json()["detail"]
    assert detail[0]["msg"] == "Unknown query parameter 'sort'"


def test_apply_list_params_reads_the_renamed_sort_parameter():
    model = derive_schema_list_params(ItemSchema, Item, sort_query_param="order_by")

    stmt = apply_list_params(
        select(Item), model.model_validate({"order_by": "-name"}), Item, ItemSchema
    )

    assert "ORDER BY keys_item.name DESC" in str(stmt)


def test_the_list_settings_have_one_default():
    # the view attributes and the keywords repeat the defaults for the docs
    keywords = inspect.signature(derive_schema_list_params).parameters
    for name, default in _LIST_SETTING_DEFAULTS.items():
        assert getattr(fr.AsyncRestView, name) == default
        assert keywords[name].default == default


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("", "ItemView.sort_query_param must be a non-empty string"),
        ("order__by", "'__' is reserved for filter operators"),
        ("_order", "cannot start with '_'"),
    ],
)
def test_a_bad_sort_parameter_fails_when_the_class_is_defined(value, message):
    with pytest.raises(RestlyConfigurationError, match=message):
        item_view(sort_query_param=value)


def test_the_sort_parameter_cannot_be_a_page_parameter(app):
    with pytest.raises(RestlyConfigurationError) as exc_info:
        fr.include_view(app, item_view(sort_query_param="page"))

    assert str(exc_info.value) == (
        "ItemView.sort_query_param cannot be 'page': it is a page parameter "
        "of the pagination. Choose another name for one of them."
    )


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("", "sort_query_param must be a non-empty string, got ''."),
        (
            "page",
            "sort_query_param cannot be 'page': it is a page parameter of the "
            "pagination. Choose another name for one of them.",
        ),
    ],
)
def test_derive_schema_list_params_names_the_sort_parameter_once(value, message):
    with pytest.raises(RestlyConfigurationError) as exc_info:
        derive_schema_list_params(ItemSchema, Item, sort_query_param=value)

    assert str(exc_info.value) == message


def test_declared_list_params_must_have_the_sort_parameter_of_the_view(app):
    declared = derive_schema_list_params(ItemSchema, Item)
    view = item_view(schema_list_params=declared, sort_query_param="order_by")

    with pytest.raises(RestlyConfigurationError) as exc_info:
        fr.include_view(app, view)

    assert str(exc_info.value) == (
        "ItemView sets sort_query_param = 'order_by', but its "
        "schema_list_params was created with sort_query_param='sort'. A view's "
        "settings must be the ones that its list params were created with. "
        "Create it with sort_query_param='order_by', or leave "
        "schema_list_params out to have it generated."
    )

    base = item_view(schema_list_params=declared)
    ordered = fr.include_view(
        app, type("OrderedView", (base,), {"prefix": "/o", "sort_query_param": "o"})
    )
    assert "o" in ordered.schema_list_params.model_fields


@pytest.mark.parametrize("base", [fr.AsyncReactAdminView, fr.ReactAdminView])
def test_react_admin_views_reject_the_sort_parameter(app, base):
    view = type(
        "ItemAdmin",
        (base,),
        {
            "prefix": "/items",
            "model": Item,
            "schema": ItemSchema,
            "sort_query_param": "order_by",
        },
    )

    with pytest.raises(RestlyConfigurationError, match="ItemAdmin sets sort_query"):
        fr.include_view(app, view)


class NarrowSchema(fr.IDSchema):
    name: str


def test_a_subclass_keeps_list_params_from_a_narrower_schema(app):
    declared = derive_schema_list_params(NarrowSchema, Item)
    base = item_view(schema_list_params=declared)
    fr.include_view(app, base)

    same = type("SameView", (base,), {"pagination": fr.NumberedPagination()})
    assert fr.include_view(app, same).schema_list_params is declared

    # new list params would get the keys of every field of ItemSchema
    paged = fr.NumberedPagination(default_page_size=10)
    view = type("PagedView", (base,), {"pagination": paged})
    with pytest.raises(RestlyConfigurationError) as exc_info:
        fr.include_view(app, view)

    assert str(exc_info.value) == (
        "PagedView.schema_list_params (from ItemView) was created for other "
        "pagination settings than the view's. Remove the setting, or declare "
        "schema_list_params on PagedView as well, created with its pagination."
    )


def test_a_subclass_keeps_list_params_with_settings_of_their_own(app):
    declared = derive_schema_list_params(ItemSchema, Item, include_filters=("name",))
    # a base that passes the setting only to the call, and is not registered
    base = item_view(schema_list_params=declared)
    paged = fr.NumberedPagination(default_page_size=10)
    view = type("PagedView", (base,), {"pagination": paged})

    with pytest.raises(RestlyConfigurationError) as exc_info:
        fr.include_view(app, view)

    assert str(exc_info.value).startswith(
        "PagedView has include_filters = ('*',), but its schema_list_params "
        "(from ItemView) was created with include_filters=('name',)."
    )


def test_a_page_parameter_cannot_be_the_sort_parameter_without_a_record():
    # hand-written list params and a QueryParams read "sort" for the sort
    pagination = fr.NumberedPagination(page_query_param="sort")

    with pytest.raises(RestlyConfigurationError) as exc_info:
        apply_list_params(
            select(Item),
            QueryParams("sort=2&page_size=2"),
            Item,
            ItemSchema,
            pagination=pagination,
        )

    assert str(exc_info.value) == (
        "A page parameter of the pagination is 'sort', which is also the sort "
        "parameter of these list params. Give the page parameter another name."
    )


def test_a_bad_setting_from_a_mixin_fails_when_the_view_registers(app):
    class KeysMixin:
        include_filters = None

    declared = derive_schema_list_params(ItemSchema, Item)
    view = item_view(
        type("Base", (KeysMixin, fr.AsyncRestView), {}), schema_list_params=declared
    )

    with pytest.raises(RestlyConfigurationError, match=r"\(from KeysMixin\)"):
        fr.include_view(app, view)
