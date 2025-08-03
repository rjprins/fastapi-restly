"""Test that all imports work correctly."""

import fastapi_ding as fd
from fastapi_ding import (
    AsyncAlchemyView,
    BaseSchema,
    IDBase,
    IDSchema,
    TimestampsMixin,
    include_view,
    setup_async_database_connection,
    settings,
    Base,
)
from fastapi_ding import _schemas as schemas, _sqlbase as sqlbase


def test_imports_work():
    """Test that all main imports work."""
    assert fd.AsyncAlchemyView is not None
    assert fd.IDBase is not None
    assert fd.IDSchema is not None
    assert fd.TimestampsMixin is not None
    assert fd.include_view is not None
    assert fd.setup_async_database_connection is not None
    assert fd.settings is not None
    assert fd.Base is not None


def test_schemas_module():
    """Test that schemas module can be imported."""
    assert schemas is not None
    # Test that we can access schemas from the module
    assert hasattr(schemas, 'BaseSchema')


def test_sqlbase_module():
    """Test that sqlbase module can be imported."""
    assert sqlbase is not None
    # Test that we can access sqlbase from the module
    assert hasattr(sqlbase, 'Base')
