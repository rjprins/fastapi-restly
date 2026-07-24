"""fznb.7 -- the conftest registry scrub fails loudly on SQLAlchemy churn.

``_cleanup_registry`` walks SQLAlchemy's private declarative-registry internals
to drop test-local model registrations between tests. These tests pin the
guarantee that if SA moves or renames one of those internals, the scrub raises a
clear error naming what moved -- instead of going silently inert and letting the
SAWarning flood return.
"""

import types

import pytest
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm.clsregistry import _ModuleMarker

import fastapi_restly as fr

from .conftest import _SA_MODULE_REGISTRY_KEY, _cleanup_registry


def _fake_base(class_registry: dict) -> type:
    """A stand-in base whose ``registry._class_registry`` is ``class_registry``."""
    registry = types.SimpleNamespace(_class_registry=class_registry)
    return type("FakeBase", (), {"registry": registry})


def test_wrong_module_registry_type_raises():
    """A non-_ModuleMarker under the module-registry key is a structural change."""
    base = _fake_base({_SA_MODULE_REGISTRY_KEY: object()})
    with pytest.raises(RuntimeError, match="_ModuleMarker"):
        _cleanup_registry(base)


def test_classes_without_module_registry_raise():
    """Class entries but no module-registry key means SA moved the module tree."""
    base = _fake_base({"Widget": type("Widget", (), {})})
    with pytest.raises(RuntimeError, match=_SA_MODULE_REGISTRY_KEY):
        _cleanup_registry(base)


def test_unexpected_marker_type_in_module_tree_raises():
    """An unknown marker type in the module tree stops the walk loudly."""
    root = _ModuleMarker(_SA_MODULE_REGISTRY_KEY, None)
    submodule = _ModuleMarker("some_module", root)
    root.contents["some_module"] = submodule
    submodule.contents["Bogus"] = object()

    base = _fake_base({_SA_MODULE_REGISTRY_KEY: root})
    with pytest.raises(RuntimeError, match="unexpected"):
        _cleanup_registry(base)


def test_empty_registry_is_a_noop():
    """A registry with no classes and no module registry scrubs cleanly."""
    _cleanup_registry(_fake_base({}))  # no raise


def test_cleanup_removes_local_model_registration():
    """Happy path: a function-local model is scrubbed from both the top-level
    registry and the nested module tree, on the real base."""

    def _define():
        class _LeakyLocal(fr.DataclassBase):
            __tablename__ = "leaky_local"
            id: Mapped[int] = mapped_column(primary_key=True)

        return _LeakyLocal

    _define()
    class_registry = fr.DataclassBase.registry._class_registry
    assert "_LeakyLocal" in class_registry

    _cleanup_registry(fr.DataclassBase)

    assert "_LeakyLocal" not in class_registry
    module_registry = class_registry.get(_SA_MODULE_REGISTRY_KEY)
    if module_registry is not None:
        for module_marker in module_registry.contents.values():
            assert "_LeakyLocal" not in module_marker.contents
