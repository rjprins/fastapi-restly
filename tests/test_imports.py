"""Test that all imports work correctly."""

import fastapi_restly as fr
from fastapi_restly import (
    AsyncAlchemyView,
    BaseSchema,
    DataclassBase,
    IDBase,
    IDSchema,
    TimestampsMixin,
    configure,
    include_view,
    schemas,
)
from fastapi_restly import models as sqlbase


def test_imports_work():
    """Test that all main imports work."""
    assert fr.AsyncAlchemyView is not None
    assert fr.IDBase is not None
    assert fr.IDSchema is not None
    assert fr.TimestampsMixin is not None
    assert fr.include_view is not None
    assert fr.configure is not None
    assert fr.DataclassBase is not None


def test_schemas_module():
    """Test that schemas module can be imported."""
    assert schemas is not None
    # Test that we can access schemas from the module
    assert hasattr(schemas, 'BaseSchema')


def test_sqlbase_module():
    """Test that sqlbase module can be imported."""
    assert sqlbase is not None
    # Test that we can access sqlbase from the module
    assert hasattr(sqlbase, 'DataclassBase')
