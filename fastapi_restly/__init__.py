# Database layer
from .db import (
    AsyncSessionDep,
    RestlyContext,
    SessionDep,
    activate_savepoint_only_mode,
    async_open_session,
    configure,
    deactivate_savepoint_only_mode,
    get_async_engine,
    get_engine,
    get_fr_globals,
    open_session,
)

# Model base classes
from .models import DataclassBase, IDBase, IDMixin, IDStampsBase, TimestampsMixin

# List endpoint query parameters
from .query import apply_list_params, create_list_params_schema

# Schema utilities
from .schemas import (
    BaseSchema,
    IDRef,
    IDSchema,
    IDStampsSchema,
    ReadOnly,
    TimestampsSchemaMixin,
    WriteOnly,
    create_schema_from_model,
)

# Views
from .views import (
    AsyncReactAdminView,
    AsyncRestView,
    ReactAdminView,
    RestView,
    View,
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

# Public API surface for fastapi-restly.
#
# This top-level namespace is the primary public API. Submodule ``__all__``
# lists may expose additional supported advanced symbols for users working in
# that subsystem, such as ``from fastapi_restly.views import BaseRestView``.
__all__ = [
    # Database — session context managers
    "async_open_session",
    "open_session",
    # Database — FastAPI dependencies
    "AsyncSessionDep",
    "SessionDep",
    # Database — engine access
    "get_async_engine",
    "get_engine",
    # Database — setup & utilities
    "configure",
    "activate_savepoint_only_mode",
    "deactivate_savepoint_only_mode",
    "RestlyContext",
    "get_fr_globals",
    # Models
    "DataclassBase",
    "IDBase",
    "IDMixin",
    "IDStampsBase",
    "TimestampsMixin",
    # List endpoint query parameters
    "apply_list_params",
    "create_list_params_schema",
    # Schemas
    "BaseSchema",
    "IDRef",
    "IDSchema",
    "IDStampsSchema",
    "ReadOnly",
    "WriteOnly",
    "TimestampsSchemaMixin",
    "create_schema_from_model",
    # Views
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
