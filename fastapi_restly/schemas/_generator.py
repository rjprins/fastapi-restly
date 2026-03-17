"""
Schema generation utilities for auto-generating Pydantic schemas from SQLAlchemy models.
"""

import enum
import inspect
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Dict, List, Optional, Union, get_args
from uuid import UUID

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import DeclarativeBase, Mapped, RelationshipProperty

from ._base import BaseSchema, IDSchema, TimestampsSchemaMixin


def get_sqlalchemy_field_type(field: Any) -> Any:
    """
    Extract the Python type from a SQLAlchemy Mapped field.

    Args:
        field: A SQLAlchemy Mapped field

    Returns:
        The Python type annotation
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

    Args:
        field: A SQLAlchemy Mapped field

    Returns:
        True if the field is a relationship, False otherwise
    """
    if isinstance(field, RelationshipProperty):
        return True
    return isinstance(getattr(field, "property", None), RelationshipProperty)


def get_relationship_target_model(field: Any) -> Optional[type[DeclarativeBase]]:
    """
    Get the target model class for a relationship field.

    Args:
        field: A SQLAlchemy relationship field

    Returns:
        The target model class or None if not found
    """
    if not is_relationship_field(field):
        return None

    # Try to get the target from the relationship property
    relationship = field
    if not isinstance(relationship, RelationshipProperty):
        relationship = getattr(field, "property", None)

    if hasattr(relationship, "mapper") and hasattr(relationship.mapper, "class_"):
        return relationship.mapper.class_

    # Try to get from the type annotation
    if hasattr(field, "type"):
        target_type = field.type
        if hasattr(target_type, "__origin__") and target_type.__origin__ is list:
            # Handle List[Model] case
            args = get_args(target_type)
            if args:
                return args[0]
        elif inspect.isclass(target_type) and issubclass(target_type, DeclarativeBase):
            return target_type

    return None


def get_model_fields(model_cls: type[DeclarativeBase]) -> Dict[str, Any]:
    """
    Extract field information from a SQLAlchemy model.

    Args:
        model_cls: A SQLAlchemy model class

    Returns:
        Dictionary mapping field names to their types and metadata
    """
    fields: Dict[str, Any] = {}

    mapper = sa_inspect(model_cls)

    # Get all annotations from the model class and its base classes
    all_annotations = {}
    for cls in model_cls.mro():
        if hasattr(cls, "__annotations__"):
            all_annotations.update(cls.__annotations__)

    for name, field_type in all_annotations.items():
        if name.startswith("_"):
            continue

        # Check if it's a Mapped field
        if not hasattr(field_type, "__origin__") or field_type.__origin__ is not Mapped:
            continue

        # Extract the actual type from Mapped[Type]
        args = get_args(field_type)
        if not args:
            continue

        actual_type = args[0]
        relationship = mapper.relationships.get(name)

        field_info: Dict[str, Any] = {
            "type": actual_type,
            "is_relationship": relationship is not None,
            "target_model": (
                relationship.mapper.class_ if relationship is not None else None
            ),
            "is_optional": False,
            "default": None,
        }

        # Check if the field is optional (Union with None or Optional)
        if hasattr(actual_type, "__origin__"):
            origin = actual_type.__origin__
            if origin is Union:
                args = get_args(actual_type)
                if type(None) in args:
                    field_info["is_optional"] = True
                    # Remove None from the type
                    non_none_types = [arg for arg in args if arg is not type(None)]
                    if non_none_types:
                        field_info["type"] = non_none_types[0]

        if relationship is not None:
            # Relationship fields are response-oriented in generated schemas.
            # Keep them optional so create/update inputs can rely on FK columns.
            field_info["is_optional"] = True
        elif name in mapper.columns:
            column = mapper.columns[name]
            if column.default is not None or column.server_default is not None:
                field_info["default"] = column.default or column.server_default
                field_info["is_optional"] = True

        fields[name] = field_info

    return fields


def create_schema_from_model(
    model_cls: type[DeclarativeBase],
    schema_name: Optional[str] = None,
    include_relationships: bool = True,
    include_readonly_fields: bool = True,
) -> type[BaseSchema]:
    """
    Auto-generate a Pydantic schema from a SQLAlchemy model.

    Args:
        model_cls: The SQLAlchemy model class
        schema_name: Optional name for the generated schema class
        include_relationships: Whether to include relationship fields
        include_readonly_fields: Whether to include read-only fields like id, created_at, etc.

    Returns:
        A Pydantic schema class
    """
    if schema_name is None:
        schema_name = f"{model_cls.__name__}Schema"

    # Get field information from the model
    model_fields = get_model_fields(model_cls)

    # Determine base classes - start with the most specific ones
    bases: List[type] = []

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
    field_definitions: Dict[str, Any] = {}
    read_only_fields: List[str] = []

    for field_name, field_info in model_fields.items():
        # Skip relationships if not requested
        if field_info["is_relationship"] and not include_relationships:
            continue

        # Determine if field should be read-only
        is_readonly = (
            field_name in ["id", "created_at", "updated_at"] and include_readonly_fields
        )

        if is_readonly:
            read_only_fields.append(field_name)

        # Convert SQLAlchemy type to Pydantic type
        pydantic_type = convert_sqlalchemy_type_to_pydantic(
            field_info["type"], field_info["is_optional"]
        )

        # Handle relationships
        if field_info["is_relationship"] and field_info["target_model"]:
            if (
                hasattr(field_info["type"], "__origin__")
                and field_info["type"].__origin__ is list
            ):
                # Many relationship
                target_schema = create_schema_from_model(
                    field_info["target_model"],
                    include_relationships=False,  # Avoid circular references
                    include_readonly_fields=False,
                )
                pydantic_type = List[target_schema]
            else:
                # One relationship
                target_schema = create_schema_from_model(
                    field_info["target_model"],
                    include_relationships=False,  # Avoid circular references
                    include_readonly_fields=False,
                )
                pydantic_type = target_schema

            if field_info["is_optional"]:
                pydantic_type = Optional[pydantic_type]

        # Add field to definitions - use proper Pydantic field format
        # Don't include SQLAlchemy defaults as they're not JSON-serializable
        if field_info["is_optional"]:
            from pydantic import Field

            field_definitions[field_name] = (pydantic_type, Field(default=None))
        else:
            field_definitions[field_name] = (pydantic_type, ...)

    # Apply ReadOnly annotation to read-only fields
    if read_only_fields:
        from ._base import ReadOnly

        for field_name in read_only_fields:
            if field_name in field_definitions:
                original_type, field_info = field_definitions[field_name]
                # Apply ReadOnly annotation to the type
                field_definitions[field_name] = (ReadOnly[original_type], field_info)

    # Create the schema class using pydantic.create_model
    import pydantic

    schema_cls = pydantic.create_model(  # type: ignore
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

    Args:
        sqlalchemy_type: The SQLAlchemy type
        is_optional: Whether the field is optional

    Returns:
        A Pydantic-compatible type
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
        from typing import Optional

        pydantic_type = Optional[pydantic_type]

    return pydantic_type


def auto_generate_schema_for_view(
    view_cls: type, model_cls: type[DeclarativeBase], schema_name: Optional[str] = None
) -> type[BaseSchema]:
    """
    Auto-generate a schema for a view class if none is specified.

    Args:
        view_cls: The view class
        model_cls: The SQLAlchemy model class
        schema_name: Optional name for the generated schema

    Returns:
        A Pydantic schema class
    """
    if schema_name is None:
        schema_name = f"{view_cls.__name__}Schema"

    return create_schema_from_model(
        model_cls, schema_name, include_relationships=False
    )
