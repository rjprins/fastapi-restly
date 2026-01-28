"""Organization view."""

import fastapi_restly as fr

from ..models import Organization
from ..schemas import OrganizationSchema
from ..schemas.organization import OrganizationCreateSchema, OrganizationUpdateSchema


class OrganizationView(fr.AsyncAlchemyView):
    """CRUD endpoints for organizations.

    Demonstrates using different schemas per operation:
    - creation_schema: Stricter validation for POST (slug format, name length)
    - update_schema: Limited fields for PATCH (only name can be updated)
    """

    prefix = "/organizations"
    model = Organization
    schema = OrganizationSchema
    creation_schema = OrganizationCreateSchema
    update_schema = OrganizationUpdateSchema
