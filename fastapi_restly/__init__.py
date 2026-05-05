from importlib.metadata import PackageNotFoundError as _PackageNotFoundError
from importlib.metadata import version as _version

# Database layer
from .db import (
    AsyncSessionDep,
    SessionDep,
    configure,
    get_async_engine,
    get_engine,
    open_async_session,
    open_session,
)
from .exceptions import RestlyConfigurationError, RestlyError

# Model base classes
from .models import DataclassBase, IDBase, IDMixin, TimestampsMixin

# List endpoint query parameters
from .query import apply_list_params, create_list_params_schema

# Schema utilities
from .schemas import (
    BaseSchema,
    IDRef,
    IDSchema,
    ReadOnly,
    TimestampsSchemaMixin,
    WriteOnly,
)

# Views
from .views import (
    AsyncReactAdminView,
    AsyncRestView,
    ListingResult,
    ReactAdminView,
    RestView,
    View,
    ViewRoute,
    async_make_new_object,
    async_save_object,
    async_update_object,
    delete,
    get,
    include_view,
    make_new_object,
    patch,
    post,
    put,
    route,
    save_object,
    update_object,
)

try:
    __version__ = _version("fastapi-restly")
except _PackageNotFoundError:  # pragma: no cover - only possible from an unpackaged tree
    __version__ = "0+unknown"

# Public API surface for fastapi-restly.
#
# This top-level namespace is the primary public API. Submodule ``__all__``
# lists may expose additional supported advanced symbols for users working in
# that subsystem, such as ``from fastapi_restly.views import BaseRestView``.
__all__ = [
    "__version__",
    # Database — session context managers
    "open_async_session",
    "open_session",
    # Database — FastAPI dependencies
    "AsyncSessionDep",
    "SessionDep",
    # Database — engine access
    "get_async_engine",
    "get_engine",
    # Database — setup & utilities
    "configure",
    # Exceptions
    "RestlyError",
    "RestlyConfigurationError",
    # Models
    "DataclassBase",
    "IDBase",
    "IDMixin",
    "TimestampsMixin",
    # List endpoint query parameters
    "apply_list_params",
    "create_list_params_schema",
    # Schemas
    "BaseSchema",
    "IDRef",
    "IDSchema",
    "ReadOnly",
    "WriteOnly",
    "TimestampsSchemaMixin",
    # Views
    "RestView",
    "AsyncRestView",
    "ListingResult",
    "AsyncReactAdminView",
    "ReactAdminView",
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
