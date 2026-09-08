"""Organization view."""

from fastapi import Response

import fastapi_restly as fr

from .models import Organization
from .schemas import (
    OrganizationCreateSchema,
    OrganizationSchema,
    OrganizationUpdateSchema,
)


class OrganizationView(fr.AsyncRestView):
    """CRUD endpoints for organizations.

    Demonstrates using different schemas per operation:
    - schema_create: Stricter validation for POST (slug format, name length)
    - schema_update: Limited fields for PATCH (only name can be updated)

    Also demonstrates a custom POST route that returns ``201 Created`` with a
    ``Location`` header pointing at the new resource. The default create route
    returns only the body. ``exclude_routes`` removes it so the custom route
    owns ``POST /``.
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
        (``before_action_commit`` -> commit -> ``after_action_commit``). The default create
        endpoint method does the same thing. This method adds the header.
        """
        org = await self.handle_create(schema_obj)
        response.headers["Location"] = f"{self.prefix}/{org.id}"
        return org
