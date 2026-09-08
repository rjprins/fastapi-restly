"""``delete_object`` is removed from the view classes: a view class that
still defines it, itself or through a mixin, fails at class definition.

A stale override would be silently dead code, and a dead soft-delete
override hard-deletes rows. The guard names the defining class and points
at the replacement: override ``delete``.
"""

import pytest
from sqlalchemy.orm import Mapped

import fastapi_restly as fr


def _model_and_schema():
    class Record(fr.IDBase):
        name: Mapped[str]

    class RecordSchema(fr.IDSchema):
        name: str

    return Record, RecordSchema


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
