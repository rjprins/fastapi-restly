import fastapi_restly as fr
import fastapi_restly.views as fr_views


def test_base_rest_view_is_advanced_views_export_not_top_level():
    assert fr_views.BaseRestView.__name__ == "BaseRestView"
    assert "BaseRestView" in fr_views.__all__
    assert not hasattr(fr, "BaseRestView")
    assert "BaseRestView" not in fr.__all__


def test_view_registration_is_via_include_view_not_class_method():
    assert hasattr(fr_views, "include_view")
    assert not hasattr(fr_views.View, "add_to_router")


def test_react_admin_mixin_is_not_public_api():
    assert not hasattr(fr, "ReactAdminMixin")
    assert "ReactAdminMixin" not in fr.__all__
    assert not hasattr(fr_views, "ReactAdminMixin")
    assert "ReactAdminMixin" not in fr_views.__all__


def test_timestamp_convenience_bases_are_not_public_api():
    assert not hasattr(fr, "IDStampsBase")
    assert "IDStampsBase" not in fr.__all__
    assert not hasattr(fr.models, "IDStampsBase")
    assert "IDStampsBase" not in fr.models.__all__

    assert not hasattr(fr, "IDStampsSchema")
    assert "IDStampsSchema" not in fr.__all__
    assert not hasattr(fr.schemas, "IDStampsSchema")
    assert "IDStampsSchema" not in fr.schemas.__all__


def test_rest_view_route_and_hook_names_are_current():
    current_names = (
        "list",
        "get",
        "create",
        "update",
        "delete",
        "perform_list",
        "build_query",
        "count_listing",
        "to_response_schema",
        "perform_get",
        "perform_create",
        "perform_update",
        "perform_delete",
    )

    for view_cls in (fr.RestView, fr.AsyncRestView):
        for name in current_names:
            assert hasattr(view_cls, name), f"{view_cls.__name__}.{name} is missing"
