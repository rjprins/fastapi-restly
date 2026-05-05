from typing import Annotated, Any

import sqlalchemy
from fastapi import Depends, FastAPI, Response
from sqlalchemy.orm import Mapped

import fastapi_restly as fr

app = FastAPI()


def current_tenant_id() -> int:
    return 1


class Project(fr.IDBase):
    tenant_id: Mapped[int]
    name: Mapped[str]


class ProjectRead(fr.IDSchema[Project]):
    tenant_id: int
    name: str


class TenantScopedView(fr.AsyncRestView[Project, ProjectRead, ProjectRead, ProjectRead, int]):
    prefix = "/tenants"
    model = Project
    schema = ProjectRead
    tenant_id: Annotated[int, Depends(current_tenant_id)]

    def build_listing_query(self) -> sqlalchemy.Select[Any]:
        return super().build_listing_query().where(Project.tenant_id == self.tenant_id)


@fr.include_view(app)
class ProjectView(TenantScopedView):
    prefix = "/projects"


@fr.include_view(app)
class CustomProjectView(
    fr.AsyncRestView[Project, ProjectRead, ProjectRead, ProjectRead, int]
):
    prefix = "/custom-projects"
    model = Project
    schema = ProjectRead

    @fr.get("/")
    async def listing(self, query_params: Any) -> list[ProjectRead]:
        objs = await self.handle_listing(query_params)
        return [self.to_response_schema(obj) for obj in objs]

    @fr.get("/{id}")
    async def retrieve(self, id: int) -> ProjectRead:
        return self.to_response_schema(await self.handle_retrieve(id))

    @fr.post("/")
    async def create(self, schema_obj: ProjectRead) -> ProjectRead:
        return self.to_response_schema(await self.handle_create(schema_obj))

    @fr.patch("/{id}")
    async def update(self, id: int, schema_obj: ProjectRead) -> ProjectRead:
        return self.to_response_schema(await self.handle_update(id, schema_obj))

    @fr.delete("/{id}")
    async def destroy(self, id: int) -> Response:
        return await self.handle_destroy(id)
