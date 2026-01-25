"""Organization view."""

import fastapi_restly as fr

from ..models import Organization
from ..schemas import OrganizationSchema


class OrganizationView(fr.AsyncAlchemyView):
    """CRUD endpoints for organizations."""

    prefix = "/organizations"
    model = Organization
    schema = OrganizationSchema
