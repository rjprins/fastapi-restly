import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Mapped, Session, mapped_column

import fastapi_restly as fr


def test_get_one_or_create_supports_dataclass_models():
    class Widget(fr.IDBase):
        name: Mapped[str] = mapped_column(unique=True)

    engine = create_engine("sqlite+pysqlite:///:memory:")
    fr.DataclassBase.metadata.create_all(engine)

    with Session(engine) as session:
        created = fr.get_one_or_create(Widget, session, name="alpha")
        loaded = fr.get_one_or_create(Widget, session, name="alpha")

        assert loaded.id == created.id
        assert loaded.name == "alpha"


def test_get_one_or_create_supports_plain_models():
    class PlainWidget(fr.PlainIDBase):
        name: Mapped[str] = mapped_column(unique=True)

    engine = create_engine("sqlite+pysqlite:///:memory:")
    fr.PlainBase.metadata.create_all(engine)

    with Session(engine) as session:
        created = fr.get_one_or_create(PlainWidget, session, name="alpha")
        loaded = fr.get_one_or_create(PlainWidget, session, name="alpha")

        assert loaded.id == created.id
        assert loaded.name == "alpha"


@pytest.mark.asyncio
async def test_async_get_one_or_create_supports_dataclass_models():
    class AsyncWidget(fr.IDBase):
        name: Mapped[str] = mapped_column(unique=True)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(fr.DataclassBase.metadata.create_all)

    async with AsyncSession(bind=engine) as session:
        created = await fr.async_get_one_or_create(AsyncWidget, session, name="alpha")
        loaded = await fr.async_get_one_or_create(AsyncWidget, session, name="alpha")

        assert loaded.id == created.id
        assert loaded.name == "alpha"
