"""The declarative base that the framework tests share.

Tests declare models on it in function locals, so the autouse
``reset_metadata`` fixture in ``conftest.py`` scrubs its registry and clears
its metadata between tests.
"""

from sqlalchemy.orm import DeclarativeBase

import fastapi_restly as fr


class SharedBase(fr.ModelDefaults, DeclarativeBase):
    pass
