"""The object methods under the verbs are not seams: a view class that
defines one, itself or through a mixin, fails at class definition.

``delete_object`` is removed (its every override was a verb concern; a
stale one would hard-delete silently). ``make_new_object``,
``update_object``, and ``save_object`` are final (a stamp hooked there only
covered the verbs; a definition would shadow the framework's). Each guard
names the defining class and points at the replacement.
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
# make_new_object / update_object / save_object: final
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["make_new_object", "update_object", "save_object"])
def test_async_view_defining_a_final_utility_fails_at_class_definition(name):
    Record, RecordSchema = _model_and_schema()

    async def override(self, *args):
        return args[0]

    with pytest.raises(
        fr.exc.RestlyConfigurationError,
        match=rf"RecordView defines {name}, which is a final domain utility",
    ) as info:
        type(
            "RecordView",
            (fr.AsyncRestView,),
            {"model": Record, "schema": RecordSchema, name: override},
        )

    assert "column default on the model" in str(info.value)


@pytest.mark.parametrize("name", ["make_new_object", "update_object", "save_object"])
def test_sync_view_defining_a_final_utility_fails_at_class_definition(name):
    Record, RecordSchema = _model_and_schema()

    def override(self, *args):
        return args[0]

    with pytest.raises(
        fr.exc.RestlyConfigurationError,
        match=rf"RecordView defines {name}, which is a final domain utility",
    ):
        type(
            "RecordView",
            (fr.RestView,),
            {"model": Record, "schema": RecordSchema, name: override},
        )


def test_a_final_utility_from_a_mixin_names_the_mixin():
    """The cooperative-stamping mixin shape is the case the guard exists for."""
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


def test_a_create_override_that_calls_the_utilities_is_the_replacement():
    """Calling stays fine; only defining is rejected."""
    Record, RecordSchema = _model_and_schema()

    class RecordView(fr.AsyncRestView):
        model = Record
        schema = RecordSchema

        async def create(self, schema_obj):
            obj = await self.make_new_object(schema_obj)
            obj.name = obj.name.strip()
            return await self.save_object(obj)

    assert RecordView.make_new_object is fr.AsyncRestView.make_new_object
