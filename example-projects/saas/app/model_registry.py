"""Import every model module so SQLAlchemy and Alembic see complete metadata."""

from . import outbox
from .countries import models as countries
from .labels import models as labels
from .organizations import models as organizations
from .projects import models as projects
from .tasks import models as tasks
from .uploads import models as uploads
from .users import models as users

__all__ = [
    "countries",
    "labels",
    "organizations",
    "outbox",
    "projects",
    "tasks",
    "uploads",
    "users",
]
