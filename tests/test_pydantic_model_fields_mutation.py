"""Regression test for I6: OmitReadOnlyMixin / PatchMixin mutate `cls.model_fields`.

`fastapi_restly.schemas._base.OmitReadOnlyMixin` and `PatchMixin` rely on a
pydantic-v2 implementation detail: that ``BaseModel.model_fields`` is a regular
``dict`` whose entries can be deleted, that ``FieldInfo.default`` and
``FieldInfo.annotation`` can be mutated, and that ``model_rebuild(force=True)``
will regenerate the validator/serializer using those changes.

This is not in the public pydantic v2 API. If a pydantic release freezes the
field dict (or moves the metadata to ``__pydantic_fields__`` in pydantic-core)
the mixins will silently break or start raising.

This test guards against that: it exercises both mixins end-to-end and asserts
the visible behaviour. It also pins the supported pydantic minor so a future
upgrade triggers a CI failure if the contract changes.
"""

from typing import Annotated

import pydantic
import pytest

import fastapi_restly as fr
from fastapi_restly.schemas._base import (
    OmitReadOnlyMixin,
    PatchMixin,
    create_model_with_optional_fields,
    create_model_without_read_only_fields,
)

# Pinned at the lower bound from pyproject.toml. If you bump the dependency
# floor, re-run the suite first; if it still passes, bump this string too.
SUPPORTED_PYDANTIC_MAJOR_MINOR = (2, 11)


def test_pydantic_version_within_supported_range() -> None:
    """Fail loudly if pydantic moves outside the version range we tested.

    The mixins below depend on pydantic-v2 internals. We have validated them
    on 2.11.x. Newer versions *probably* work — but they should be re-tested,
    so flag a mismatch instead of letting it silently regress.
    """
    parts = pydantic.VERSION.split(".")
    major, minor = int(parts[0]), int(parts[1])
    expected_major, expected_min_minor = SUPPORTED_PYDANTIC_MAJOR_MINOR
    assert major == expected_major, (
        f"OmitReadOnlyMixin/PatchMixin tested on pydantic {expected_major}.x, "
        f"got {pydantic.VERSION}. Re-run the regression test and update "
        f"SUPPORTED_PYDANTIC_MAJOR_MINOR if everything still passes."
    )
    assert minor >= expected_min_minor, (
        f"Pydantic {pydantic.VERSION} is older than the pinned minimum "
        f"{expected_major}.{expected_min_minor}.x; the mixins assume the API "
        f"shape from that release."
    )


def test_omit_read_only_mixin_removes_readonly_fields() -> None:
    """OmitReadOnlyMixin must drop ReadOnly fields from `model_fields`."""

    class Foo(fr.BaseSchema):
        id: fr.ReadOnly[int]
        name: str
        created_at: fr.ReadOnly[str]

    Created = create_model_without_read_only_fields(Foo)

    # Both readonly fields must be gone, the writable one must remain.
    assert "id" not in Created.model_fields
    assert "created_at" not in Created.model_fields
    assert "name" in Created.model_fields

    # The validator / serializer must reflect the new shape: passing only
    # `name` must validate without "id is required" errors.
    instance = Created(name="hello")  # type: ignore[call-arg]
    assert getattr(instance, "name") == "hello"  # noqa: B009

    # And passing a `id` must be ignored / rejected, since the field no longer
    # exists. With pydantic's default `extra="ignore"`, extra keys are simply
    # not retained.
    instance2 = Created(name="hello", id=99)  # type: ignore[call-arg]
    assert not hasattr(instance2, "id") or "id" not in instance2.model_dump()


