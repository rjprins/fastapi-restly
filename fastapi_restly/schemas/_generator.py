"""
Schema generation utilities for auto-generating Pydantic schemas from SQLAlchemy models.
"""

import enum
import inspect
import sys
import types
from collections.abc import Collection
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, ForwardRef, Union, get_args, get_origin
from uuid import UUID

import pydantic
from pydantic import Field
from sqlalchemy import Column
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import ColumnProperty, DeclarativeBase, Mapped, RelationshipProperty

from ._base import (
    BaseSchema,
    IDSchema,
    ReadOnly,
    TimestampsSchemaMixin,
    _is_unresolved,
    _own_annotations,
)


def get_sqlalchemy_field_type(field: Any) -> Any:
    """
    Extract the Python type from a SQLAlchemy Mapped field.

    :param field: A SQLAlchemy Mapped field.
    :returns: The Python type annotation.
    """
    # Get the type annotation from the Mapped field
    if hasattr(field, "type"):
        return field.type
    elif hasattr(field, "__origin__"):
        return field.__origin__
    else:
        # Fallback to Any if we can't determine the type
        return Any


def is_relationship_field(field: Any) -> bool:
    """
    Check if a field is a SQLAlchemy relationship.

    :param field: A SQLAlchemy Mapped field.
    :returns: True if the field is a relationship, False otherwise.
    """
    if isinstance(field, RelationshipProperty):
        return True
    return isinstance(getattr(field, "property", None), RelationshipProperty)


def get_relationship_target_model(field: Any) -> type[DeclarativeBase] | None:
    """
    Get the target model class for a relationship field.

    :param field: A SQLAlchemy relationship field.
    :returns: The target model class, or None if not found.
    """
    if not is_relationship_field(field):
        return None

    # Try to get the target from the relationship property
    relationship = field
    if not isinstance(relationship, RelationshipProperty):
        relationship = getattr(field, "property", None)

    if (
        relationship is not None
        and hasattr(relationship, "mapper")
        and hasattr(relationship.mapper, "class_")
    ):
        return relationship.mapper.class_

    # Try to get from the type annotation
    if hasattr(field, "type"):
        target_type = field.type
        if hasattr(target_type, "__origin__") and target_type.__origin__ is list:
            # Handle list[Model] case
            args = get_args(target_type)
            if args:
                return args[0]
        elif inspect.isclass(target_type) and issubclass(target_type, DeclarativeBase):
            return target_type

    return None


def _resolve_annotation(owner: type, annotation: Any) -> Any:
    """Evaluate a string annotation against ``owner``'s module, or return None."""
    if isinstance(annotation, ForwardRef):
        annotation = annotation.__forward_arg__
    if isinstance(annotation, str):
        module = sys.modules.get(owner.__module__)
        # Module names win over class attributes, as in typing.get_type_hints:
        # a column named ``date`` must not shadow the ``date`` type.
        try:
            annotation = eval(
                annotation, dict(vars(owner)), dict(vars(module)) if module else {}
            )
        except Exception:
            return None
    return annotation


def _model_annotations(model_cls: type, keys: Collection[str]) -> dict[str, Any]:
    """Map each annotated name in ``keys`` to its inner type, or None if unresolvable.

    ``Mapped[X]`` gives ``X``; a plain annotation (SQLModel) is kept as is.
    The class nearest ``model_cls`` on the MRO wins.
    """
    annotations: dict[str, Any] = {}
    for cls in model_cls.mro():
        for name, annotation in _own_annotations(cls).items():
            if name in annotations or name not in keys:
                continue
            annotation = _resolve_annotation(cls, annotation)
            if get_origin(annotation) is Mapped:
                args = get_args(annotation)
                annotation = _resolve_annotation(cls, args[0]) if args else None
            if annotation is not None and _is_unresolved(annotation):
                annotation = None
            annotations[name] = annotation
    return annotations


def _split_optional(annotation: Any) -> tuple[Any, bool]:
    """Return ``(type, is_optional)`` with ``None`` removed from a union."""
    if isinstance(annotation, types.UnionType) or get_origin(annotation) is Union:
        union_args = get_args(annotation)
        if type(None) in union_args:
            non_none_types = [arg for arg in union_args if arg is not type(None)]
            return (non_none_types[0] if non_none_types else annotation), True
    return annotation, False


