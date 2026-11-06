"""The key settings: ``include_filters``, ``exclude_filters``,
``include_sort_keys`` and ``exclude_sort_keys``.

An entry is a key in the Python names of the fields, or a pattern. ``*``
matches any characters except ``.``, so it stays within one level. By default a list has
the keys of the resource's own fields, and no dotted keys.
"""

import asyncio
import datetime
import inspect
from typing import Annotated, Any

import pydantic
import pytest
from fastapi import FastAPI, Query
from sqlalchemy import JSON, ForeignKey, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from starlette.datastructures import QueryParams

import fastapi_restly as fr
from fastapi_restly.exc import BadQueryParam, RestlyConfigurationError
from fastapi_restly.query import apply_list_params, derive_list_params_schema
from fastapi_restly.query._impl import _record_of
from fastapi_restly.testing import RestlyTestClient
from fastapi_restly.views._list_params import _LIST_SETTING_DEFAULTS


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
    return derive_list_params_schema(ItemSchema, Item, pagination=None, **settings)


def filter_keys(**settings: Any) -> set[str]:
    return set(params_model(**settings).model_fields) - {"sort"}


def sort_keys(**settings: Any) -> tuple[str, ...]:
    record = _record_of(params_model(**settings))
    assert record is not None
    return record.sort_keys


OWN_FIELDS = ("id", "name", "username", "created_at", "active")
EVERY_LEVEL = ("*", "*.*", "*.*.*")


def test_the_default_is_the_keys_of_the_own_fields():
    keys = filter_keys()

    assert {"name", "name__icontains", "created_at__gte", "active__not_in"} <= keys
    assert not [key for key in keys if "." in key]
    assert sort_keys() == OWN_FIELDS


def test_one_star_per_level_gives_every_key():
    keys = filter_keys(include_filters=EVERY_LEVEL)

    assert {"vendor.name__icontains", "vendor.country.name"} <= keys
    assert filter_keys() < keys
    assert sort_keys(include_sort_keys=EVERY_LEVEL) == (
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
        ("vendor.*.*", {"vendor.country.id", "vendor.country.name"}),
        ("*.*", {"vendor.id", "vendor.name"}),
        # every "." in a pattern must be in the key
        ("*.*.name", {"vendor.country.name"}),
        ("*name", {"name", "username"}),
        ("name*", {"name"}),
        ("*e", {"name", "username", "active"}),
    ],
)
def test_a_star_stays_within_one_level(pattern, expected):
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
                "created_at__not_in",
                "created_at__gte",
                "created_at__lte",
                "created_at__gt",
                "created_at__lt",
            },
        ),
        ("*__icontains", {"name__icontains", "username__icontains"}),
        ("*.*__icontains", {"vendor.name__icontains"}),
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

    assert "name__in" in keys
    assert "name__icontains" not in keys
    assert "name" not in keys
    assert filter_keys(
        include_filters=EVERY_LEVEL, exclude_filters=("*.*", "*.*.*")
    ) == (filter_keys())
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

    model = derive_list_params_schema(
        TaggedItemSchema, Item, include_filters=("*", "*.*")
    )

    assert "tags.name" in model.model_fields
    with pytest.raises(RestlyConfigurationError, match="'\\*.\\*' matches no sort key"):
        derive_list_params_schema(TaggedItemSchema, Item, include_sort_keys=("*.*",))
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


