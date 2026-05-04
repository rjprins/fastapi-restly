import fastapi_restly as fr
import fastapi_restly.views as fr_views


def test_base_rest_view_is_advanced_views_export_not_top_level():
    assert fr_views.BaseRestView.__name__ == "BaseRestView"
    assert "BaseRestView" in fr_views.__all__
    assert not hasattr(fr, "BaseRestView")
    assert "BaseRestView" not in fr.__all__
