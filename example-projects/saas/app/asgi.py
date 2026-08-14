"""ASGI entrypoint: ``uvicorn app.asgi:app`` and the FastAPI CLI load this.

Only the server imports this module. Importing it builds the application, which
requires a configured environment. Everything else imports ``main``, which does
not, so the test suite can name its own database and Alembic can read metadata
without one.
"""

from .main import create_app

app = create_app()