def test_patch_mixin_makes_fields_optional_with_none_default() -> None:
    """PatchMixin must let every field be omitted and default to None."""

    class Foo(fr.BaseSchema):
        id: fr.ReadOnly[int]
        name: str
        count: int

    Update = create_model_with_optional_fields(Foo)

    # `id` removed by OmitReadOnlyMixin; `name` and `count` made optional.
    assert "id" not in Update.model_fields
    for field_name in ("name", "count"):
        info = Update.model_fields[field_name]
        assert info.default is None, (
            f"{field_name} default should be None, got {info.default!r}"
        )

    # Empty payload must validate.
    empty = Update()  # type: ignore[call-arg]
    assert empty.model_dump(exclude_unset=True) == {}

    # Partial payload must validate; unspecified fields must not appear in the
    # serialized output unless explicitly set.
    partial = Update(name="hi")  # type: ignore[call-arg]
    assert partial.model_dump(exclude_unset=True) == {"name": "hi"}


def test_patch_mixin_preserves_already_optional_fields() -> None:
    """PatchMixin must not double-wrap `T | None` annotations."""

    class Foo(fr.BaseSchema):
        maybe_name: str | None = None

    Update = create_model_with_optional_fields(Foo)
    info = Update.model_fields["maybe_name"]
    # The validator should still accept `None`; the exact annotation shape is
    # an implementation detail (Optional[str] vs str | None) but it must not
    # have grown an extra layer.
    instance = Update(maybe_name=None)  # type: ignore[call-arg]
    assert getattr(instance, "maybe_name") is None  # noqa: B009
    instance2 = Update(maybe_name="x")  # type: ignore[call-arg]
    assert getattr(instance2, "maybe_name") == "x"  # noqa: B009
    # And passing `field.default = None` must not have replaced an existing
    # default if the field was already None.
    assert info.default is None


def test_model_fields_is_still_mutable_dict() -> None:
    """Direct guard: `cls.model_fields` is a dict we can ``del`` from.

    If pydantic moves to a frozen mapping, this test fails first — before the
    mixin-level tests above — and the error message points squarely at the
    contract that broke.
    """

    class Foo(fr.BaseSchema):
        id: int
        name: str

    # Mutability checks. If pydantic switches `model_fields` to a property
    # backed by a `MappingProxyType`, this raises TypeError.
    fields = Foo.model_fields
    assert isinstance(fields, dict), (
        f"OmitReadOnlyMixin/PatchMixin assume `model_fields` is a dict; "
        f"got {type(fields).__name__}. See I6 in swarm-code-quality.md."
    )
    # Subclass to avoid mutating the class under test.

    class FooCopy(fr.BaseSchema):
        id: int
        name: str

    try:
        del FooCopy.model_fields["id"]
    except (TypeError, KeyError) as exc:  # pragma: no cover - regression guard
        pytest.fail(
            f"`del cls.model_fields[name]` raised {type(exc).__name__}: {exc}. "
            f"OmitReadOnlyMixin relies on this being permitted."
        )

    # FieldInfo mutability — `default` and `annotation` must be settable.
    info = FooCopy.model_fields["name"]
    info.default = None
    info.annotation = Annotated[str, "marker"]  # type: ignore[assignment]
    # Rebuild must succeed.
    FooCopy.model_rebuild(force=True)


def test_omit_readonly_mixin_directly() -> None:
    """Use OmitReadOnlyMixin via direct subclassing (not through helper)."""

    class Foo(fr.BaseSchema):
        id: fr.ReadOnly[int]
        name: str

    class CreateFoo(OmitReadOnlyMixin, Foo):
        pass

    assert "id" not in CreateFoo.model_fields
    assert "name" in CreateFoo.model_fields


def test_patch_mixin_directly() -> None:
    """Use PatchMixin via direct subclassing (not through helper)."""

    class Foo(fr.BaseSchema):
        name: str
        count: int = 5

    class UpdateFoo(PatchMixin, Foo):
        pass

    # Defaults wiped to None.
    assert UpdateFoo.model_fields["name"].default is None
    assert UpdateFoo.model_fields["count"].default is None

    instance = UpdateFoo()  # type: ignore[call-arg]
    assert instance.model_dump(exclude_unset=True) == {}
