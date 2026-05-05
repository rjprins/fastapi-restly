import pytest

pytest_plugins = ["pytester"]


def test_async_session_fixture_supports_async_only_projects(pytester: pytest.Pytester):
    pytester.makefile(
        ".toml",
        pyproject="""
[tool.pytest.ini_options]
asyncio_default_fixture_loop_scope = "function"
""",
    )
    pytester.makeconftest(
        """
import pytest
import fastapi_restly as fr

pytest_plugins = ["fastapi_restly.pytest_fixtures"]


@pytest.fixture(autouse=True)
def setup_async_db():
    fr.configure(async_database_url="sqlite+aiosqlite:///:memory:")
"""
    )

    pytester.makepyfile(
        """
import pytest
import fastapi_restly as fr
from sqlalchemy.orm import Mapped


class Widget(fr.IDBase):
    name: Mapped[str]


@pytest.mark.asyncio
async def test_async_session_without_sync_sessionmaker(restly_async_session):
    conn = await restly_async_session.connection()
    await conn.run_sync(fr.DataclassBase.metadata.create_all)

    widget = Widget(name="alpha")
    restly_async_session.add(widget)
    await restly_async_session.commit()

    fetched = await restly_async_session.get(Widget, widget.id)
    assert fetched is not None
    assert fetched.name == "alpha"
"""
    )

    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=1)


def test_public_plugin_uses_prefixed_fixture_names(pytester: pytest.Pytester):
    pytester.makefile(
        ".toml",
        pyproject="""
[tool.pytest.ini_options]
asyncio_default_fixture_loop_scope = "function"
""",
    )
    pytester.makeconftest(
        """
pytest_plugins = ["fastapi_restly.pytest_fixtures"]
"""
    )
    pytester.makepyfile(
        """
def test_fixture_names(pytestconfig):
    fixture_names = set(
        pytestconfig.pluginmanager.get_plugin("funcmanage")._arg2fixturedefs
    )

    assert {
        "restly_app",
        "restly_client",
        "restly_session",
        "restly_async_session",
        "restly_project_root",
    } <= fixture_names
    assert {
        "app",
        "client",
        "session",
        "async_session",
        "project_root",
        "autouse_alembic_upgrade",
        "autouse_savepoint_only_mode_sessions",
    }.isdisjoint(fixture_names)
"""
    )

    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=1)
