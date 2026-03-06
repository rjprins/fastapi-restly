"""Tests for context-local global state isolation."""

import fastapi_restly as fr
from fastapi_restly.db import fr_globals
from fastapi_restly.db._globals import FRGlobals
from fastapi_restly.query import QueryModifierVersion


def test_use_fr_globals_scopes_database_state_to_context():
    original_url = fr_globals.database_url

    globals_a = FRGlobals()
    globals_a.database_url = "sqlite:///a.db"

    globals_b = FRGlobals()
    globals_b.database_url = "sqlite:///b.db"

    with fr.use_fr_globals(globals_a):
        assert fr_globals.database_url == "sqlite:///a.db"
        with fr.use_fr_globals(globals_b):
            assert fr_globals.database_url == "sqlite:///b.db"
        assert fr_globals.database_url == "sqlite:///a.db"

    assert fr_globals.database_url == original_url


def test_use_query_modifier_version_scopes_version_to_context():
    fr.set_query_modifier_version(QueryModifierVersion.V1)

    with fr.use_query_modifier_version(QueryModifierVersion.V2):
        assert fr.get_query_modifier_version() == QueryModifierVersion.V2

    assert fr.get_query_modifier_version() == QueryModifierVersion.V1
