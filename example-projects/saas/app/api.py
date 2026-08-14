"""Compose the domain views onto the FastAPI application.

Keeping registration here rather than in ``main.py`` is organization, not
contract. Tools that need complete metadata import ``main`` instead, which
reaches this module and through it every view and model.
"""

from fastapi import FastAPI

import fastapi_restly as fr

from .countries.views import CountryView
from .labels.views import LabelView, TaskLabelView
from .organizations.views import OrganizationView
from .projects.views import ProjectView
from .tasks.views import TaskView
from .uploads.views import UploadView
from .users.views import UserView


def register_views(app: FastAPI) -> None:
    """Register every domain view at the application composition boundary."""
    for view in (
        OrganizationView,
        UserView,
        ProjectView,
        TaskView,
        LabelView,
        TaskLabelView,
        UploadView,
        CountryView,
    ):
        fr.include_view(app, view)
