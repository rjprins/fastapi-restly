from importlib.metadata import PackageNotFoundError as _PackageNotFoundError
from importlib.metadata import version as _version

# Concept/layer namespaces, reachable as ``fr.<name>`` but kept out of the flat
# ``__all__`` so ``from fastapi_restly import *`` stays the everyday surface:
#   fr.exc     — errors, HTTP exceptions, and the uncommitted-changes warning
#   fr.objects — schema<->ORM helpers for use outside a view
#   fr.query   — low-level list query-parameter helpers
# (fr.db / fr.models / fr.schemas / fr.views are bound by the from-imports below.)
from . import exc, objects, query  # noqa: F401

# Database layer
from .db import AsyncSessionDep, SessionDep, configure, open_async_session, open_session

# Model base classes
from .models import DataclassBase, IDBase, TimestampsMixin

# Schema utilities
from .schemas import (
    BaseSchema,
    IDRef,
    IDSchema,
    MustExist,
    ReadOnly,
    TimestampsSchemaMixin,
    WriteOnly,
)

# Views
# (run_write_action / async_run_write_action are intentionally NOT re-exported at
# the top level: they're the self-free primitive the CRUD handlers and
# write_action share, and the off-HTTP use case that would justify a public name
# isn't built yet. They remain importable from fastapi_restly.views.)
from .views import (
    Action,
    AsyncReactAdminView,
    AsyncRestView,
    ListingResult,
    ReactAdminView,
    ResponseShape,
    RestView,
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

try:
    __version__ = _version("fastapi-restly")
except _PackageNotFoundError:  # pragma: no cover - only possible from an unpackaged tree
    __version__ = "0+unknown"

# Public API surface for fastapi-restly.
#
# This top-level namespace is the primary public API. The ``fr.exc`` namespace
# and the layer submodules (``fr.db``, ``fr.models``, ``fr.schemas``,
# ``fr.views``, ``fr.objects``, ``fr.query``) expose the remaining supported
# symbols for users working in that subsystem.
__all__ = [
    "__version__",
    # Database — session context managers
    "open_async_session",
    "open_session",
    # Database — FastAPI dependencies
    "AsyncSessionDep",
    "SessionDep",
    # Database — setup
    "configure",
    # Models
    "DataclassBase",
    "IDBase",
    "TimestampsMixin",
    # Schemas
    "BaseSchema",
    "IDRef",
    "IDSchema",
    "MustExist",
    "ReadOnly",
    "WriteOnly",
    "TimestampsSchemaMixin",
    # Views
    "RestView",
    "AsyncRestView",
    "ListingResult",
    "AsyncReactAdminView",
    "ReactAdminView",
    "Action",
    "ResponseShape",
    "View",
    "ViewRoute",
    "include_view",
    # Views — route decorators
    "route",
    "get",
    "post",
    "put",
    "patch",
    "delete",
]