def test_sort_rejects_a_key_that_appears_twice():
    """``name,-name`` sorts twice on one column, and the second term changes
    nothing. The list params reject it, and so does ``apply_list_params``,
    for a sort that skipped them."""
    model = params_model()

    with pytest.raises(pydantic.ValidationError) as exc_info:
        model.model_validate({"sort": "name,-created_at,-name"})
    [error] = exc_info.value.errors()
    assert error["type"] == "repeated_sort_key"
    assert error["msg"] == "Sort key 'name' appears more than once"
    with pytest.raises(BadQueryParam, match="Sort key 'id' appears more than once"):
        apply_list_params(select(Item), QueryParams("sort=id,id"), Item, ItemSchema)


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
            "active__ne, active__not_in. Only a field on a text column has "
            "__icontains, and not an enum, a Literal or a bool. On a TypeDecorator "
            "column, the field must be a str.",
        ),
        (
            {"exclude_filters": ("id__icontains",)},
            "exclude_filters: 'id__icontains' matches no filter key of "
            "ItemSchema. The filter keys of 'id' are: id, id__in, id__ne, "
            "id__not_in, id__gte, id__lte, id__gt, id__lt. Only a field on a text "
            "column has __icontains, and not an enum, a Literal or a bool. On a "
            "TypeDecorator column, the field must be a str.",
        ),
        (
            {"include_filters": ("active__gte",)},
            "Only fields with an order, such as numbers, dates and int enums, "
            "have __gte, __lte, __gt and __lt.",
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
            {"include_filters": ("vendor.**",)},
            "include_filters: 'vendor.**' uses '**'. A key pattern has one "
            "wildcard, '*', for any characters except '.'. Write one entry "
            "for each level, such as ('vendor.*', 'vendor.*.*').",
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
    keys = filter_keys(
        include_filters=EVERY_LEVEL, exclude_filters=("*.name", "*.*.name")
    )

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
    detail = client.get(
        "/items/", params={"sort": "name,-name"}, assert_status_code=422
    ).json()["detail"]
    assert detail[0]["msg"] == "Sort key 'name' appears more than once"


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
    assert set(plain.list_params_schema.model_fields) == {"page", "page_size", "sort"}

    class ItemView(AppView):
        prefix = "/all-items"
        model = Item
        schema = ItemSchema
        include_filters = ("*",)

    fr.include_view(app, ItemView)
    fields = set(ItemView.list_params_schema.model_fields)
    assert {"name", "name__icontains"} <= fields
    record = _record_of(ItemView.list_params_schema)
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

    record = _record_of(view.list_params_schema)
    assert record is not None
    assert record.sort_keys == ("id",)
    assert "name__icontains" not in view.list_params_schema.model_fields


def test_a_string_fails_when_the_class_is_defined():
    with pytest.raises(RestlyConfigurationError, match=r"write \('name',\)"):

        class ItemView(fr.AsyncRestView):
            include_filters = "name"  # type: ignore[assignment]


def test_hand_written_list_params_cannot_follow_the_settings(app):
    class ItemParams(pydantic.BaseModel):
        name: str | None = None

    class AppView(fr.AsyncRestView):
        exclude_filters = ("*__icontains",)

    view = item_view(AppView, list_params_schema=ItemParams)

    with pytest.raises(RestlyConfigurationError) as exc_info:
        fr.include_view(app, view)

    assert str(exc_info.value) == (
        "ItemView inherits exclude_filters = ('*__icontains',) from AppView, "
        "but its list_params_schema is written by hand, so Restly cannot "
        "apply the setting. Set exclude_filters = () on ItemView, or leave "
        "list_params_schema out to have it generated."
    )


def test_a_subclass_cannot_change_the_keys_of_hand_written_list_params(app):
    class ItemParams(pydantic.BaseModel):
        name: str | None = None

    base = item_view(list_params_schema=ItemParams)
    view = type("UserView", (base,), {"include_filters": ("username",)})

    with pytest.raises(RestlyConfigurationError) as exc_info:
        fr.include_view(app, view)

    assert str(exc_info.value) == (
        "UserView sets include_filters = ('username',), but its list_params_schema "
        "(from ItemView) is written by hand, so Restly cannot apply the "
        "setting. Remove the setting, or declare list_params_schema on "
        "UserView as well, created by fr.query.derive_list_params_schema() "
        "with include_filters=('username',)."
    )


def test_declared_list_params_must_have_the_settings_of_the_view(app):
    declared = derive_list_params_schema(ItemSchema, Item, include_filters=("name",))

    view = item_view(list_params_schema=declared)
    with pytest.raises(RestlyConfigurationError) as exc_info:
        fr.include_view(app, view)
    assert str(exc_info.value) == (
        "ItemView has include_filters = ('*',), but its list_params_schema "
        "was created with include_filters=('name',). A view's settings must "
        "be the ones that its list params were created with. Set "
        "include_filters = ('name',) on ItemView, or leave list_params_schema "
        "out to have it generated."
    )

    same = item_view(list_params_schema=declared, include_filters=("name",))
    fr.include_view(app, same)
    assert same.list_params_schema is declared


def test_declared_list_params_cannot_hide_an_inherited_setting(app):
    class AppView(fr.AsyncRestView):
        exclude_filters = ("*__icontains",)

    declared = derive_list_params_schema(ItemSchema, Item)
    view = item_view(AppView, list_params_schema=declared)

    with pytest.raises(RestlyConfigurationError) as exc_info:
        fr.include_view(app, view)

    assert str(exc_info.value) == (
        "ItemView inherits exclude_filters = ('*__icontains',) from AppView, "
        "but its list_params_schema was created with exclude_filters=(). A "
        "view's settings must be the ones that its list params were created "
        "with. Set exclude_filters = () on ItemView, or leave "
        "list_params_schema out to have it generated."
    )

    reset = item_view(AppView, list_params_schema=declared, exclude_filters=())
    fr.include_view(app, reset)
    assert reset.list_params_schema is declared


def test_a_subclass_with_other_settings_gets_new_list_params(app):
    declared = derive_list_params_schema(ItemSchema, Item, include_filters=("name",))
    base = item_view(list_params_schema=declared, include_filters=("name",))

    same = fr.include_view(app, type("SameView", (base,), {"prefix": "/same"}))
    user = fr.include_view(
        app,
        type(
            "UserView", (base,), {"prefix": "/users", "include_filters": ("username",)}
        ),
    )

    assert same.list_params_schema is declared
    assert "username" in user.list_params_schema.model_fields
    assert "name" not in user.list_params_schema.model_fields


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

    with pytest.raises(
        RestlyConfigurationError, match=r"exclude_filters = \('page',\)"
    ):
        derive_list_params_schema(BookSchema, Book)

    model = derive_list_params_schema(BookSchema, Book, exclude_filters=("page",))
    # the page parameter, and none of the keys of the field
    assert model.model_fields["page"].annotation is int
    assert "page__gte" not in model.model_fields
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
    model = derive_list_params_schema(ItemSchema, Item, sort_query_param="order_by")

    stmt = apply_list_params(
        select(Item), model.model_validate({"order_by": "-name"}), Item, ItemSchema
    )

    assert "ORDER BY keys_item.name DESC" in str(stmt)


def test_the_list_settings_have_one_default():
    # the view attributes and the keywords repeat the defaults for the docs
    keywords = inspect.signature(derive_list_params_schema).parameters
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
def test_derive_list_params_schema_names_the_sort_parameter_once(value, message):
    with pytest.raises(RestlyConfigurationError) as exc_info:
        derive_list_params_schema(ItemSchema, Item, sort_query_param=value)

    assert str(exc_info.value) == message


def test_declared_list_params_must_have_the_sort_parameter_of_the_view(app):
    declared = derive_list_params_schema(ItemSchema, Item)
    view = item_view(list_params_schema=declared, sort_query_param="order_by")

    with pytest.raises(RestlyConfigurationError) as exc_info:
        fr.include_view(app, view)

    assert str(exc_info.value) == (
        "ItemView sets sort_query_param = 'order_by', but its "
        "list_params_schema was created with sort_query_param='sort'. A view's "
        "settings must be the ones that its list params were created with. "
        "Create it with sort_query_param='order_by', or leave "
        "list_params_schema out to have it generated."
    )

    base = item_view(list_params_schema=declared)
    ordered = fr.include_view(
        app, type("OrderedView", (base,), {"prefix": "/o", "sort_query_param": "o"})
    )
    assert "o" in ordered.list_params_schema.model_fields


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
    declared = derive_list_params_schema(NarrowSchema, Item)
    base = item_view(list_params_schema=declared)
    fr.include_view(app, base)

    same = type("SameView", (base,), {"pagination": fr.NumberedPagination()})
    assert fr.include_view(app, same).list_params_schema is declared

    # new list params would get the keys of every field of ItemSchema
    paged = fr.NumberedPagination(default_page_size=10)
    view = type("PagedView", (base,), {"pagination": paged})
    with pytest.raises(RestlyConfigurationError) as exc_info:
        fr.include_view(app, view)

    assert str(exc_info.value) == (
        "PagedView.list_params_schema (from ItemView) was created for other "
        "pagination settings than the view's. Remove the setting, or declare "
        "list_params_schema on PagedView as well, created with its pagination."
    )


def test_a_subclass_keeps_list_params_with_settings_of_their_own(app):
    declared = derive_list_params_schema(ItemSchema, Item, include_filters=("name",))
    # a base that passes the setting only to the call, and is not registered
    base = item_view(list_params_schema=declared)
    paged = fr.NumberedPagination(default_page_size=10)
    view = type("PagedView", (base,), {"pagination": paged})

    with pytest.raises(RestlyConfigurationError) as exc_info:
        fr.include_view(app, view)

    assert str(exc_info.value).startswith(
        "PagedView has include_filters = ('*',), but its list_params_schema "
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

    declared = derive_list_params_schema(ItemSchema, Item)
    view = item_view(
        type("Base", (KeysMixin, fr.AsyncRestView), {}), list_params_schema=declared
    )

    with pytest.raises(RestlyConfigurationError, match=r"\(from KeysMixin\)"):
        fr.include_view(app, view)


# ---------------------------------------------------------------------------
# exclude_filters: a bare name is the whole field
# ---------------------------------------------------------------------------


def test_a_bare_name_in_exclude_filters_removes_the_whole_field():
    keys = filter_keys(exclude_filters=("name",))

    assert not [key for key in keys if key == "name" or key.startswith("name__")]
    # a field whose name only starts the same stays
    assert "username" in keys


def test_a_bare_name_in_include_filters_is_one_key():
    assert filter_keys(include_filters=("name",)) == {"name"}


def test_a_key_with_a_suffix_in_exclude_filters_is_one_key():
    keys = filter_keys(exclude_filters=("name__in",))

    assert "name__in" not in keys
    assert {"name", "name__ne"} <= keys


def test_a_bare_dotted_name_in_exclude_filters_removes_the_whole_field():
    keys = filter_keys(include_filters=("*", "*.*"), exclude_filters=("vendor.name",))

    assert "vendor.id" in keys
    assert not [key for key in keys if key.startswith("vendor.name")]


def test_only_the_plain_key_can_go_with_include_filters():
    """``exclude_filters`` has no entry for only the plain key of a field, so
    ``include_filters`` names the others."""
    keys = filter_keys(include_filters=("*__*", "id", "username"))

    assert "name" not in keys
    assert {"name__in", "name__icontains", "id", "username"} <= keys


def test_a_bare_name_removes_a_field_that_has_only_isnull():
    class JsonBase(DeclarativeBase):
        pass

    class Doc(JsonBase):
        __tablename__ = "keys_doc"
        id: Mapped[int] = mapped_column(primary_key=True)
        meta: Mapped[dict | None] = mapped_column(JSON)

    class DocSchema(fr.IDSchema):
        meta: dict | None = None

    assert "meta__isnull" in derive_list_params_schema(DocSchema, Doc).model_fields
    fields = derive_list_params_schema(
        DocSchema, Doc, exclude_filters=("meta",)
    ).model_fields
    assert "meta__isnull" not in fields


# ---------------------------------------------------------------------------
# __isnull: only where NULL can be
# ---------------------------------------------------------------------------


def test_only_a_column_that_can_be_null_has_isnull():
    class NullBase(DeclarativeBase):
        pass

    class Maker(NullBase):
        __tablename__ = "keys_maker"
        id: Mapped[int] = mapped_column(primary_key=True)
        name: Mapped[str]

    class Thing(NullBase):
        __tablename__ = "keys_thing"
        id: Mapped[int] = mapped_column(primary_key=True)
        name: Mapped[str]
        note: Mapped[str | None]
        maker_id: Mapped[int | None] = mapped_column(ForeignKey(Maker.id))
        maker: Mapped[Maker | None] = relationship()

    class MakerSchema(fr.IDSchema):
        name: str

    class ThingSchema(fr.IDSchema):
        name: str
        note: str | None = None
        maker: MakerSchema | None = None

    fields = derive_list_params_schema(
        ThingSchema, Thing, include_filters=("*", "*.*")
    ).model_fields

    assert "note__isnull" in fields
    assert "name__isnull" not in fields
    assert "id__isnull" not in fields
    # the column of the related row cannot be NULL, but the row can be missing
    assert "maker.name__isnull" in fields


def test_a_json_column_that_cannot_be_null_has_no_keys():
    class JsonBase(DeclarativeBase):
        pass

    class Doc(JsonBase):
        __tablename__ = "keys_required_doc"
        id: Mapped[int] = mapped_column(primary_key=True)
        meta: Mapped[dict] = mapped_column(JSON)

    class DocSchema(fr.IDSchema):
        meta: dict

    assert not [
        key
        for key in derive_list_params_schema(DocSchema, Doc).model_fields
        if key.startswith("meta")
    ]
    with pytest.raises(RestlyConfigurationError, match="'meta' has no filter keys"):
        derive_list_params_schema(DocSchema, Doc, include_filters=("meta__isnull",))


# ---------------------------------------------------------------------------
# Range keys over text: only for a field of type date
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("annotation", "has_range_keys"),
    [
        (datetime.date, True),
        (datetime.date | None, True),
        (datetime.datetime, False),
        (int, False),
        (float, False),
        (str, False),
        (Any, False),
    ],
)
def test_a_text_column_has_range_keys_only_for_a_date(annotation, has_range_keys):
    class TextBase(DeclarativeBase):
        pass

    class Event(TextBase):
        __tablename__ = "keys_event"
        id: Mapped[int] = mapped_column(primary_key=True)
        when: Mapped[str]

    class EventSchema(fr.IDSchema):
        when: annotation

    fields = derive_list_params_schema(EventSchema, Event).model_fields

    assert ("when__gte" in fields) is has_range_keys
    assert "when__icontains" in fields


def test_a_number_column_keeps_its_range_keys_for_a_text_field():
    class NumberBase(DeclarativeBase):
        pass

    class Score(NumberBase):
        __tablename__ = "keys_score"
        id: Mapped[int] = mapped_column(primary_key=True)
        points: Mapped[int]

    class ScoreSchema(fr.IDSchema):
        points: int

    assert "points__gte" in derive_list_params_schema(ScoreSchema, Score).model_fields


# ---------------------------------------------------------------------------
# The name of a key is the name that a response shows
# ---------------------------------------------------------------------------


def test_a_key_has_the_name_that_a_response_shows():
    class NameBase(DeclarativeBase):
        pass

    class Person(NameBase):
        __tablename__ = "keys_person"
        id: Mapped[int] = mapped_column(primary_key=True)
        full_name: Mapped[str]
        nick_name: Mapped[str]
        age: Mapped[int]

    class PersonSchema(fr.IDSchema):
        full_name: str = pydantic.Field(serialization_alias="fullName")
        nick_name: str = pydantic.Field(alias="nickIn", serialization_alias="nickOut")
        age: int = pydantic.Field(alias="years")

    model = derive_list_params_schema(PersonSchema, Person)
    fields = model.model_fields
    record = _record_of(model)
    assert record is not None

    for name in ("fullName", "nickOut", "years"):
        assert name in fields
        assert f"{name}__in" in fields
        assert name in record.sort_keys
    for name in ("full_name", "nick_name", "nickIn", "age"):
        assert name not in fields
        assert name not in record.sort_keys


def test_the_key_of_a_serialization_alias_finds_the_row(app):
    class NameBase(DeclarativeBase):
        pass

    class Person(NameBase):
        __tablename__ = "keys_found_person"
        id: Mapped[int] = mapped_column(primary_key=True)
        full_name: Mapped[str]

    class PersonSchema(fr.IDSchema):
        full_name: str = pydantic.Field(serialization_alias="fullName")

    @fr.include_view(app)
    class PersonView(fr.AsyncRestView):
        prefix = "/people"
        model = Person
        schema = PersonSchema

    client = RestlyTestClient(app)
    asyncio.run(fr.db.async_create_all(NameBase))
    client.post("/people", json={"full_name": "Ann"})
    client.post("/people", json={"full_name": "Bob"})

    rows = client.get("/people", params={"fullName": "Bob", "sort": "-fullName"})
    assert [row["fullName"] for row in rows.json()["data"]] == ["Bob"]


# ---------------------------------------------------------------------------
# The settings take Python names
# ---------------------------------------------------------------------------


class AliasBase(DeclarativeBase):
    pass


class Maker(AliasBase):
    __tablename__ = "keys_alias_maker"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime.datetime]


