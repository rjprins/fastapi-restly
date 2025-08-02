"""Pytest configuration and shared fixtures."""

import pytest

import fastapi_ding as fd

pytest_plugins = ["fastapi_ding.pytest_fixtures"]


@pytest.fixture(autouse=True)
def reset_metadata():
    """Reset SQLAlchemy metadata to prevent table redefinition conflicts."""
    fd.SQLBase.metadata.clear()
