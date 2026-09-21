"""Surviving writes keep loaded response relationships usable after rollback."""

from contextlib import asynccontextmanager
from inspect import isawaitable
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from sqlalchemy import ForeignKey, create_engine, event, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

import fastapi_restly as fr

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _call(value):
    return await value if isawaitable(value) else value


@asynccontextmanager
async def _enter(manager):
    if hasattr(manager, "__aenter__"):
        async with manager as value:
            yield value
    else:
        with manager as value:
            yield value


@pytest.fixture(params=["sync", "async"])
async def related_writes(request, tmp_path):
    class Company(fr.IDBase):
        name: Mapped[str]
        internal_note: Mapped[str] = mapped_column(default="private", deferred=True)
        owners: Mapped[list["Owner"]] = relationship(
            back_populates="company", default_factory=list
        )

    class Owner(fr.IDBase):
        name: Mapped[str]
        company_id: Mapped[int] = mapped_column(ForeignKey("company.id"))
        company: Mapped[Company] = relationship(init=False, back_populates="owners")

    class Document(fr.IDBase):
        title: Mapped[str]
        owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id"))
        owner: Mapped[Owner] = relationship(init=False)

    class NameSchema(fr.IDSchema):
        name: str

    class OwnerSchema(NameSchema):
        company: fr.ReadOnly[NameSchema]

    class DocumentSchema(fr.IDSchema):
        title: str
        owner_id: int
        owner: fr.ReadOnly[OwnerSchema]

    asynchronous = request.param == "async"
    view_base = fr.AsyncRestView if asynchronous else fr.RestView
    app = FastAPI()
    views = {}
    for model, schema in (
        (Company, NameSchema),
        (Owner, OwnerSchema),
        (Document, DocumentSchema),
    ):
        view_type = type(
            f"{model.__name__}View",
            (view_base,),
            {"model": model, "schema": schema, "prefix": f"/{model.__tablename__}"},
        )
        views[model.__name__] = fr.include_view(app)(view_type)

    database = tmp_path / "relationships.db"
    if asynchronous:
        engine = create_async_engine(f"sqlite+aiosqlite:///{database}")
        target_engine = engine.sync_engine
        make_session = lambda: AsyncSession(engine, expire_on_commit=False)
    else:
        engine = target_engine = create_engine(f"sqlite:///{database}")
        make_session = lambda: Session(engine, expire_on_commit=False)

    @event.listens_for(target_engine, "connect")
    def connect(connection, record):
        connection.isolation_level = None

    @event.listens_for(target_engine, "begin")
    def begin(connection):
        connection.exec_driver_sql("BEGIN")

    statements = []

    @event.listens_for(target_engine, "before_cursor_execute")
    def record_statement(connection, cursor, statement, parameters, context, many):
        statements.append(statement)

    try:
        if asynchronous:
            async with engine.begin() as connection:
                await connection.run_sync(fr.DataclassBase.metadata.create_all)
        else:
            fr.DataclassBase.metadata.create_all(engine)

        async with _enter(make_session()) as session:
            company = Company(name="Initech")
            session.add(company)
            await _call(session.flush())
            owner = Owner(name="Alice", company_id=company.id)
            colleague = Owner(name="Bob", company_id=company.id)
            session.add(owner)
            session.add(colleague)
            await _call(session.commit())
            owner_id = owner.id
            colleague_id = colleague.id
            company_id = company.id
            session.expunge_all()
            yield SimpleNamespace(
                document_view=views["Document"](request=None, session=session),
                owner_view=views["Owner"](request=None, session=session),
                company_view=views["Company"](request=None, session=session),
                owner_id=owner_id,
                colleague_id=colleague_id,
                company_id=company_id,
                document_model=Document,
                owner_model=Owner,
                company_model=Company,
                session=session,
                make_session=make_session,
                statements=statements,
                asynchronous=asynchronous,
            )
    finally:
        await _call(engine.dispose())


