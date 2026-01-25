"""Organization schema."""

import fastapi_restly as fr

from ..models import Organization


class OrganizationSchema(fr.TimestampsSchemaMixin, fr.IDSchema[Organization]):
    """Schema for Organization model."""

    name: str
    slug: str
