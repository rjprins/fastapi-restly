import typing as _typing

from ._base import (
    _REF_EXISTS_REMOVED,
    BaseSchema,
    IDRef,
    IDSchema,
    MustExist,
    ReadOnly,
    TimestampsSchemaMixin,
    UTCDatetime,
    WriteOnly,
    derive_schema_list_response,
    derive_schema_response,
)
from ._generator import derive_schema

# Public API for ``fastapi_restly.schemas``.
#
# Framework internals (``OmitReadOnlyMixin``, ``PatchMixin``,
# ``create_model_with_optional_fields``, ``create_model_without_read_only_fields``,
# ``readonly_marker``, ``writeonly_marker``, ``getattrs``,
# ``rebase_with_model_config``, ``set_schema_title``, ``get_writable_inputs``,
# ``get_read_only_fields``, ``get_write_only_fields``, ``SQLAlchemyModel``,
# the ``_generator`` introspection helpers, etc.) live in
# ``fastapi_restly.schemas._base`` / ``._generator`` and are not re-exported
# here. They may move or change without notice.
__all__ = [
    "BaseSchema",
    "MustExist",
    "IDRef",
    "IDSchema",
    "ReadOnly",
    "TimestampsSchemaMixin",
    "UTCDatetime",
    "WriteOnly",
    "derive_schema",
    "derive_schema_list_response",
    "derive_schema_response",
]

if not _typing.TYPE_CHECKING:

    def __getattr__(name: str):
        if name == "RefExists":
            raise AttributeError(_REF_EXISTS_REMOVED)
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
