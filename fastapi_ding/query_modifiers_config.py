"""
Configuration system for query modifiers.

This module provides a pluggable system for choosing between different query modifier
implementations (v1 vs v2) and allows users to configure their preferred interface.
"""

from enum import Enum
from typing import Any, Callable, Protocol

from sqlalchemy import Select
from sqlalchemy.orm import DeclarativeBase
from starlette.datastructures import QueryParams

import pydantic

SchemaType = type[pydantic.BaseModel]


class QueryModifierVersion(Enum):
    """Available query modifier versions."""
    V1 = "v1"  # Original JSONAPI-style interface
    V2 = "v2"  # Standard HTTP interface


class QueryModifierInterface(Protocol):
    """Protocol for query modifier implementations."""
    
    def apply_query_modifiers(
        self,
        query_params: QueryParams,
        select_query: Select[Any],
        model: type[DeclarativeBase],
        schema_cls: SchemaType,
    ) -> Select[Any]:
        """Apply query modifiers to a select query."""
        ...


# Global configuration
_query_modifier_version = QueryModifierVersion.V1


def set_query_modifier_version(version: QueryModifierVersion) -> None:
    """
    Set the global query modifier version to use.
    
    Args:
        version: The query modifier version to use (V1 or V2)
    """
    global _query_modifier_version
    _query_modifier_version = version


def get_query_modifier_version() -> QueryModifierVersion:
    """
    Get the current global query modifier version.
    
    Returns:
        The current query modifier version
    """
    return _query_modifier_version


def get_query_modifier_interface() -> QueryModifierInterface:
    """
    Get the current query modifier interface based on the configured version.
    
    Returns:
        The query modifier interface to use
    """
    if _query_modifier_version == QueryModifierVersion.V2:
        from .query_modifiers_v2 import apply_query_modifiers_v2
        
        class V2Interface:
            def apply_query_modifiers(
                self,
                query_params: QueryParams,
                select_query: Select[Any],
                model: type[DeclarativeBase],
                schema_cls: SchemaType,
            ) -> Select[Any]:
                return apply_query_modifiers_v2(query_params, select_query, model, schema_cls)
        
        return V2Interface()
    else:
        from .query_modifiers import apply_query_modifiers
        
        class V1Interface:
            def apply_query_modifiers(
                self,
                query_params: QueryParams,
                select_query: Select[Any],
                model: type[DeclarativeBase],
                schema_cls: SchemaType,
            ) -> Select[Any]:
                return apply_query_modifiers(query_params, select_query, model, schema_cls)
        
        return V1Interface()


def get_query_param_schema_creator() -> Callable[[SchemaType], SchemaType]:
    """
    Get the appropriate query param schema creator based on the configured version.
    
    Returns:
        A function that creates query param schemas
    """
    if _query_modifier_version == QueryModifierVersion.V2:
        from .query_modifiers_v2 import create_query_param_schema_v2
        return create_query_param_schema_v2
    else:
        from .query_modifiers import create_query_param_schema
        return create_query_param_schema


# Convenience functions for backward compatibility
def apply_query_modifiers(
    query_params: QueryParams,
    select_query: Select[Any],
    model: type[DeclarativeBase],
    schema_cls: SchemaType,
) -> Select[Any]:
    """
    Apply query modifiers using the configured version.
    
    This is a convenience function that delegates to the appropriate implementation
    based on the global configuration.
    """
    interface = get_query_modifier_interface()
    return interface.apply_query_modifiers(query_params, select_query, model, schema_cls)


def create_query_param_schema(schema_cls: SchemaType) -> SchemaType:
    """
    Create a query param schema using the configured version.
    
    This is a convenience function that delegates to the appropriate implementation
    based on the global configuration.
    """
    creator = get_query_param_schema_creator()
    return creator(schema_cls) 