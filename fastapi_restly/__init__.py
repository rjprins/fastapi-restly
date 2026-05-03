# Database layer
from .db import (
    AsyncSessionDep,
    SessionDep,
    activate_savepoint_only_mode,
    async_session,
    configure,
    deactivate_savepoint_only_mode,
    get_async_engine,
    get_engine,
    get_fr_globals,
    session,
    use_fr_globals,
)

# Model base classes
from .models import (
    DataclassBase,
    IDBase,
    IDMixin,
    IDStampsBase,
    PlainBase,
    PlainIDBase,
    PlainIDMixin,
    PlainIDStampsBase,
    PlainTimestampsMixin,
    TimestampsMixin,
    async_get_one_or_create,
    get_one_or_create,
)

# Query modifiers
from .query import (
    QueryModifierVersion,
    apply_query_modifiers,
    get_query_modifier_version,
    set_query_modifier_version,
    use_query_modifier_version,
)

# Schema utilities
from .schemas import (
    BaseSchema,
    IDRef,
    IDSchema,
    IDStampsSchema,
    ReadOnly,
    TimestampsSchemaMixin,
    WriteOnly,
    auto_generate_schema_for_view,
    create_schema_from_model,
    resolve_ids_to_sqlalchemy_objects,
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
# Anything not listed here is considered internal and may change without
# warning. Submodule ``__init__.py`` files have their own (narrower)
# ``__all__`` lists for submodule-level imports such as
# ``from fastapi_restly.schemas import ReadOnly``.
__all__ = [
    # Database — session context managers
    "async_session",
    "session",
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
    "get_fr_globals",
    "use_fr_globals",
    # Models
    "DataclassBase",
    "IDBase",
    "IDMixin",
    "IDStampsBase",
    "PlainBase",
    "PlainIDBase",
    "PlainIDMixin",
    "PlainIDStampsBase",
    "PlainTimestampsMixin",
    "TimestampsMixin",
    "async_get_one_or_create",
    "get_one_or_create",
    # Query modifiers
    "QueryModifierVersion",
    "apply_query_modifiers",
    "get_query_modifier_version",
    "set_query_modifier_version",
    "use_query_modifier_version",
    # Schemas
    "BaseSchema",
    "IDRef",
    "IDSchema",
    "IDStampsSchema",
    "ReadOnly",
    "WriteOnly",
    "TimestampsSchemaMixin",
    "auto_generate_schema_for_view",
    "create_schema_from_model",
    "resolve_ids_to_sqlalchemy_objects",
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
