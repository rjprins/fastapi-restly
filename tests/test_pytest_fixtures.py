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

pytest_plugins = ["fastapi_restly.testing._fixtures"]


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
async def test_async_session_without_sync_sessionmaker(async_session):
    conn = await async_session.connection()
    await conn.run_sync(fr.DataclassBase.metadata.create_all)

    widget = Widget(name="alpha")
    async_session.add(widget)
    await async_session.commit()

    fetched = await async_session.get(Widget, widget.id)
    assert fetched is not None
    assert fetched.name == "alpha"
"""
    )

    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=1)
