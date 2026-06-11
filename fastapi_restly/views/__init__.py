"""Class-based views: generated CRUD plus explicit override tiers.

Every CRUD verb on ``RestView`` / ``AsyncRestView`` exists at three tiers —
name the tier that owns your change and override one method:

1. ``<verb>_endpoint`` — the route shell (wire tier): the ``@route``, FastAPI
   signature, ``response_model``, and ``to_response``. Replace only to change
   the HTTP contract.
2. ``handle_<verb>`` — the request handler: runs ``authorize`` and the commit
   bracket (``before_commit`` -> commit -> ``after_commit``). Override for
   orchestration or timing.
3. ``<verb>`` (``get_many``, ``get_one``, ``create``, ``update``, ``delete``)
   — the business verb: the domain operation, auth-free and commit-free. The
   usual override point.

Cross-cutting seams: ``build_query`` (read scope/visibility), ``authorize``
(policy), ``apply_query_params`` (URL grammar), ``to_response`` (wire shape),
``write_action`` (custom write brackets). ``View`` is the bare class-based
primitive for non-CRUD endpoint groups (auth flows, webhooks, RPC).
"""

from ._async import AsyncRestView
from ._base import (
    Action,
    BaseRestView,
    ListingResult,
    ResponseShape,
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
    "Action",
    "ResponseShape",
    "View",
    "ViewRoute",
    "async_run_write_action",
    "run_write_action",
    "delete",
    "get",
    "include_view",
    "patch",
    "post",
    "put",
    "route",
]
