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
