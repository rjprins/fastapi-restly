from ._async import (
    AsyncRestView,
    async_make_new_object,
    async_save_object,
    async_update_object,
)
from ._base import (
    BaseRestView,
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
from ._sync import RestView, make_new_object, save_object, update_object

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
    "View",
    "ViewRoute",
    "async_make_new_object",
    "async_save_object",
    "async_update_object",
    "delete",
    "get",
    "include_view",
    "make_new_object",
    "patch",
    "post",
    "put",
    "route",
    "save_object",
    "update_object",
]
