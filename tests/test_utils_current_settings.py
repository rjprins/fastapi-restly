"""Contract for fr.utils.CurrentSettingsMixin."""

from typing import Any

import pytest
from pydantic import ValidationError
from pydantic_settings import BaseSettings

import fastapi_restly as fr


class Settings(fr.utils.CurrentSettingsMixin, BaseSettings):
    database_url: str = "sqlite:///real.db"
    pool_size: int = 5


def test_nothing_is_built_until_current_is_read():
    built: list[int] = []

    class Counted(Settings):
        def __init__(self, **kwargs: Any) -> None:
            built.append(1)
            super().__init__(**kwargs)

    assert built == []

    assert Counted.current.database_url == "sqlite:///real.db"
    assert built == [1]

    # Built once, then the same object every time.
    assert Counted.current is Counted.current
    assert built == [1]


def test_use_installs_an_explicit_instance():
    class Installed(Settings):
        pass

    explicit = Installed(database_url="sqlite:///explicit.db")

    assert Installed.use(explicit) is explicit
    assert Installed.current is explicit


def test_current_is_a_real_instance_not_a_proxy():
    class Real(Settings):
        pass

    assert isinstance(Real.current, Real)
    assert type(Real.current) is Real
    assert Real.current == Real()
    assert Real.current.model_dump() == {
        "database_url": "sqlite:///real.db",
        "pool_size": 5,
    }


def test_a_subclass_does_not_inherit_its_parents_instance():
    """Two settings classes must not silently share one object."""

    class Parent(Settings):
        pass

    class Child(Parent):
        pass

    Parent.use(Parent(database_url="sqlite:///parent.db"))

    assert Child.current.database_url == "sqlite:///real.db"
    assert Child.current is not Parent.current


def test_the_bookkeeping_attribute_is_not_a_pydantic_field():
    """Regression: pydantic must not adopt _current_instance as a field or
    private attribute, which would break the descriptor's __dict__ lookup."""

    class Clean(Settings):
        pass

    Clean.use(Clean())

    assert "_current_instance" not in Clean.model_fields
    assert "_current_instance" not in Clean.__private_attributes__
    assert set(Clean.current.model_dump()) == {"database_url", "pool_size"}


def test_a_missing_required_setting_raises_where_it_is_read():
    class Required(fr.utils.CurrentSettingsMixin, BaseSettings):
        must_be_set: str

    with pytest.raises(ValidationError):
        Required.current

    # The environment can be fixed and the next read retries.
    Required.use(Required(must_be_set="now set"))
    assert Required.current.must_be_set == "now set"


def test_env_is_read_at_first_use_not_at_class_definition(monkeypatch):
    class FromEnv(fr.utils.CurrentSettingsMixin, BaseSettings):
        database_url: str = "sqlite:///real.db"

    monkeypatch.setenv("DATABASE_URL", "sqlite:///from-env.db")

    assert FromEnv.current.database_url == "sqlite:///from-env.db"
