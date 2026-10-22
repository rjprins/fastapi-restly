"""A column_property is a column for the list params: it gets filters and a
sort key, as docs/filtering.md says."""

from fastapi import FastAPI
from sqlalchemy import ForeignKey, func, select
from sqlalchemy.orm import Mapped, column_property, mapped_column

import fastapi_restly as fr
from fastapi_restly.testing import RestlyTestClient

from ._models import SharedBase
from .conftest import create_tables


def _register(app, view_base):
    class Writer(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class Novel(fr.IDMixin, SharedBase):
        title: Mapped[str]
        writer_id: Mapped[int] = mapped_column(ForeignKey("writer.id"))

    Writer.novel_count = column_property(
        select(func.count(Novel.id))
        .where(Novel.writer_id == Writer.id)
        .correlate_except(Novel)
        .scalar_subquery()
    )

    class WriterSchema(fr.IDSchema):
        name: str
        novel_count: fr.ReadOnly[int] = 0

    class NovelSchema(fr.IDSchema):
        title: str
        writer_id: int

    for prefix, model, schema in (
        ("/writers", Writer, WriterSchema),
        ("/novels", Novel, NovelSchema),
    ):
        attributes = {"prefix": prefix, "model": model, "schema": schema}
        fr.include_view(app, type(f"{model.__name__}View", (view_base,), attributes))


def _assert_the_list_params(client):
    ids = {}
    for name, novels in (("ann", 2), ("bob", 0), ("cy", 1)):
        ids[name] = client.post("/writers", json={"name": name}).json()["id"]
        for number in range(novels):
            client.post("/novels", json={"title": f"{number}", "writer_id": ids[name]})

    by_count = client.get("/writers", params={"sort": "-novel_count"}).json()["data"]
    assert [(row["name"], row["novel_count"]) for row in by_count] == [
        ("ann", 2),
        ("cy", 1),
        ("bob", 0),
    ]
    some = client.get("/writers", params={"novel_count__gte": 1}).json()
    assert sorted(row["name"] for row in some["data"]) == ["ann", "cy"]
    assert some["total_count"] == 2


def test_a_column_property_gets_filters_and_a_sort_key_async(client):
    _register(client.app, fr.AsyncRestView)
    create_tables()
    _assert_the_list_params(client)


def test_a_column_property_gets_filters_and_a_sort_key_sync(sync_db):
    engine, _ = sync_db
    app = FastAPI()
    _register(app, fr.RestView)
    SharedBase.metadata.create_all(engine)
    _assert_the_list_params(RestlyTestClient(app))
