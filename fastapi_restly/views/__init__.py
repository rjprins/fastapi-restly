"""Class-based views with default CRUD methods.

Every CRUD route on ``RestView`` / ``AsyncRestView`` runs through three
methods. The handler handles the steps, and you override a step:

1. ``<verb>_endpoint``, the endpoint method: the ``@route``, FastAPI
   signature, ``response_model``, and ``to_response``. Replace only to change
   the HTTP contract.
2. ``handle_<verb>``, the handler: handles the steps, ``authorize``, the
   business method, and the commit bracket (``before_action_commit`` ->
   commit -> ``after_action_commit``). Final: call it from a custom route,
   never override it.
3. ``<verb>`` (``get_many``, ``get_one``, ``create``, ``update``, ``delete``),
   the business method: the domain operation, auth-free and commit-free. The
   usual override point. A write business method changes objects in the
   session and returns the object; it does not flush.

Cross-cutting seams: ``scope`` (read visibility), ``authorize`` (policy),
``apply_list_params`` (list params), ``to_response`` (response shape),
``write_action`` (custom write actions), ``shared_write_action_commit``
(one commit over several writes). After a write business method, the
commit bracket saves: it adds a new object, flushes, refreshes the object
and every other row of the view's model that the action wrote, and loads
the relationships of the response on them. ``fr.objects`` has the
schema-to-ORM functions, for another model and for code outside a view; a
server-stamped field is a column default on the model. ``View`` is the bare class-based
primitive for non-CRUD endpoint groups (auth flows, webhooks, RPC).
"""

from .._pagination import Envelope, NoPagination, NumberedPagination, PaginatedEnvelope
from ._async import AsyncRestView
from ._base import (
    Action,
    BaseRestView,
    ListResult,
    ReadScope,
    ResponseShape,
    View,
    ViewRoute,
    delete,
    get,
    include_view,
    list_filter,
    patch,
    post,
    put,
    resolve_scope,
    route,
)
from ._sync import RestView
from ._view_types import ViewCreate, ViewID, ViewListResponse, ViewResponse, ViewUpdate

# isort: split
# Last: the react-admin views import the public names above, as a view of
# yours does.
from ._react_admin import AsyncReactAdminView, ReactAdminListParams, ReactAdminView

# Public API for ``fastapi_restly.views``.
#
# Submodule exports are supported public API for users working in this
# subsystem. Some names, such as ``BaseRestView``, stay out of the top-level
# ``fastapi_restly`` namespace because they are advanced building blocks, not
# the primary import path.
__all__ = [
    "RestView",
    "AsyncRestView",
    "AsyncReactAdminView",
    "ReactAdminView",
    "ReactAdminListParams",
    "BaseRestView",
    "ListResult",
    "NumberedPagination",
    "NoPagination",
    "Envelope",
    "PaginatedEnvelope",
    "Action",
    "ReadScope",
    "ResponseShape",
    "View",
    "ViewRoute",
    "ViewID",
    "ViewResponse",
    "ViewListResponse",
    "ViewCreate",
    "ViewUpdate",
    "resolve_scope",
    "delete",
    "get",
    "include_view",
    "list_filter",
    "patch",
    "post",
    "put",
    "route",
]