class Product(AliasBase):
    __tablename__ = "keys_alias_product"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime.datetime]
    maker_id: Mapped[int] = mapped_column(ForeignKey(Maker.id))
    maker: Mapped[Maker] = relationship()


class MakerSchema(fr.IDSchema):
    created_at: datetime.datetime = pydantic.Field(alias="createdAt")


class ProductSchema(fr.IDSchema):
    created_at: datetime.datetime = pydantic.Field(alias="createdAt")
    maker: MakerSchema = pydantic.Field(alias="supplier")


def product_params(**settings: Any) -> type[pydantic.BaseModel]:
    return derive_list_params_schema(
        ProductSchema, Product, pagination=None, **settings
    )


def test_a_filter_setting_takes_python_names():
    model = product_params(include_filters=("created_at", "maker.created_at__gte"))

    assert set(model.model_fields) - {"sort"} == {
        "createdAt",
        "supplier.createdAt__gte",
    }


def test_a_pattern_takes_python_names():
    model = product_params(include_filters=("maker.*",))

    keys = set(model.model_fields) - {"sort"}
    assert "supplier.createdAt__gte" in keys
    assert all(key.startswith("supplier.") for key in keys)


def test_exclude_filters_takes_python_names():
    model = product_params(exclude_filters=("created_at",))

    assert not any(key.startswith("createdAt") for key in model.model_fields)
    assert "id__in" in model.model_fields


