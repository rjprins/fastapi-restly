import typing as _typing
from importlib.metadata import PackageNotFoundError as _PackageNotFoundError
from importlib.metadata import version as _version

# Concept/layer namespaces, reachable as ``fr.<name>`` but kept out of the flat
# ``__all__`` so ``from fastapi_restly import *`` stays the everyday surface:
#   fr.exc     — errors, HTTP exceptions, and the uncommitted-changes warning
#   fr.objects — schema<->ORM helpers for use outside a view
#   fr.query   — low-level helpers for list params
# (fr.db / fr.models / fr.schemas / fr.views / fr.clauses are bound by the
# from-imports below.)
from . import exc, objects, query  # noqa: F401

# Clauses — composable, context-aware query fragments
from .clauses import (
    ClauseNamespace,
    ContextNamespace,
    ContextParam,
    WhereClause,
    all_of,
    any_of,
    apply_clauses,
    none_of,
    where_clause,
)

# Database layer
from .db import AsyncSessionDep, SessionDep, configure, open_async_session, open_session

# Models
from .models import IDMixin, ModelDefaults, TimestampsMixin

# Schema utilities
from .schemas import (
    BaseSchema,
    IDRef,
    IDSchema,
    MustExist,
    ReadOnly,
    TimestampsSchemaMixin,
    UTCDatetime,
    WriteOnly,
    in_utc,
)

# Views
from .views import (
    Action,
    AsyncReactAdminView,
    AsyncRestView,
    ListResult,
    NoPagination,
    NumberedPagination,
    ReactAdminView,
    ResponseShape,
    RestView,
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

try:
    __version__ = _version("fastapi-restly")
except (
    _PackageNotFoundError
):  # pragma: no cover - only possible from an unpackaged tree
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
    # Clauses
    "ClauseNamespace",
    "WhereClause",
    "where_clause",
    "all_of",
    "any_of",
    "none_of",
    "apply_clauses",
    "ContextNamespace",
    "ContextParam",
    # Models
    "ModelDefaults",
    "IDMixin",
    "TimestampsMixin",
    # Schemas
    "BaseSchema",
    "IDRef",
    "IDSchema",
    "MustExist",
    "ReadOnly",
    "WriteOnly",
    "TimestampsSchemaMixin",
    "UTCDatetime",
    "in_utc",
    # Views
    "RestView",
    "AsyncRestView",
    "ListResult",
    "NumberedPagination",
    "NoPagination",
    "AsyncReactAdminView",
    "ReactAdminView",
    "Action",
    "ResponseShape",
    "View",
    "ViewRoute",
    "include_view",
    "list_filter",
    "resolve_scope",
    # Views — route decorators
    "route",
    "get",
    "post",
    "put",
    "patch",
    "delete",
]


if _typing.TYPE_CHECKING:
    from . import testing as testing
else:

    def __getattr__(name: str):
        # ``fr.testing`` on demand: importing it eagerly would drag the HTTP
        # test client (and httpx) into every production import of
        # fastapi_restly. Type checkers see the import above instead, so
        # they still report an unknown name.
        if name == "testing":
            import fastapi_restly.testing as testing_module

            return testing_module
        if name == "RefExists":
            from .schemas._base import _REF_EXISTS_REMOVED

            raise AttributeError(_REF_EXISTS_REMOVED)
        from .models._base import _REMOVED_BASES

        if name in _REMOVED_BASES:
            raise AttributeError(_REMOVED_BASES[name])
        if name == "utils":
            raise AttributeError(
                "fr.utils was removed, with CurrentSettingsMixin. Use a plain "
                "pydantic-settings class and a get_settings() function, and "
                "pass the settings to your app factory in tests."
            )
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
