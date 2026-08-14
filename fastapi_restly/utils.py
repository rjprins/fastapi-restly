"""Helpers that belong to no single Restly subsystem.

:func:`lazy_proxy` defers building an object until something reads it, which
is how a module-level name can exist without the configuration it needs::

    # myapp/settings.py
    settings = fr.utils.lazy_proxy(Settings)

Importing that module now costs nothing, so ``myapp.main`` stays importable
without a configured environment, and a test suite can build the application
against a database it names itself rather than setting environment variables
before its imports. :func:`lazy_proxy_set` is how the suite hands that
explicit object over.
"""

from collections.abc import Callable as _Callable
from typing import Any as _Any
from typing import Generic as _Generic
from typing import TypeVar as _TypeVar
from typing import cast as _cast

__all__ = ["lazy_proxy", "lazy_proxy_set"]

_T = _TypeVar("_T")


class _LazyProxy(_Generic[_T]):
    """Builds its target on first use, and reports the target's type."""

    def __init__(self, factory: _Callable[..., _T]) -> None:
        object.__setattr__(self, "_factory", factory)
        object.__setattr__(self, "_built", None)

    def _target(self) -> _T:
        built = object.__getattribute__(self, "_built")
        if built is None:
            built = object.__getattribute__(self, "_factory")()
            object.__setattr__(self, "_built", built)
        return built

    # isinstance() reads __class__, so the proxy answers with the real one.
    @property  # type: ignore[misc]
    def __class__(self) -> type:  # type: ignore[override]
        return type(self._target())

    def __getattr__(self, name: str) -> _Any:
        return getattr(self._target(), name)

    # Without these, setattr lands in the proxy's own __dict__ and shadows the
    # target for good: monkeypatch would appear to work, its undo would leave
    # the shadow behind, and lazy_proxy_set() would silently stop taking effect.
    def __setattr__(self, name: str, value: _Any) -> None:
        setattr(self._target(), name, value)

    def __delattr__(self, name: str) -> None:
        delattr(self._target(), name)

    def __repr__(self) -> str:
        built = object.__getattribute__(self, "_built")
        factory = object.__getattribute__(self, "_factory")
        if built is None:
            return f"<lazy {getattr(factory, '__name__', factory)}, not built yet>"
        return repr(built)


def lazy_proxy(factory: _Callable[..., _T]) -> _T:
    """Return a lazy proxy for ``factory()``, built the first time it is used.

    The usual target is a settings object, so that importing a module costs
    nothing and the environment is only read when a value is wanted::

        # myapp/settings.py
        settings = fr.utils.lazy_proxy(Settings)

    Attribute reads, writes and ``isinstance`` all reach the built object, so
    the proxy can be passed and patched like the real one. Only ``type()`` sees
    through it.

    Proxy what is process-global and read-mostly. Do not proxy anything
    request-scoped or with a lifecycle to end, a database session above all:
    the proxy has no way to know which request it is in, and nothing would
    close what it opened.
    """
    return _cast(_T, _LazyProxy(factory))


def lazy_proxy_set(proxy: _T, instance: _T) -> _T:
    """Install an explicit instance behind a lazy proxy, and return it.

    A test that builds its own settings hands them over this way, rather than
    arranging for the factory to construct the right thing::

        fr.utils.lazy_proxy_set(settings, Settings(database_url=..., _env_file=None))
    """
    object.__setattr__(proxy, "_built", instance)
    return instance
