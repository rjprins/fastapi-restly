import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

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


def test_get_one_or_create_supports_custom_declarative_models():
    class Base(DeclarativeBase):
        pass

    class Widget(Base):
        __tablename__ = "widget"

        id: Mapped[int] = mapped_column(primary_key=True)
        name: Mapped[str] = mapped_column(unique=True)

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        created = fr.get_one_or_create(Widget, session, name="alpha")
        loaded = fr.get_one_or_create(Widget, session, name="alpha")

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
