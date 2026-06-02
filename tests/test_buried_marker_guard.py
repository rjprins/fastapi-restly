"""Regression for ticket 3u1r (extended to ReadOnly): a ReadOnly/WriteOnly
marker buried inside a union member silently no-ops -- WriteOnly fails to exclude
the field from responses (it leaks), ReadOnly fails to drop it from create/update
(it stays writable). The framework now rejects the misuse loudly:

* at schema-definition time for ``BaseSchema`` subclasses, via
  ``__pydantic_init_subclass__`` (so it surfaces at import);
* again at view registration for schemas that do NOT derive from ``BaseSchema``
  (the import-time hook never sees those).

The safe form is ``Marker[Optional[T]]``; the leaky forms are
``Optional[Marker[T]]`` and ``Marker[T] | None``.
"""

from __future__ import annotations

from typing import Optional

import pydantic
import pytest
from sqlalchemy.orm import Mapped

import fastapi_restly as fr
from fastapi_restly import RestlyConfigurationError
from fastapi_restly.schemas._base import (
    _find_buried_marker_fields,
    readonly_marker,
    writeonly_marker,
)

# --- import-time guard on BaseSchema subclasses -------------------------------


def test_writeonly_buried_in_optional_rejected_at_definition():
    with pytest.raises(RestlyConfigurationError) as exc:

        class Leaky(fr.BaseSchema):
            secret: Optional[fr.WriteOnly[str]]

    msg = str(exc.value)
    assert "Leaky.secret" in msg
    assert "WriteOnly[Optional[T]]" in msg


def test_writeonly_buried_in_pipe_union_rejected_at_definition():
    with pytest.raises(RestlyConfigurationError) as exc:

        class Leaky(fr.BaseSchema):
            secret: "fr.WriteOnly[str] | None"

    assert "Leaky.secret" in str(exc.value)


def test_readonly_buried_in_union_rejected_at_definition():
    with pytest.raises(RestlyConfigurationError) as exc:

        class Leaky(fr.BaseSchema):
            rid: Optional[fr.ReadOnly[int]]

    msg = str(exc.value)
    assert "Leaky.rid" in msg
    assert "ReadOnly[Optional[T]]" in msg


def test_writeonly_buried_in_container_rejected_at_definition():
    # The marker nested in a container element (not just a union) also no-ops and
    # leaks, so it must be rejected too.
    with pytest.raises(RestlyConfigurationError) as exc:

        class Leaky(fr.BaseSchema):
            tags: list[fr.WriteOnly[str]]

    msg = str(exc.value)
    assert "Leaky.tags" in msg
    assert "WriteOnly[Optional[T]]" in msg


# Defined at module scope (not inside the test) so the ``Optional[SafeNested]``
# forward ref resolves against the module globals when the framework forces a
# ``model_rebuild`` on the derived schemas. A function-local nested class is only
# reachable from the test frame, which the pinned-minimum pydantic does not
# capture during the rebuild -- mirroring real usage, schemas live at module level.
class SafeNested(fr.BaseSchema):
    inner_secret: fr.WriteOnly[str]


class Safe(fr.BaseSchema):
    a: fr.WriteOnly[str]
    b: fr.WriteOnly[Optional[str]]
    c: fr.ReadOnly[int]
    d: Optional[str]
    e: fr.WriteOnly[list[str]]  # marker wraps the container -> safe
    f: Optional[SafeNested]  # nested schema's own top-level marker -> safe


def test_safe_forms_are_allowed():
    # The recommended forms (and a plain optional) must not trip the guard, and
    # neither should the framework's derived create/update schemas.
    from fastapi_restly.schemas._base import (
        create_model_with_optional_fields,
        create_model_without_read_only_fields,
    )

    create_model_without_read_only_fields(Safe)  # Create schema (OmitReadOnly)
    create_model_with_optional_fields(Safe)  # Update schema (PatchMixin)
    assert _find_buried_marker_fields(Safe) == []


# --- registration backstop for non-BaseSchema schemas -------------------------


class _BuriedMarkerWidget(fr.IDBase):
    name: Mapped[str]


@pytest.mark.parametrize("view_base", [fr.AsyncRestView, fr.RestView])
def test_registration_backstop_for_non_baseschema_schema(view_base):
    from fastapi import FastAPI

    # A plain pydantic schema (NOT fr.BaseSchema) escapes the import-time hook,
    # so the view-registration backstop is what must catch the buried marker.
    class PlainSchema(pydantic.BaseModel):
        model_config = pydantic.ConfigDict(from_attributes=True)
        token: Optional[fr.WriteOnly[str]]

    app = FastAPI()
    with pytest.raises(RestlyConfigurationError) as exc:

        @fr.include_view(app)
        class _BuriedMarkerView(view_base):
            model = _BuriedMarkerWidget
            schema = PlainSchema

    assert "PlainSchema.token" in str(exc.value)


# --- unit coverage of the finder ----------------------------------------------


def test_find_buried_marker_fields_reports_name_and_marker():
    # A plain (non-BaseSchema) model so defining it does not raise; the finder is
    # inspected directly and must report exactly the buried fields + their marker.
    class Nested(pydantic.BaseModel):
        deep: fr.WriteOnly[str]

    class M(pydantic.BaseModel):
        ok_wo: fr.WriteOnly[str]
        ok_ro: fr.ReadOnly[int]
        ok_wo_list: fr.WriteOnly[list[str]]  # marker wraps the container -> safe
        leak_wo: Optional[fr.WriteOnly[str]]
        leak_ro: "fr.ReadOnly[int] | None"
        leak_wo_list: list[fr.WriteOnly[str]]  # container element burial
        leak_wo_opt_list: Optional[list[fr.WriteOnly[str]]]  # union + container
        nested_model: Optional[Nested]  # marker lives in a nested model -> NOT buried
        plain: Optional[str]

    found = dict(_find_buried_marker_fields(M))
    assert found == {
        "leak_wo": writeonly_marker,
        "leak_ro": readonly_marker,
        "leak_wo_list": writeonly_marker,
        "leak_wo_opt_list": writeonly_marker,
    }