def get_model_fields(model_cls: type[DeclarativeBase]) -> dict[str, Any]:
    """
    Extract field information from a SQLAlchemy model.

    The mapper decides which attributes exist; annotations only refine a
    column's type. Fields follow annotation order, then the mapper's order.

    :param model_cls: A SQLAlchemy model class.
    :returns: Dictionary mapping field names to their types and metadata.
    :raises TypeError: If a column's Python type can be read from neither its
        annotation nor its column type.
    """
    fields: dict[str, Any] = {}

    mapper = sa_inspect(model_cls)
    props = {prop.key: prop for prop in mapper.attrs}
    annotations = _model_annotations(model_cls, props.keys())
    names = list(annotations)
    names += [name for name in props if name not in annotations]

    for name in names:
        if name.startswith("_"):
            continue
        prop = props[name]

        if isinstance(prop, RelationshipProperty):
            target_model = prop.mapper.class_
            # Relationship fields are response-oriented in generated schemas.
            # Keep them optional so create/update inputs can rely on FK columns.
            fields[name] = {
                "type": list[target_model] if prop.uselist else target_model,
                "is_relationship": True,
                "target_model": target_model,
                "is_optional": True,
                "is_read_only": False,
                "default": None,
            }
            continue

        if not isinstance(prop, ColumnProperty):
            # Synonyms and composites are views of attributes listed already.
            continue

        column = prop.columns[0]
        annotation = annotations.get(name)
        if annotation is not None:
            field_type, is_optional = _split_optional(annotation)
        else:
            try:
                field_type = column.type.python_type
            except NotImplementedError:
                field_type = object
            if field_type is object:
                raise TypeError(
                    f"Cannot determine the Python type of "
                    f"{model_cls.__name__}.{name} for an auto-generated schema: "
                    f"it has no annotation that resolves at runtime, and its "
                    f"column type {column.type!r} has no concrete python_type. Annotate "
                    f"it as Mapped[...] or set schema= on the view."
                ) from None
            is_optional = bool(getattr(column, "nullable", False))

        default = None
        column_default = getattr(column, "default", None)
        server_default = getattr(column, "server_default", None)
        if column_default is not None or server_default is not None:
            default = column_default or server_default
            is_optional = True

        fields[name] = {
            "type": field_type,
            "is_relationship": False,
            "target_model": None,
            "is_optional": is_optional,
            # A column_property over an expression cannot be written.
            "is_read_only": not isinstance(column, Column),
            "default": default,
        }

    return fields


def create_schema_from_model(
    model_cls: type[DeclarativeBase],
    *,
    schema_name: str | None = None,
    include_relationships: bool = True,
    include_readonly_fields: bool = True,
) -> type[BaseSchema]:
    """
    Auto-generate a Pydantic schema from a SQLAlchemy model.

    :param model_cls: The SQLAlchemy model class.
    :param schema_name: Name for the generated schema class. Defaults to the
        model name suffixed with ``Read``.
    :param include_relationships: Whether to include relationship fields.
    :param include_readonly_fields: Whether to include read-only fields such as
        ``id``, ``created_at`` and ``updated_at``.
    :returns: A Pydantic schema class.
    """
    if schema_name is None:
        schema_name = f"{model_cls.__name__}Read"

    # Get field information from the model
    model_fields = get_model_fields(model_cls)

    # Determine base classes - start with the most specific ones
    bases: list[type] = []

    # Check if model has timestamp fields (inherits from TimestampsMixin)
    has_timestamps = "created_at" in model_fields and "updated_at" in model_fields
    if has_timestamps:
        bases.append(TimestampsSchemaMixin)

    # Check if model has an id field (inherits from IDBase)
    has_id = "id" in model_fields
    if has_id:
        bases.append(IDSchema)

    # Always include BaseSchema as the base
    bases.append(BaseSchema)

    # Create field definitions for the schema
    field_definitions: dict[str, Any] = {}
    read_only_fields: list[str] = []

    for field_name, field_info in model_fields.items():
        # Skip relationships if not requested
        if field_info["is_relationship"] and not include_relationships:
            continue

        # Determine if field should be read-only
        is_readonly = (
            field_name in ["id", "created_at", "updated_at"]
            or field_info["is_read_only"]
        ) and include_readonly_fields

        if is_readonly:
            read_only_fields.append(field_name)

        if field_info["is_relationship"]:
            target_model = field_info["target_model"]

            # Skip self-referential relationship to avoid infinite recursion
            if target_model is model_cls:
                continue

            target_schema = create_schema_from_model(
                target_model,
                include_relationships=False,  # Avoid circular references
                include_readonly_fields=False,
            )
            if get_origin(field_info["type"]) is list:
                pydantic_type = list[target_schema]
            else:
                pydantic_type = target_schema

            if field_info["is_optional"]:
                pydantic_type = pydantic_type | None
        else:
            pydantic_type = convert_sqlalchemy_type_to_pydantic(
                field_info["type"], field_info["is_optional"]
            )

        # Add field to definitions - use proper Pydantic field format
        # Don't include SQLAlchemy defaults as they're not JSON-serializable
        if field_info["is_optional"]:
            field_definitions[field_name] = (pydantic_type, Field(default=None))
        else:
            field_definitions[field_name] = (pydantic_type, ...)

    # Apply ReadOnly annotation to read-only fields
    for field_name in read_only_fields:
        if field_name in field_definitions:
            original_type, field_info = field_definitions[field_name]
            # Apply ReadOnly annotation to the type
            field_definitions[field_name] = (ReadOnly[original_type], field_info)

    # Create the schema class using pydantic.create_model
    schema_cls = pydantic.create_model(  # type: ignore[call-overload]
        schema_name,
        __doc__=f"Auto-generated schema for {model_cls.__name__}",
        __base__=tuple(bases),
        **field_definitions,
    )

    return schema_cls


