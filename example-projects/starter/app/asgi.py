"""The ASGI application. Only servers import this, so main.py stays import-safe."""

from .main import create_app

app = create_app()
