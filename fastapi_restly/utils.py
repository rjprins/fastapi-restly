"""Helpers that belong to no single Restly subsystem."""

from typing import Any as _Any
from typing import TypeVar as _TypeVar

__all__ = ["CurrentSettingsMixin"]

_T = _TypeVar("_T")


class _Current:
    """Descriptor returning the owner class's one instance, built on first read."""

    def __get__(self, obj: _Any, cls: type[_T]) -> _T:
        # __dict__, not getattr: a subclass must not inherit its parent's
        # instance, or two settings classes would silently share one object.
        instance = cls.__dict__.get("_current_instance")
        if instance is None:
            instance = cls()
            cls._current_instance = instance  # type: ignore[attr-defined]
        return instance


class CurrentSettingsMixin:
    """Give a settings class one shared instance, built the first time it is read.

    Mixed into a ``pydantic_settings.BaseSettings`` subclass, it replaces the
    module-level instance that would otherwise make importing your application
    require a configured environment::

        # myapp/settings.py
        class Settings(fr.utils.CurrentSettingsMixin, BaseSettings):
            database_url: str

    ``Settings.current`` builds the instance on first read and returns the same
    one after that, so nothing is constructed at import and ``myapp.main`` stays
    importable without configuration. Alembic and the test suite rely on that.

    ``Settings.use(...)`` installs an instance instead of building one, which is
    how a test suite hands over settings it constructed itself::

        # tests/conftest.py
        Settings.use(Settings(database_url=..., _env_file=None))

    ``current`` and ``use`` become reserved names on the class: a settings field
    may not use either.
    """

    @classmethod
    def use(cls: type[_T], instance: _T) -> _T:
        """Install an explicit instance as ``current``, and return it."""
        cls._current_instance = instance  # type: ignore[attr-defined]
        return instance

    current = _Current()