def test_the_sort_settings_take_python_names():
    model = product_params(
        include_sort_keys=("*", "maker.*"),
        exclude_sort_keys=("id",),
        default_sort="-created_at,maker.created_at",
    )
    record = _record_of(model)
    assert record is not None

    assert record.sort_keys == ("createdAt", "supplier.id", "supplier.createdAt")
    # as a client writes it, so OpenAPI shows it
    assert record.default_sort == "-createdAt,supplier.createdAt"
    assert model.model_fields["sort"].default == "-createdAt,supplier.createdAt"


def test_a_default_sort_from_python_names_sorts_the_list(app):
    @fr.include_view(app)
    class ProductView(fr.AsyncRestView):
        prefix = "/products"
        model = Product
        schema = ProductSchema
        default_sort = "-created_at"

    client = RestlyTestClient(app)
    asyncio.run(fr.db.async_create_all(AliasBase))

    async def add_rows() -> None:
        day = datetime.datetime(2026, 1, 1)
        async with fr.db.get_async_engine().begin() as connection:
            await connection.execute(
                Maker.__table__.insert(), [{"id": 1, "created_at": day}]
            )
            await connection.execute(
                Product.__table__.insert(),
                [
                    {"id": 1, "created_at": day, "maker_id": 1},
                    {"id": 2, "created_at": day.replace(day=2), "maker_id": 1},
                ],
            )

    asyncio.run(add_rows())

    rows = client.get("/products").json()["data"]
    assert [row["id"] for row in rows] == [2, 1]
    operation = client.get("/openapi.json").json()["paths"]["/products"]["get"]
    (sort,) = [p for p in operation["parameters"] if p["name"] == "sort"]
    assert sort["schema"]["default"] == "-createdAt"


