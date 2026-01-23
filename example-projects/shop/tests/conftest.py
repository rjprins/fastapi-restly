# Import shop.main to set up database connection before fixtures run
import shop.main  # noqa: F401

pytest_plugins = ["fastapi_restly.pytest_fixtures"]
