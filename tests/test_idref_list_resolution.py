"""Regression tests for IDRef LIST resolution.

Duplicates: a DUPLICATE id must not 404 when all referenced rows exist; a
missing id must 404 naming it. (The old code compared ``len(ids) != len(rows)``,
but ``IN`` returns DISTINCT rows, so any repeat raised ``Id not found: set()``.)

Ordering: the resolved list follows the CLIENT's order, not the ``IN`` query's
DB/PK order -- the resolver builds an id -> row map and rebuilds in first-appearance
order (deduped), which also subsumes the existence check.

Exercises the resolver directly (sync + async), as in
test_schema_edge_coverage.py (``NotFound`` is the 404).
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
async def test_async_idref_list_resolution_order_dedup_and_missing():
    async_engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    class ListResTagAsync(fr.IDBase):
        name: Mapped[str]

    class TagRefSchema(fr.BaseSchema):
        tags: list[fr.IDRef[ListResTagAsync]]

    try:
        async with async_engine.begin() as conn:
            await conn.run_sync(fr.DataclassBase.metadata.create_all)

        async with AsyncSession(bind=async_engine, expire_on_commit=False) as session:
            t1, t2 = ListResTagAsync(name="a"), ListResTagAsync(name="b")
            session.add_all([t1, t2])
            await session.commit()

            # Duplicate-of-existing must NOT 404; the client's order is kept and
            # deduped: [t2, t2, t1] -> [t2, t1], not the IN/PK order. The
            # resolver returns a {field: resolved} mapping (it no longer mutates
            # the payload).
            payload = TagRefSchema(tags=[t2.id, t2.id, t1.id])
            resolved = await _async_resolve_ids_to_sqlalchemy_objects(session, payload)
            assert [t.id for t in resolved["tags"]] == [t2.id, t1.id]
            # The request model keeps its wire shape (still IDRefs).
            assert all(isinstance(t, fr.IDRef) for t in payload.tags)

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


def test_sync_idref_list_resolution_order_dedup_and_missing():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    class ListResTagSync(fr.IDBase):
        name: Mapped[str]

    class TagRefSchema(fr.BaseSchema):
        tags: list[fr.IDRef[ListResTagSync]]

    try:
        fr.DataclassBase.metadata.create_all(engine)

        with Session(bind=engine, expire_on_commit=False) as session:
            t1, t2 = ListResTagSync(name="a"), ListResTagSync(name="b")
            session.add_all([t1, t2])
            session.commit()

            # Duplicate-of-existing must NOT 404; the client's order is kept and
            # deduped: [t2, t2, t1] -> [t2, t1], not the IN/PK order. The
            # resolver returns a {field: resolved} mapping (it no longer mutates
            # the payload).
            payload = TagRefSchema(tags=[t2.id, t2.id, t1.id])
            resolved = _resolve_ids_to_sqlalchemy_objects(session, payload)
            assert [t.id for t in resolved["tags"]] == [t2.id, t1.id]
            # The request model keeps its wire shape (still IDRefs).
            assert all(isinstance(t, fr.IDRef) for t in payload.tags)

            missing_id = t2.id + 100
            with pytest.raises(HTTPException) as exc:
                _resolve_ids_to_sqlalchemy_objects(
                    session, TagRefSchema(tags=[t1.id, missing_id])
                )
            assert str(missing_id) in str(exc.value.detail)
            assert "set()" not in str(exc.value.detail)
    finally:
        engine.dispose()