@pytest.mark.parametrize(
    "settings, message",
    [
        (
            {"include_filters": ("createdAt",)},
            "include_filters: 'createdAt' is the name in the URL. The key "
            "settings take the Python name of a field, so write 'created_at'.",
        ),
        (
            {"exclude_filters": ("createdAt",)},
            "exclude_filters: 'createdAt' is the name in the URL. The key "
            "settings take the Python name of a field, so write 'created_at'.",
        ),
        (
            {"include_filters": ("supplier.createdAt__gte",)},
            "include_filters: 'supplier.createdAt__gte' is the name in the URL. "
            "The key settings take the Python name of a field, so write "
            "'maker.created_at__gte'.",
        ),
        (
            {"include_filters": ("supplier.*",)},
            "include_filters: 'supplier.*' matches only names in the URL, such "
            "as 'supplier.id'. The key settings take the Python names of the "
            "fields, such as 'maker.id'.",
        ),
        (
            {"include_sort_keys": ("createdAt",)},
            "include_sort_keys: 'createdAt' is the name in the URL. The key "
            "settings take the Python name of a field, so write 'created_at'.",
        ),
        (
            {"default_sort": "-createdAt"},
            "default_sort: 'createdAt' is the name in the URL. A key is the "
            "Python name of a field, so write 'created_at'.",
        ),
    ],
)
def test_a_name_in_the_url_fails(settings, message):
    with pytest.raises(RestlyConfigurationError) as exc_info:
        product_params(**settings)

    assert str(exc_info.value) == message


