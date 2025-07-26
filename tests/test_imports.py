"""Test that all imports work correctly."""

import fastapi_ding as fa
from fastapi_ding import (
    AsyncAlchemyView,
    BaseSchema,
    IDBase,
    IDSchema,
    SQLBase,
    TimestampsMixin,
    include_view,
    setup_async_database_connection,
    settings,
)
from fastapi_ding import schemas, sqlbase


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
