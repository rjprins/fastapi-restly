from ._async import AsyncRestView
from ._base import (
    BaseRestView,
    ListingResult,
    View,
    ViewRoute,
    delete,
    get,
    include_view,
    patch,
    post,
    put,
    route,
)
from ._react_admin import AsyncReactAdminView, ReactAdminView
from ._sync import RestView

# Public API for ``fastapi_restly.views``.
#
# Submodule exports are supported public API for users working in this
# subsystem. Some names, such as ``BaseRestView``, are intentionally kept out
# of the top-level ``fastapi_restly`` namespace because they are advanced
# building blocks rather than the primary import path.
__all__ = [
    "RestView",
    "AsyncRestView",
    "AsyncReactAdminView",
    "ReactAdminView",
    "BaseRestView",
    "ListingResult",
    "View",
    "ViewRoute",
    "delete",
    "get",
    "include_view",
    "patch",
    "post",
    "put",
    "route",
]