def test_an_inherited_name_in_the_url_fails(app):
    class AppView(fr.AsyncRestView):
        exclude_filters = ("createdAt",)

    class ProductView(AppView):
        prefix = "/products"
        model = Product
        schema = ProductSchema

    with pytest.raises(RestlyConfigurationError) as exc_info:
        fr.include_view(app, ProductView)

    assert str(exc_info.value) == (
        "ProductView.exclude_filters (from AppView): 'createdAt' is the name in "
        "the URL. The key settings take the Python name of a field, so write "
        "'created_at'."
    )


def test_an_inherited_python_name_may_match_no_key(app):
    class AppView(fr.AsyncRestView):
        exclude_filters = ("username",)

    class ProductView(AppView):
        prefix = "/products"
        model = Product
        schema = ProductSchema

    fr.include_view(app, ProductView)


def test_a_message_shows_both_names():
    with pytest.raises(RestlyConfigurationError) as exc_info:
        product_params(include_filters=("created_at__icontains",))

    assert str(exc_info.value) == (
        "include_filters: 'created_at__icontains' matches no filter key of "
        "ProductSchema. The filter keys of 'created_at' (createdAt in the URL) "
        "are: created_at, created_at__in, created_at__ne, created_at__not_in, "
        "created_at__gte, created_at__lte, created_at__gt, created_at__lt. "
        "Only a field on a text column has __icontains, and not an enum, a "
        "Literal or a bool. On a TypeDecorator column, the field must be a str."
    )

    with pytest.raises(RestlyConfigurationError) as exc_info:
        product_params(include_sort_keys=("name",))

    assert str(exc_info.value) == (
        "include_sort_keys: 'name' matches no sort key of ProductSchema. The "
        "sort keys are: id, created_at (createdAt in the URL), maker.id "
        "(supplier.id in the URL), maker.created_at (supplier.createdAt in the "
        "URL)."
    )


