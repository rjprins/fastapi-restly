"""
Schema generation utilities for auto-generating Pydantic schemas from SQLAlchemy models.
"""

import inspect
from typing import Any, Dict, List, Optional, Union, get_args

from sqlalchemy.orm import DeclarativeBase, Mapped, RelationshipProperty

from .schemas import BaseSchema, IDSchema, TimestampsSchemaMixin


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
    return isinstance(field, RelationshipProperty)


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
    if hasattr(field, "mapper") and hasattr(field.mapper, "class_"):
        return field.mapper.class_

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

        field_info: Dict[str, Any] = {
            "type": actual_type,
            "is_relationship": is_relationship_field(field_type),
            "target_model": get_relationship_target_model(field_type),
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

        # Check for default values from the model instance
        if hasattr(model_cls, name):
            attr = getattr(model_cls, name)
            if hasattr(attr, "default"):
                field_info["default"] = attr.default
                # If there's a SQLAlchemy default, make the field optional
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

        # Add field to definitions - use proper Pydantic field format
        # Don't include SQLAlchemy defaults as they're not JSON-serializable
        if field_info["is_optional"]:
            from pydantic import Field

            field_definitions[field_name] = (pydantic_type, Field(default=None))
        else:
            field_definitions[field_name] = (pydantic_type, ...)

    # Create the schema class using pydantic.create_model
    import pydantic

    schema_cls = pydantic.create_model(  # type: ignore
        schema_name,
        __doc__=f"Auto-generated schema for {model_cls.__name__}",
        __base__=tuple(bases),
        **field_definitions,
    )

    # Set read-only fields as a class variable
    schema_cls.read_only_fields = read_only_fields

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
    # Handle common SQLAlchemy types
    type_mapping = {
        "str": str,
        "int": int,
        "float": float,
        "bool": bool,
        "datetime": "datetime",
        "date": "date",
        "time": "time",
        "UUID": "UUID",
        "Decimal": "Decimal",
        "Text": str,
        "String": str,
        "Integer": int,
        "Float": float,
        "Boolean": bool,
        "DateTime": "datetime",
        "Date": "date",
        "Time": "time",
    }

    # Get the type name
    type_name = getattr(sqlalchemy_type, "__name__", str(sqlalchemy_type))

    # Map to Pydantic type
    pydantic_type = type_mapping.get(type_name, str)

    # Handle datetime types specifically
    if type_name in ["datetime", "DateTime"]:
        from datetime import datetime

        pydantic_type = datetime
    elif type_name in ["date", "Date"]:
        from datetime import date

        pydantic_type = date
    elif type_name in ["time", "Time"]:
        from datetime import time

        pydantic_type = time

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

    return create_schema_from_model(model_cls, schema_name)