def convert_sqlalchemy_type_to_pydantic(
    sqlalchemy_type: Any, is_optional: bool = False
) -> Any:
    """
    Convert a SQLAlchemy type to a Pydantic-compatible type.

    :param sqlalchemy_type: The SQLAlchemy type.
    :param is_optional: Whether the field is optional.
    :returns: A Pydantic-compatible type.
    """
    type_name = getattr(sqlalchemy_type, "__name__", str(sqlalchemy_type))

    if sqlalchemy_type is Any:
        pydantic_type = Any
    elif sqlalchemy_type in (
        str,
        int,
        float,
        bool,
        dict,
        list,
        datetime,
        date,
        time,
        UUID,
        Decimal,
    ):
        pydantic_type = sqlalchemy_type
    elif isinstance(sqlalchemy_type, type) and issubclass(sqlalchemy_type, enum.Enum):
        pydantic_type = sqlalchemy_type
    elif isinstance(sqlalchemy_type, type) and issubclass(
        sqlalchemy_type, DeclarativeBase
    ):
        # Relationship targets are replaced with nested schemas later.
        pydantic_type = sqlalchemy_type
    elif getattr(sqlalchemy_type, "__origin__", None) is not None:
        # Preserve parameterized container types like dict[str, Any] or list[int].
        pydantic_type = sqlalchemy_type
    elif type_name in {"Text", "String"}:
        pydantic_type = str
    elif type_name in {"Integer"}:
        pydantic_type = int
    elif type_name in {"Float"}:
        pydantic_type = float
    elif type_name in {"Boolean"}:
        pydantic_type = bool
    elif type_name in {"DateTime"}:
        pydantic_type = datetime
    elif type_name in {"Date"}:
        pydantic_type = date
    elif type_name in {"Time"}:
        pydantic_type = time
    else:
        raise TypeError(
            f"Unsupported field type for auto-generated schema: {sqlalchemy_type!r}"
        )

    # Handle optional types
    if is_optional:
        pydantic_type = pydantic_type | None

    return pydantic_type


def auto_generate_schema_for_view(
    view_cls: type, model_cls: type[DeclarativeBase], schema_name: str | None = None
) -> type[BaseSchema]:
    """
    Auto-generate a schema for a view class if none is specified.

    :param view_cls: The view class.
    :param model_cls: The SQLAlchemy model class.
    :param schema_name: Name for the generated schema. Defaults to the model
        name suffixed with ``Read``.
    :returns: A Pydantic schema class.
    """
    if schema_name is None:
        schema_name = f"{model_cls.__name__}Read"

    return create_schema_from_model(
        model_cls, schema_name=schema_name, include_relationships=False
    )