@pytest.mark.parametrize("related_model", ["owner", "company"])
@pytest.mark.parametrize("read_in_after_hook", [False, True])
async def test_savepoint_rollback_keeps_related_response_objects_readable(
    related_writes, monkeypatch, related_model, read_in_after_hook
):
    writes = related_writes
    view = writes.document_view
    after_names = []

    def after(action, new, old=None):
        after_names.append((new.owner.name, new.owner.company.name))

    async def async_after(action, new, old=None):
        after(action, new, old)

    if read_in_after_hook:
        monkeypatch.setattr(
            view, "after_action_commit", async_after if writes.asynchronous else after
        )

    async with _enter(view.shared_write_action_commit()):
        document = await _call(
            view.handle_create(
                view.schema_create(title="survivor", owner_id=writes.owner_id)
            )
        )
        assert {"internal_note", "owners"} <= inspect(document.owner.company).unloaded
        with pytest.raises(ValueError, match="discard related update"):
            async with _enter(writes.session.begin_nested()):
                related_view = getattr(writes, f"{related_model}_view")
                await _call(
                    related_view.handle_update(
                        getattr(writes, f"{related_model}_id"),
                        related_view.schema_update(name="discarded"),
                    )
                )
                raise ValueError("discard related update")

    writes.statements.clear()
    body = view.to_response(document).model_dump(mode="json")
    assert body["title"] == "survivor"
    assert body["owner"]["name"] == "Alice"
    assert body["owner"]["company"]["name"] == "Initech"
    assert writes.statements == []
    assert {"internal_note", "owners"} <= inspect(document.owner.company).unloaded
    assert after_names == ([("Alice", "Initech")] if read_in_after_hook else [])

    async with _enter(writes.make_session()) as session:
        assert await _call(session.scalar(select(writes.document_model.title))) == (
            "survivor"
        )
        assert (
            await _call(
                session.scalar(
                    select(writes.owner_model.name).where(
                        writes.owner_model.id == writes.owner_id
                    )
                )
            )
            == "Alice"
        )
        assert await _call(session.scalar(select(writes.company_model.name))) == (
            "Initech"
        )


async def test_savepoint_rollback_recovers_collection_members_in_a_loaded_cycle(
    related_writes, monkeypatch
):
    writes = related_writes
    view = writes.document_view
    after_names = []

    def before(action, new, old=None):
        writes.session.refresh(new.owner.company, attribute_names=["owners"])

    async def async_before(action, new, old=None):
        await writes.session.refresh(new.owner.company, attribute_names=["owners"])

    def after(action, new, old=None):
        after_names.append(sorted(owner.name for owner in new.owner.company.owners))

    async def async_after(action, new, old=None):
        after(action, new, old)

    monkeypatch.setattr(
        view, "before_action_commit", async_before if writes.asynchronous else before
    )
    monkeypatch.setattr(
        view, "after_action_commit", async_after if writes.asynchronous else after
    )

    async with _enter(view.shared_write_action_commit()):
        document = await _call(
            view.handle_create(
                view.schema_create(title="survivor", owner_id=writes.owner_id)
            )
        )
        company = document.owner.company
        assert any(owner is document.owner for owner in company.owners)
        colleague = next(owner for owner in company.owners if owner.name == "Bob")
        assert "company" in inspect(colleague).unloaded
        with pytest.raises(ValueError, match="discard collection member update"):
            async with _enter(writes.session.begin_nested()):
                await _call(
                    writes.owner_view.handle_update(
                        writes.colleague_id,
                        writes.owner_view.schema_update(name="discarded"),
                    )
                )
                raise ValueError("discard collection member update")

    assert after_names == [["Alice", "Bob"]]
    writes.statements.clear()
    assert view.to_response(document).owner.company.name == "Initech"
    assert sorted(owner.name for owner in company.owners) == ["Alice", "Bob"]
    assert writes.statements == []
    assert "company" in inspect(colleague).unloaded
    assert "internal_note" in inspect(company).unloaded


async def test_shared_write_without_rollback_does_not_reload_related_objects(
    related_writes,
):
    writes = related_writes
    view = writes.document_view
    async with _enter(view.shared_write_action_commit()):
        document = await _call(
            view.handle_create(
                view.schema_create(title="survivor", owner_id=writes.owner_id)
            )
        )
        writes.statements.clear()

    assert writes.statements == []
    assert view.to_response(document).owner.name == "Alice"
    assert writes.statements == []
    assert {"internal_note", "owners"} <= inspect(document.owner.company).unloaded
