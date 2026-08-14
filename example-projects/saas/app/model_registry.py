"""Import every model so SQLAlchemy and Alembic see complete metadata."""

from .labels.model import Label, TaskLabel
from .lookups.model import Country
from .organizations.model import Organization
from .outbox import OutboxEvent
from .projects.model import Project, ProjectStatus
from .tasks.model import Task, TaskPriority, TaskStatus, TaskType
from .uploads.model import Upload, UploadLine
from .users.model import User, UserRole

__all__ = [
    "Organization",
    "User",
    "UserRole",
    "Project",
    "ProjectStatus",
    "Task",
    "TaskStatus",
    "TaskPriority",
    "TaskType",
    "Label",
    "TaskLabel",
    "OutboxEvent",
    "Upload",
    "UploadLine",
    "Country",
]
