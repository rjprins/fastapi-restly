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

    def build_query(self) -> sqlalchemy.Select[Any]:
        return super().build_query().where(Project.tenant_id == self.tenant_id)


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

    # Custom endpoint methods override the generated ``*_endpoint`` methods. They
    # must NOT be named after the business methods (get_many/create/...), which
    # would shadow them and make handle_<verb> -> self.<verb> recurse.
    @fr.get("/")
    async def get_many_endpoint(self, query_params: Any) -> list[ProjectRead]:
        result = await self.handle_get_many(query_params)
        return [self.to_response_schema(obj) for obj in result.objects]

    @fr.get("/{id}")
    async def get_one_endpoint(self, id: int) -> ProjectRead:
        return self.to_response_schema(await self.handle_get_one(id))

    @fr.post("/")
    async def create_endpoint(self, schema_obj: ProjectRead) -> ProjectRead:
        return self.to_response_schema(await self.handle_create(schema_obj))

    @fr.patch("/{id}")
    async def update_endpoint(self, id: int, schema_obj: ProjectRead) -> ProjectRead:
        return self.to_response_schema(await self.handle_update(id, schema_obj))

    @fr.delete("/{id}")
    async def delete_endpoint(self, id: int) -> Response:
        await self.handle_delete(id)
        return Response(status_code=204)
