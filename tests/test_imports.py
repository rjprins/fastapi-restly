"""Test that all imports work correctly."""

import fastapi_alchemy as fa
from fastapi_alchemy import (
    AsyncAlchemyView,
    BaseSchema,
    SQLBase,
    include_view,
    schemas,
    settings,
    sqlbase,
)


def test_imports_work():
    """Test that all main imports work correctly."""
    assert fa.AsyncAlchemyView is AsyncAlchemyView
    assert fa.BaseSchema is BaseSchema
    assert fa.SQLBase is SQLBase
    assert fa.include_view is include_view
    assert fa.settings is settings


def test_schemas_module():
    """Test that schemas module is accessible."""
    assert hasattr(schemas, "BaseSchema")
    assert hasattr(schemas, "IDSchema")
    assert hasattr(schemas, "TimestampsSchemaMixin")


def test_sqlbase_module():
    """Test that sqlbase module is accessible."""
    assert hasattr(sqlbase, "SQLBase")
    assert hasattr(sqlbase, "IDBase")
    assert hasattr(sqlbase, "TimestampsMixin")
