"""Names that were renamed or removed before 1.0 fail at class definition.

Restly no longer reads the old names, so a view that still uses one would
lose its setting or override without any error, or fail only on a request.
The error names the new name, or what to use instead.
"""

import re
import sys

import pytest
from pydantic import BaseModel
from sqlalchemy.orm import Mapped

import fastapi_restly as fr
from fastapi_restly.exc import RestlyConfigurationError

from ._models import SharedBase
from .conftest import create_tables

VIEW_BASES = [fr.AsyncRestView, fr.RestView, fr.AsyncReactAdminView, fr.ReactAdminView]


@pytest.mark.parametrize("base", VIEW_BASES)
@pytest.mark.parametrize(
    "old, new",
    [
        ("listing_param_schema", "list_params_schema"),
        ("schema_list_params", "list_params_schema"),
        ("schema_response", "response_schema"),
        ("schema_create", "create_schema"),
        ("schema_update", "update_schema"),
    ],
)
def test_old_schema_setting_names_the_new_one(base, old, new):
    with pytest.raises(RestlyConfigurationError) as excinfo:
        type("OldView", (base,), {old: object})

    assert str(excinfo.value) == (
        f"OldView sets {old}, which was renamed to {new}. Rename the setting."
    )


def test_a_subclass_of_a_base_with_an_old_setting_names_the_base():
    class OldMixin:
        schema_response = object

    with pytest.raises(RestlyConfigurationError) as excinfo:

        class OldView(OldMixin, fr.AsyncRestView):
            pass

    assert "OldView sets schema_response (from OldMixin)" in str(excinfo.value)


@pytest.mark.parametrize(
    "module, name",
    [
        (fr.views, "ViewID"),
        (fr.views, "ViewResponse"),
        (fr.views, "ViewListResponse"),
        (fr.views, "ViewCreate"),
        (fr.views, "ViewUpdate"),
        (fr.schemas, "derive_schema_response"),
        (fr.schemas, "derive_schema_list_response"),
        (fr.schemas, "derive_schema_create"),
        (fr.schemas, "derive_schema_update"),
        (fr.query, "derive_schema_list_params"),
    ],
)
def test_an_old_marker_or_function_name_is_gone(module, name):
    assert not hasattr(module, name)


@pytest.mark.parametrize("base", VIEW_BASES)
@pytest.mark.parametrize(
    "name, instead",
    [
        ("route_options", "under the name of the route method"),
        ("extra_query_params", 'set unknown_query_params = "ignore".'),
    ],
)
def test_removed_setting_says_what_to_use_instead(base, name, instead):
    with pytest.raises(RestlyConfigurationError) as excinfo:
        type("OldView", (base,), {name: {}})

    message = str(excinfo.value)
    assert message.startswith(f"OldView sets {name}, which was removed. ")
    assert instead in message


def test_removed_setting_on_a_plain_view_fails_at_registration(client):
    class OldView(fr.View):
        prefix = "/old"
        route_options = {"ping": {"summary": "Ping"}}

        @fr.get("/ping")
        async def ping(self):
            return "pong"

    with pytest.raises(
        RestlyConfigurationError, match="OldView sets route_options, which was removed"
    ):
        fr.include_view(client.app, OldView)


@pytest.mark.parametrize("base", VIEW_BASES)
@pytest.mark.parametrize(
    "old, new, hint",
    [
        (
            "to_listing_response",
            "to_list_response",
            "It takes only the list result: the list params are in "
            "list_result.list_params.",
        ),
        (
            "to_react_admin_listing_response",
            "to_list_response",
            "The list params are in list_result.list_params.",
        ),
        ("to_response_schema", "to_single_response", ""),
        (
            "apply_query_params",
            "apply_list_params",
            "Its arguments are (query, list_params).",
        ),
    ],
)
def test_old_method_override_names_the_new_one(base, old, new, hint):
    def method(self, *args):
        raise AssertionError("never called")

    with pytest.raises(RestlyConfigurationError) as excinfo:
        type("OldView", (base,), {old: method})

    message = str(excinfo.value)
    assert message.startswith(f"OldView defines {old}, which was renamed to {new}.")
    assert hint in message
    assert message.endswith("Rename the method.")


