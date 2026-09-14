"""Class-based views with default CRUD methods and explicit override tiers.

Every CRUD verb on ``RestView`` / ``AsyncRestView`` exists at three tiers.
Name the tier that owns your change and override one method:

1. ``<verb>_endpoint``, the endpoint method: the ``@route``, FastAPI
   signature, ``response_model``, and ``to_response``. Replace only to change
   the HTTP contract.
2. ``handle_<verb>``, the handler: runs ``authorize`` and the commit
   bracket (``before_action_commit`` -> commit -> ``after_action_commit``).
   Final: call it from a custom route, never override it.
3. ``<verb>`` (``get_many``, ``get_one``, ``create``, ``update``, ``delete``),
   the business method: the domain operation, auth-free and commit-free. The
   usual override point.

Cross-cutting seams: ``scope`` (read visibility), ``authorize`` (policy),
``apply_query_params`` (URL grammar), ``to_response`` (wire shape),
``write_action`` (custom write actions), ``shared_write_action_commit``
(one commit over several writes). Under the verbs sit the final
domain utilities (``make_new_object``, ``update_object``, ``save_object``):
call them from a verb override, never override them; a server-stamped
field is a column default on the model. ``View`` is the bare class-based
primitive for non-CRUD endpoint groups (auth flows, webhooks, RPC).
"""

from ._async import AsyncRestView
from ._base import (
    Action,
    BaseRestView,
    Envelope,
    ListingResult,
    PaginatedEnvelope,
    ReadScope,
    ResponseShape,
    View,
    ViewRoute,
    delete,
    get,
    include_view,
    patch,
    post,
    put,
    resolve_scope,
    route,
)
from ._lifecycle import async_run_write_action, run_write_action
from ._react_admin import AsyncReactAdminView, ReactAdminView
from ._sync import RestView

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
    "BaseRestView",
    "ListingResult",
    "Envelope",
    "PaginatedEnvelope",
    "Action",
    "ReadScope",
    "ResponseShape",
    "View",
    "ViewRoute",
    "async_run_write_action",
    "run_write_action",
    "resolve_scope",
    "delete",
    "get",
    "include_view",
    "patch",
    "post",
    "put",
    "route",
]
