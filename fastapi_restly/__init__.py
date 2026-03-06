from sqlalchemy.orm import mapped_column

# Database layer
from .db import (
    AsyncSession,
    AsyncSessionDep,
    FRAsyncSession,
    FRSession,
    Session,
    SessionDep,
    activate_savepoint_only_mode,
    deactivate_savepoint_only_mode,
    get_fr_globals,
    setup_async_database_connection,
    setup_database_connection,
    use_fr_globals,
)

# Model base classes
from .models import (
    Base,
    IDBase,
    IDStampsBase,
    PlainBase,
    PlainIDBase,
    PlainIDStampsBase,
    TimestampsMixin,
)

# Query modifiers
from .query import (
    QueryModifierVersion,
    apply_query_modifiers,
    create_query_param_schema,
    get_query_modifier_version,
    set_query_modifier_version,
    use_query_modifier_version,
)

# Schema utilities
from .schemas import (
    BaseSchema,
    IDSchema,
    IDStampsSchema,
    OmitReadOnlyMixin,
    PatchMixin,
    ReadOnly,
    TimestampsSchemaMixin,
    auto_generate_schema_for_view,
    create_schema_from_model,
    get_writable_inputs,
    is_readonly_field,
    resolve_ids_to_sqlalchemy_objects,
)

# Settings
from ._settings import settings

# Views
from .views import (
    AlchemyView,
    AsyncAlchemyView,
    BaseAlchemyView,
    delete,
    get,
    include_view,
    make_new_object,
    patch,
    post,
    put,
    route,
    update_object,
)

__all__ = [
    # Database (new names - preferred)
    "FRAsyncSession",
    "FRSession",
    "AsyncSessionDep",
    "SessionDep",
    # Database (deprecated aliases)
    "AsyncSession",
    "Session",
    # Database utilities
    "activate_savepoint_only_mode",
    "deactivate_savepoint_only_mode",
    "get_fr_globals",
    "setup_async_database_connection",
    "setup_database_connection",
    "use_fr_globals",
    # Models
    "Base",
    "IDBase",
    "IDStampsBase",
    "PlainBase",
    "PlainIDBase",
    "PlainIDStampsBase",
    "TimestampsMixin",
    # Query modifiers
    "QueryModifierVersion",
    "apply_query_modifiers",
    "create_query_param_schema",
    "get_query_modifier_version",
    "set_query_modifier_version",
    "use_query_modifier_version",
    # Schemas
    "BaseSchema",
    "IDSchema",
    "IDStampsSchema",
    "OmitReadOnlyMixin",
    "PatchMixin",
    "ReadOnly",
    "TimestampsSchemaMixin",
    "auto_generate_schema_for_view",
    "create_schema_from_model",
    "get_writable_inputs",
    "is_readonly_field",
    "resolve_ids_to_sqlalchemy_objects",
    # Settings
    "settings",
    # Views
    "AlchemyView",
    "AsyncAlchemyView",
    "BaseAlchemyView",
    "delete",
    "get",
    "include_view",
    "make_new_object",
    "mapped_column",
    "patch",
    "post",
    "put",
    "route",
    "update_object",
]
