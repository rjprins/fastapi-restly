"""Project schema."""

import fastapi_restly as fr

from ..models import Project, ProjectStatus


class ProjectSchema(fr.TimestampsSchemaMixin, fr.IDSchema[Project]):
    """Schema for Project model."""

    name: str
    description: str = ""
    status: ProjectStatus = ProjectStatus.ACTIVE
    organization_id: int
