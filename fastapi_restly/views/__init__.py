from ._async import (
    AsyncRestView,
    async_make_new_object,
    async_save_object,
    async_update_object,
)
from ._base import (
    BaseRestView,  # kept importable for advanced subclassing; not in __all__
    View,
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
# ``BaseRestView`` is intentionally not listed: it is the abstract parent
# shared by ``RestView`` / ``AsyncRestView`` and has no endpoints of its own,
# so subclassing it directly is an advanced/internal pattern. It remains
# importable from this module for users who explicitly need it, but it is
# not part of the supported public surface.
__all__ = [
    "RestView",
    "AsyncRestView",
    "AsyncReactAdminView",
    "ReactAdminView",
    "View",
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
