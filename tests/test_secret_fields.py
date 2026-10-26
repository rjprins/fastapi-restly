"""A field of Pydantic's secret types, such as ``SecretStr``.

A write stores the secret, and a response masks it, as Pydantic does. Like a
WriteOnly field, it gets no filter and is no sort key: a filter or a sort on
it would let a client find out the secret.
"""

import json

from fastapi import FastAPI
from pydantic import Secret, SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr
from fastapi_restly.testing import RestlyTestClient

from ._models import SharedBase


def _models():
    class SecretHolder(fr.IDMixin, SharedBase):
        name: Mapped[str]
        token: Mapped[str]
        pin: Mapped[int | None] = mapped_column(default=None)

    class SecretHolderSchema(fr.IDSchema):
        name: str
        token: SecretStr
        pin: Secret[int] | None = None

    return SecretHolder, SecretHolderSchema


def test_a_write_stores_the_secret_and_a_response_masks_it(sync_db):
    engine, make_session = sync_db
    SecretHolder, SecretHolderSchema = _models()

    class SecretHolderView(fr.RestView):
        prefix = "/holders"
        model = SecretHolder
        schema = SecretHolderSchema

    app = FastAPI()
    fr.include_view(app, SecretHolderView)
    SharedBase.metadata.create_all(engine)
    client = RestlyTestClient(app)

    created = client.post("/holders", json={"name": "a", "token": "zzz", "pin": 1234})
    assert created.json() == {
        "id": 1,
        "name": "a",
        "token": "**********",
        "pin": "**********",
    }
    client.patch("/holders/1", json={"token": "yyy"})
    with make_session() as session:
        stored = session.execute(select(SecretHolder.token, SecretHolder.pin)).one()
    assert tuple(stored) == ("yyy", 1234)

    keys = SecretHolderView.schema_list_params.model_fields
    assert not [key for key in keys if key.startswith(("token", "pin"))]
    client.get("/holders?token=yyy", assert_status_code=422)
    client.get("/holders?pin__gte=1000", assert_status_code=422)
    response = client.get("/holders?sort=token", assert_status_code=422)
    assert response.json()["detail"][0]["msg"] == "Unknown sort key 'token'"


def test_a_react_admin_view_does_not_filter_or_sort_on_a_secret(sync_db):
    engine, _ = sync_db
    SecretHolder, SecretHolderSchema = _models()

    class SecretHolderAdminView(fr.views.ReactAdminView):
        prefix = "/admin/holders"
        model = SecretHolder
        schema = SecretHolderSchema

    app = FastAPI()
    fr.include_view(app, SecretHolderAdminView)
    SharedBase.metadata.create_all(engine)
    client = RestlyTestClient(app)

    response = client.get(
        "/admin/holders/",
        params={"filter": json.dumps({"token": "zzz"})},
        assert_status_code=400,
    )
    assert response.json()["detail"] == "Unknown filter key: 'token'"
    response = client.get(
        "/admin/holders/",
        params={"sort": json.dumps(["token", "ASC"])},
        assert_status_code=400,
    )
    assert response.json()["detail"] == "Unknown sort key: 'token'"
