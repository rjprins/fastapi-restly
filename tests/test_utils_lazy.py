"""Contract for fr.utils.lazy: what the proxy must keep doing."""

import pytest
from pydantic import ValidationError
from pydantic_settings import BaseSettings

import fastapi_restly as fr


class Settings(BaseSettings):
    database_url: str = "sqlite:///real.db"
    pool_size: int = 5


def test_nothing_is_built_until_something_is_read():
    built: list[int] = []

    class Counted(Settings):
        def __init__(self, **kwargs: object) -> None:
            built.append(1)
            super().__init__(**kwargs)

    settings = fr.utils.lazy_proxy(Counted)
    assert built == []

    assert settings.database_url == "sqlite:///real.db"
    assert built == [1]

    # Built once, then reused.
    assert settings.pool_size == 5
    assert built == [1]


def test_the_proxy_reports_the_target_type():
    settings = fr.utils.lazy_proxy(Settings)

    # isinstance() reads __class__, which the proxy answers for the target.
    assert isinstance(settings, Settings)
    # type() still sees the proxy; that is the one thing it cannot hide.
    assert type(settings) is not Settings


def test_methods_of_the_target_are_reachable():
    settings = fr.utils.lazy_proxy(Settings)

    assert settings.model_dump() == {
        "database_url": "sqlite:///real.db",
        "pool_size": 5,
    }


def test_lazy_proxy_set_installs_an_explicit_instance():
    settings = fr.utils.lazy_proxy(Settings)
    explicit = Settings(database_url="sqlite:///explicit.db")

    assert fr.utils.lazy_proxy_set(settings, explicit) is explicit
    assert settings.database_url == "sqlite:///explicit.db"


def test_a_failed_build_is_not_cached():
    class Required(BaseSettings):
        must_be_set: str

    settings = fr.utils.lazy_proxy(Required)

    with pytest.raises(ValidationError):
        settings.must_be_set

    # The environment can still be fixed and the next read retries.
    fr.utils.lazy_proxy_set(settings, Required(must_be_set="now set"))
    assert settings.must_be_set == "now set"


def test_env_read_at_first_use_not_at_import(monkeypatch):
    """Deferred construction is the point: an env var set after the proxy
    exists still reaches the settings it builds."""
    settings = fr.utils.lazy_proxy(Settings)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///from-env.db")

    assert settings.database_url == "sqlite:///from-env.db"


def test_monkeypatch_reaches_the_target_and_undo_restores_it(monkeypatch):
    """Regression: without __setattr__ forwarding, setattr lands in the proxy's
    own __dict__. Reads through the proxy would look patched while the target
    kept its value, undo would leave the shadow behind, and lazy_proxy_set() would
    silently stop taking effect."""
    settings = fr.utils.lazy_proxy(Settings)
    target = Settings()
    fr.utils.lazy_proxy_set(settings, target)

    monkeypatch.setattr(settings, "database_url", "sqlite:///patched.db")
    assert settings.database_url == "sqlite:///patched.db"
    assert target.database_url == "sqlite:///patched.db"
    assert "database_url" not in object.__getattribute__(settings, "__dict__")

    monkeypatch.undo()
    assert settings.database_url == "sqlite:///real.db"

    # A later install still wins; no shadow is sitting in front of it.
    fr.utils.lazy_proxy_set(settings, Settings(database_url="sqlite:///later.db"))
    assert settings.database_url == "sqlite:///later.db"


def test_repr_says_so_before_it_is_built():
    settings = fr.utils.lazy_proxy(Settings)
    assert "not built yet" in repr(settings)

    settings.database_url
    assert "database_url" in repr(settings)