def test_old_method_on_a_mixin_names_the_mixin():
    class SerializeMixin:
        def to_response_schema(self, obj):
            return obj

    with pytest.raises(
        RestlyConfigurationError,
        match=re.escape(
            "defines to_response_schema (from SerializeMixin), which was "
            "renamed to to_single_response"
        ),
    ):

        class OldView(SerializeMixin, fr.AsyncRestView):
            pass


def test_old_route_parameter_names_the_new_one():
    with pytest.raises(RestlyConfigurationError) as excinfo:

        class OldView(fr.AsyncRestView):
            @fr.get("/trash")
            async def trash(self, query_params):
                return await self.handle_get_many(query_params)

    assert str(excinfo.value) == (
        "OldView.trash declares a query_params parameter, which was renamed to "
        "list_params. Rename the parameter, so the route still takes the "
        "filter, sort and page parameters."
    )


def test_old_route_parameter_fails_on_a_sync_view():
    with pytest.raises(RestlyConfigurationError, match="renamed to list_params"):

        class OldView(fr.RestView):
            @fr.get("/trash")
            def trash(self, query_params, verbose: bool = False):
                return self.handle_get_many(query_params)


def test_old_route_parameter_on_a_mixin_names_the_mixin():
    class TrashMixin:
        @fr.get("/trash")
        async def trash(self, query_params):
            return await self.handle_get_many(query_params)

    with pytest.raises(
        RestlyConfigurationError,
        match=re.escape("OldView.trash (from TrashMixin) declares a query_params"),
    ):

        class OldView(TrashMixin, fr.AsyncRestView):
            pass


def test_a_plain_view_route_may_take_a_query_params_parameter():
    """On a plain ``View``, ``query_params`` was never special: it is an
    ordinary query parameter there."""

    class SearchView(fr.View):
        prefix = "/search"

        @fr.get("/")
        def search(self, query_params: str = ""):
            return {"query_params": query_params}