def test_the_hint_for_a_reserved_key_gives_the_python_name():
    class PageBase(DeclarativeBase):
        pass

    class Book(PageBase):
        __tablename__ = "keys_alias_book"
        id: Mapped[int] = mapped_column(primary_key=True)
        page_number: Mapped[int]

    class BookSchema(fr.IDSchema):
        page_number: int = pydantic.Field(alias="page")

    with pytest.raises(
        RestlyConfigurationError, match=r"exclude_filters = \('page_number',\)"
    ):
        derive_list_params_schema(BookSchema, Book)

    model = derive_list_params_schema(
        BookSchema, Book, exclude_filters=("page_number",)
    )
    assert model.model_fields["page"].annotation is int


def test_the_hint_for_an_own_key_gives_the_python_name(app):
    class ProductView(fr.AsyncRestView):
        prefix = "/products"
        model = Product
        schema = ProductSchema

        @fr.list_filter("createdAt")
        def filter_created(self, value: datetime.datetime) -> None:
            return None

    with pytest.raises(RestlyConfigurationError) as exc_info:
        fr.include_view(app, ProductView)

    assert "exclude_filters = ('created_at',)" in str(exc_info.value)
    assert "removes created_at__in and" in str(exc_info.value)


# ---------------------------------------------------------------------------
# The generated model forbids a key that it does not have
# ---------------------------------------------------------------------------


def test_the_generated_model_works_on_a_route_of_your_own(app):
    params_class = params_model()

    @app.get("/own")
    async def own(params: Annotated[params_class, Query()]) -> dict[str, Any]:
        return {"names": params.name__in, "name": params.name}

    client = RestlyTestClient(app)

    response = client.get("/own", params={"name": "a", "name__in": "b,c"})
    assert response.json() == {"names": ["b", "c"], "name": "a"}
    response = client.get("/own?name__in=b&name__in=c")
    assert response.json()["names"] == ["b", "c"]
    client.get("/own", params={"nope": "1"}, assert_status_code=422)


def test_a_view_that_ignores_unknown_params_still_ignores_them(app):
    fr.include_view(app, item_view(unknown_query_params="ignore"))
    client = RestlyTestClient(app)
    asyncio.run(fr.db.async_create_all(Base))

    assert client.get("/items", params={"nope": "1"}).status_code == 200


class Gadget(AliasBase):
    __tablename__ = "keys_alias_gadget"
    id: Mapped[int] = mapped_column(primary_key=True)
    cost__internal: Mapped[int | None]


class GadgetSchema(fr.IDSchema):
    cost__internal: int | None = pydantic.Field(None, alias="costInternal")


def test_a_python_name_with_two_underscores_excludes_the_whole_field():
    model = derive_list_params_schema(
        GadgetSchema, Gadget, pagination=None, exclude_filters=("cost__internal",)
    )

    assert not [key for key in model.model_fields if key.startswith("cost")]
