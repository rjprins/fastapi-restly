"""Project view."""

import fastapi_restly as fr

from ..models import Project
from ..schemas import ProjectSchema


class ProjectView(fr.AsyncAlchemyView):
    """CRUD endpoints for projects."""

    prefix = "/projects"
    model = Project
    schema = ProjectSchema
