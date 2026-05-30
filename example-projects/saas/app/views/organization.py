"""Organization view."""

from fastapi import Response

import fastapi_restly as fr

from ..models import Organization
from ..schemas import OrganizationSchema
from ..schemas.organization import OrganizationCreateSchema, OrganizationUpdateSchema


class OrganizationView(fr.AsyncRestView):
    """CRUD endpoints for organizations.

    Demonstrates using different schemas per operation:
    - schema_create: Stricter validation for POST (slug format, name length)
    - schema_update: Limited fields for PATCH (only name can be updated)

    Also demonstrates a custom POST route that returns ``201 Created`` with
    a ``Location`` header pointing at the new resource — the HTTP-correct
    contract for create. The framework's generated create route returns the body
    only; replacing it lets us add the header. ``exclude_routes`` removes
    the auto-generated route so the custom one owns ``POST /``.
    """

    prefix = "/organizations"
    model = Organization
    schema = OrganizationSchema
    schema_create = OrganizationCreateSchema
    schema_update = OrganizationUpdateSchema
    exclude_routes = [fr.ViewRoute.CREATE]

    @fr.post("/", response_model=OrganizationSchema, status_code=201)
    async def create_with_location(
        self, schema_obj: OrganizationCreateSchema, response: Response
    ) -> Organization:
        """Create + 201 Created + Location header.

        Calls ``handle_create`` (not the bare ``create``) so this custom route
        runs the full request handler: ``authorize`` plus the commit bracket
        (``before_commit`` -> commit -> ``after_commit``). The framework's
        generated create route does the same thing — we only add the header.
        """
        org = await self.handle_create(schema_obj)
        response.headers["Location"] = f"{self.prefix}/{org.id}"
        return org
