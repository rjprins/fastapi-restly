from ._base import (
    BaseSchema,
    IDRef,
    IDSchema,
    IDStampsSchema,
    OmitReadOnlyMixin,
    PatchMixin,
    ReadOnly,
    SQLAlchemyModel,
    TimestampsSchemaMixin,
    WriteOnly,
    async_resolve_ids_to_sqlalchemy_objects,
    create_model_with_optional_fields,
    create_model_without_read_only_fields,
    get_read_only_fields,
    get_writable_inputs,
    get_write_only_fields,
    getattrs,
    is_field_writeonly,
    is_readonly_field,
    readonly_marker,
    rebase_with_model_config,
    resolve_ids_to_sqlalchemy_objects,
    set_schema_title,
    writeonly_marker,
)
from ._base import (
    is_readonly_field as is_field_readonly,  # noqa: F401  (canonical alias)
)
from ._generator import (
    auto_generate_schema_for_view,
    convert_sqlalchemy_type_to_pydantic,
    create_schema_from_model,
    get_model_fields,
    get_relationship_target_model,
    get_sqlalchemy_field_type,
    is_relationship_field,
)

# Public API for ``fastapi_restly.schemas``.
#
# A number of helper symbols (``SQLAlchemyModel``, ``readonly_marker``,
# ``writeonly_marker``, ``getattrs``, ``rebase_with_model_config``,
# ``set_schema_title``, ``get_writable_inputs``,
# ``create_model_with_optional_fields``,
# ``create_model_without_read_only_fields``, the ``_generator`` introspection
# helpers, etc.) remain importable for backwards compatibility and for the
# framework's own test suite, but they are framework internals and are not
# part of the supported public surface. They may move or change without
# notice in a future release.
#
# For checking whether a field is read-only/write-only on a schema, use the
# ``is_field_readonly`` / ``is_field_writeonly`` pair. The legacy
# ``is_readonly_field`` name remains importable but is not the recommended
# spelling.
__all__ = [
    "BaseSchema",
    "IDRef",
    "IDSchema",
    "IDStampsSchema",
    "OmitReadOnlyMixin",
    "PatchMixin",
    "ReadOnly",
    "TimestampsSchemaMixin",
    "WriteOnly",
    "async_resolve_ids_to_sqlalchemy_objects",
    "auto_generate_schema_for_view",
    "create_schema_from_model",
    "is_field_readonly",
    "is_field_writeonly",
    "resolve_ids_to_sqlalchemy_objects",
]
