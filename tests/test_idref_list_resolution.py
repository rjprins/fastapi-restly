"""Regression for ticket bb6: an IDRef LIST field with a DUPLICATE id must not
404 when every referenced row exists, and a genuinely missing id must 404 with
a message that NAMES it.

The list branch resolved references with ``select(model).where(id.in_(ids))``
then compared ``len(ids) != len(rows)``. ``IN`` returns DISTINCT rows, so any
repeated id made the lengths differ and raised ``Id not found for <field>: set()``
-- a 404 that named nothing -- even though all rows existed. The fix checks
existence by set membership and names the genuinely-missing ids.

These exercise the resolver directly (sync + async), matching how this code is
already tested in test_schema_edge_coverage.py (``NotFound`` is the 404).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Mapped, Session
from sqlalchemy.pool import StaticPool

import fastapi_restly as fr
from fastapi_restly.schemas._base import (
    _async_resolve_ids_to_sqlalchemy_objects,
    _resolve_ids_to_sqlalchemy_objects,
)


@pytest.mark.asyncio
async def test_async_idref_list_duplicate_existing_resolves_missing_names_id():
    async_engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    class Bb6TagAsync(fr.IDBase):
        name: Mapped[str]

    class TagRefSchema(fr.BaseSchema):
        tags: list[fr.IDRef[Bb6TagAsync]]

    try:
        async with async_engine.begin() as conn:
            await conn.run_sync(fr.DataclassBase.metadata.create_all)

        async with AsyncSession(bind=async_engine, expire_on_commit=False) as session:
            t1, t2 = Bb6TagAsync(name="a"), Bb6TagAsync(name="b")
            session.add_all([t1, t2])
            await session.commit()

            # A duplicate of an existing id must NOT 404 (the bb6 bug).
            payload = TagRefSchema(tags=[t1.id, t1.id, t2.id])
            await _async_resolve_ids_to_sqlalchemy_objects(session, payload)
            assert {t.id for t in payload.tags} == {t1.id, t2.id}

            # A genuinely missing id 404s and NAMES the id (not the old "set()").
            missing_id = t2.id + 100
            with pytest.raises(HTTPException) as exc:
                await _async_resolve_ids_to_sqlalchemy_objects(
                    session, TagRefSchema(tags=[t1.id, missing_id])
                )
            assert str(missing_id) in str(exc.value.detail)
            assert "set()" not in str(exc.value.detail)
    finally:
        await async_engine.dispose()


def test_sync_idref_list_duplicate_existing_resolves_missing_names_id():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    class Bb6TagSync(fr.IDBase):
        name: Mapped[str]

    class TagRefSchema(fr.BaseSchema):
        tags: list[fr.IDRef[Bb6TagSync]]

    try:
        fr.DataclassBase.metadata.create_all(engine)

        with Session(bind=engine, expire_on_commit=False) as session:
            t1, t2 = Bb6TagSync(name="a"), Bb6TagSync(name="b")
            session.add_all([t1, t2])
            session.commit()

            payload = TagRefSchema(tags=[t1.id, t1.id, t2.id])
            _resolve_ids_to_sqlalchemy_objects(session, payload)
            assert {t.id for t in payload.tags} == {t1.id, t2.id}

            missing_id = t2.id + 100
            with pytest.raises(HTTPException) as exc:
                _resolve_ids_to_sqlalchemy_objects(
                    session, TagRefSchema(tags=[t1.id, missing_id])
                )
            assert str(missing_id) in str(exc.value.detail)
            assert "set()" not in str(exc.value.detail)
    finally:
        engine.dispose()
