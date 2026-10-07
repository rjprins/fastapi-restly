"""Names that were renamed before 1.0 fail at class definition.

Restly no longer reads the old names, so a view that still uses one would
lose its setting or override without any error, or fail only on a request.
The error names the new name.
"""

import re

import pytest

import fastapi_restly as fr
from fastapi_restly.exc import RestlyConfigurationError

VIEW_BASES = [fr.AsyncRestView, fr.RestView, fr.AsyncReactAdminView, fr.ReactAdminView]


@pytest.mark.parametrize("base", VIEW_BASES)
def test_old_list_params_setting_names_the_new_one(base):
    with pytest.raises(RestlyConfigurationError) as excinfo:
        type("OldView", (base,), {"listing_param_schema": object})

    assert str(excinfo.value) == (
        "OldView sets listing_param_schema, which was renamed to "
        "schema_list_params. Rename the setting."
    )


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


def test_the_new_names_define_a_view():
    class NewView(fr.AsyncRestView):
        def to_list_response(self, list_result):
            return super().to_list_response(list_result)

        def to_single_response(self, obj):
            return super().to_single_response(obj)

        def apply_list_params(self, query, list_params):
            return super().apply_list_params(query, list_params)

        @fr.get("/trash")
        async def trash(self, list_params):
            return await self.handle_get_many(list_params)


def test_the_list_shape_is_named_list():
    assert fr.ResponseShape.LIST.value == "list"
    assert not hasattr(fr.ResponseShape, "LISTING")
