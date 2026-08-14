"""Import every model module so SQLAlchemy and Alembic see complete metadata."""

from . import outbox
from .labels import model as labels
from .lookups import model as lookups
from .organizations import model as organizations
from .projects import model as projects
from .tasks import model as tasks
from .uploads import model as uploads
from .users import model as users

__all__ = [
    "labels",
    "lookups",
    "organizations",
    "outbox",
    "projects",
    "tasks",
    "uploads",
    "users",
]
