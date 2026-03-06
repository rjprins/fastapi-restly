from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import FastAPI, Response
from pydantic import BaseModel
from sqlalchemy import Uuid
from sqlalchemy.orm import Mapped

import fastapi_restly as fr


class UUIDModel(fr.Base):
    __tablename__ = "uuid_model"

    id: Mapped[UUID] = fr.mapped_column(
        Uuid, primary_key=True, default_factory=uuid4
    )
    name: Mapped[str]


class UUIDSchema(BaseModel):
    id: UUID
    name: str


class UUIDView(fr.AsyncAlchemyView):
    prefix = "/uuid-models"
    model = UUIDModel
    schema = UUIDSchema
    id_type = UUID

    async def process_get(self, id: UUID):
        return SimpleNamespace(id=id, name="demo")

    async def process_patch(self, id: UUID, schema_obj: BaseModel):
        return SimpleNamespace(id=id, name=getattr(schema_obj, "name", "demo"))

    async def process_delete(self, id: UUID):
        return Response(status_code=204)


def test_view_id_type_controls_openapi_path_parameter():
    app = FastAPI()
    fr.include_view(app, UUIDView)

    parameter_schema = app.openapi()["paths"]["/uuid-models/{id}"]["get"]["parameters"][0]["schema"]
    assert parameter_schema["type"] == "string"
    assert parameter_schema["format"] == "uuid"
