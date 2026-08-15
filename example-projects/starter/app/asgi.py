"""The application object servers import.

Only the server imports this module. Keeping the object out of ``main.py`` is
what lets Alembic and the test suite import ``main`` without building an
application, and therefore without a configured environment.
"""

from .main import create_app

app = create_app()
