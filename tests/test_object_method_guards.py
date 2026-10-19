"""The object methods under the verbs are removed from the view: a view
class that defines one, itself or through a mixin, fails at class definition.

``delete_object`` is removed (its every override was a verb concern; a
stale one would hard-delete silently). ``make_new_object``,
``update_object``, and ``save_object`` are removed because the business
methods no longer flush: ``super().create()`` and ``super().update()`` build
and change the object, and the commit bracket saves it. A definition would be
dead code. Each guard names the defining class and points at the replacement.
"""

import pytest
from sqlalchemy.orm import Mapped

import fastapi_restly as fr

from ._models import SharedBase


def _model_and_schema():
    class Record(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class RecordSchema(fr.IDSchema):
        name: str

    return Record, RecordSchema


# ---------------------------------------------------------------------------
# delete_object: removed
# ---------------------------------------------------------------------------


def test_sync_view_defining_delete_object_fails_at_class_definition():
    Record, RecordSchema = _model_and_schema()

    with pytest.raises(
        fr.exc.RestlyConfigurationError,
        match=r"RecordView defines delete_object, which is removed",
    ) as info:

        class RecordView(fr.RestView):
            model = Record
            schema = RecordSchema

            def delete_object(self, obj):
                obj.deleted = True

    assert "Override delete instead" in str(info.value)


def test_async_view_defining_delete_object_fails_at_class_definition():
    Record, RecordSchema = _model_and_schema()

    with pytest.raises(
        fr.exc.RestlyConfigurationError,
        match=r"RecordView defines delete_object, which is removed",
    ):

        class RecordView(fr.AsyncRestView):
            model = Record
            schema = RecordSchema

            async def delete_object(self, obj):
                obj.deleted = True


def test_delete_object_from_a_mixin_names_the_mixin():
    Record, RecordSchema = _model_and_schema()

    class SoftDeleteMixin:
        async def delete_object(self, obj):
            obj.deleted = True

    with pytest.raises(
        fr.exc.RestlyConfigurationError,
        match=r"RecordView defines delete_object \(from SoftDeleteMixin\)",
    ):

        class RecordView(SoftDeleteMixin, fr.AsyncRestView):
            model = Record
            schema = RecordSchema


def test_a_delete_override_is_the_replacement():
    """The verb override the guard points at defines cleanly."""
    Record, RecordSchema = _model_and_schema()

    class RecordView(fr.AsyncRestView):
        model = Record
        schema = RecordSchema

        async def delete(self, obj):
            obj.deleted = True

    assert RecordView.delete is not fr.AsyncRestView.delete


# ---------------------------------------------------------------------------
# make_new_object / update_object / save_object: removed from the view
# ---------------------------------------------------------------------------

_REPLACEMENTS = {
    "make_new_object": "super().create(data)",
    "update_object": "super().update(obj, data)",
    "save_object": "The commit bracket saves",
}


@pytest.mark.parametrize("name", ["make_new_object", "update_object", "save_object"])
def test_async_view_defining_a_removed_utility_fails_at_class_definition(name):
    Record, RecordSchema = _model_and_schema()

    async def override(self, *args):
        return args[0]

    with pytest.raises(
        fr.exc.RestlyConfigurationError,
        match=rf"RecordView defines {name}, which was removed from the view",
    ) as info:
        type(
            "RecordView",
            (fr.AsyncRestView,),
            {"model": Record, "schema": RecordSchema, name: override},
        )

    message = str(info.value)
    assert _REPLACEMENTS[name] in message
    assert f"fr.objects.{name}" in message


@pytest.mark.parametrize("name", ["make_new_object", "update_object", "save_object"])
def test_sync_view_defining_a_removed_utility_fails_at_class_definition(name):
    Record, RecordSchema = _model_and_schema()

    def override(self, *args):
        return args[0]

    with pytest.raises(
        fr.exc.RestlyConfigurationError,
        match=rf"RecordView defines {name}, which was removed from the view",
    ):
        type(
            "RecordView",
            (fr.RestView,),
            {"model": Record, "schema": RecordSchema, name: override},
        )


def test_a_removed_utility_from_a_mixin_names_the_mixin():
    """A stamping mixin of the old shape would be dead code now."""
    Record, RecordSchema = _model_and_schema()

    class AuditStampedMixin:
        async def make_new_object(self, schema_obj):
            obj = await super().make_new_object(schema_obj)  # type: ignore[misc]
            obj.created_by_id = 1
            return obj

    with pytest.raises(
        fr.exc.RestlyConfigurationError,
        match=r"RecordView defines make_new_object \(from AuditStampedMixin\)",
    ):

        class RecordView(AuditStampedMixin, fr.AsyncRestView):
            model = Record
            schema = RecordSchema


def test_a_create_override_that_calls_super_is_the_replacement():
    """The override the guard points at defines cleanly, and the view has no
    make_new_object any more."""
    Record, RecordSchema = _model_and_schema()

    class RecordView(fr.AsyncRestView):
        model = Record
        schema = RecordSchema

        async def create(self, schema_obj):
            obj = await super().create(schema_obj)
            obj.name = obj.name.strip()
            return obj

    assert not hasattr(RecordView, "make_new_object")
    assert not hasattr(RecordView, "save_object")