def test_an_old_name_set_after_the_class_fails_at_registration(client):
    class Thing(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class LateView(fr.AsyncRestView):
        prefix = "/things"
        model = Thing

    LateView.listing_param_schema = object  # type: ignore[attr-defined]
    with pytest.raises(
        RestlyConfigurationError,
        match="LateView sets listing_param_schema, which was renamed to "
        "list_params_schema",
    ):
        fr.include_view(client.app, LateView)


@pytest.mark.skipif(sys.version_info < (3, 14), reason="Deferred annotations need 3.14")
def test_the_check_does_not_evaluate_route_annotations(client):
    """On 3.14 a route may name a class that the module defines further down.
    The check reads the route's parameters when the view class is defined,
    before that name exists, so it must not evaluate the annotations."""

    class Thing(fr.IDMixin, SharedBase):
        name: Mapped[str]

    namespace = {"fr": fr, "Thing": Thing, "__name__": __name__}
    exec(
        "class LaterView(fr.AsyncRestView):\n"
        "    prefix = '/things'\n"
        "    model = Thing\n"
        "\n"
        "    @fr.post('/echo')\n"
        "    async def echo(self, body: EchoBody) -> EchoBody:\n"
        "        return body\n",
        namespace,
    )

    class EchoBody(BaseModel):
        text: str

    namespace["EchoBody"] = EchoBody
    fr.include_view(client.app, namespace["LaterView"])
    create_tables()

    assert client.post("/things/echo", json={"text": "hi"}).json() == {"text": "hi"}


@pytest.mark.parametrize("base", [fr.AsyncRestView, fr.RestView])
@pytest.mark.parametrize(
    "name, says",
    [
        ("make_new_object", "super().create(data) builds the new object"),
        ("update_object", "super().update(obj, data) applies the data"),
        ("save_object", "The commit bracket saves the object"),
        ("get_relationship_loader_options", "is now get_loader_options."),
    ],
)
def test_a_call_of_a_removed_method_names_the_replacement(base, name, says):
    """Code from 0.11, such as ``await self.make_new_object(data)`` inside
    ``create``, defines nothing that the class check sees. Its call fails at
    once, with the replacement."""
    view = object.__new__(type("GadgetView", (base,), {}))

    with pytest.raises(AttributeError, match=re.escape(says)):
        getattr(view, name)


def test_an_unknown_name_and_a_failing_property_keep_their_errors():
    class GadgetView(fr.RestView):
        @property
        def broken(self):
            raise AttributeError("the real error")

    view = object.__new__(GadgetView)

    with pytest.raises(AttributeError, match="'GadgetView' object has no attribute"):
        view.nope  # noqa: B018
    with pytest.raises(AttributeError, match="the real error"):
        view.broken  # noqa: B018


@pytest.mark.parametrize(
    "lookup, says",
    [
        (lambda: fr.DataclassBase, "Restly no longer owns a declarative base"),
        (lambda: fr.IDBase, "class Post(fr.IDMixin, Base)"),
        (lambda: fr.models.IDBase, "class Post(fr.IDMixin, Base)"),
        (lambda: fr.utils, "a get_settings() function"),
    ],
    ids=["DataclassBase", "IDBase", "models.IDBase", "utils"],
)
def test_a_removed_module_name_names_the_replacement(lookup, says):
    with pytest.raises(AttributeError, match=re.escape(says)):
        lookup()


def test_the_new_names_define_a_view():
    class NewView(fr.AsyncRestView):
        def to_list_response(self, list_result):
            return super().to_list_response(list_result)

        def to_single_response(self, obj):
            return super().to_single_response(obj)

        async def apply_list_params(self, stmt, list_params):
            return await super().apply_list_params(stmt, list_params)

        @fr.get("/trash")
        async def trash(self, list_params):
            return await self.handle_get_many(list_params)


def test_an_override_of_the_removed_to_response_raises():
    with pytest.raises(RestlyConfigurationError) as caught:

        class ItemView(fr.AsyncRestView):
            def to_response(self, result, shape=None):
                return result

    message = str(caught.value)
    assert "ItemView defines to_response, which was removed" in message
    assert "to_single_response" in message
    assert "to_list_response" in message
    assert "list_response_schema" in message


def test_an_override_of_to_response_from_a_mixin_raises():
    class ShapeMixin:
        def to_response(self, result, shape=None):
            return result

    with pytest.raises(RestlyConfigurationError) as caught:

        class ItemView(ShapeMixin, fr.AsyncRestView):
            pass

    assert "to_response (from ShapeMixin)" in str(caught.value)


def test_a_call_of_the_removed_to_response_says_what_to_use():
    class ItemView(fr.AsyncRestView):
        pass

    with pytest.raises(AttributeError) as caught:
        ItemView().to_response(None)

    message = str(caught.value)
    assert "to_response was removed" in message
    assert "to_single_response" in message


@pytest.mark.parametrize("package", [fr, fr.views])
def test_response_shape_is_gone(package):
    assert not hasattr(package, "ResponseShape")


@pytest.mark.parametrize(
    "old, new",
    [
        ("schema_response", "response_schema"),
        ("schema_create", "create_schema"),
        ("schema_update", "update_schema"),
        ("schema_list_params", "list_params_schema"),
    ],
)
def test_reading_an_old_setting_names_the_new_one(old, new):
    class Thing(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class ThingView(fr.AsyncRestView):
        model = Thing

    view = object.__new__(ThingView)
    with pytest.raises(AttributeError, match=f"{old} is now {new}"):
        getattr(view, old)
