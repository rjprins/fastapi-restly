"""The handlers are not seams: a view class that defines one, itself or
through a mixin, fails at class definition.

``handle_<verb>`` stays the tier a custom route calls, which is what keeps
``authorize`` and the commit bracket on every generated CRUD route. An
override re-implements the load, authorize and commit order to reach
something another seam gives directly, so the guard names that seam.
"""

import pytest
from sqlalchemy.orm import Mapped

import fastapi_restly as fr

HANDLERS = [
    "handle_get_many",
    "handle_get_one",
    "handle_create",
    "handle_update",
    "handle_delete",
]
WRITE_HANDLERS = ["handle_create", "handle_update", "handle_delete"]


def _model_and_schema():
    class Record(fr.IDBase):
        name: Mapped[str]

    class RecordSchema(fr.IDSchema):
        name: str

    return Record, RecordSchema


def _define(base, name, override):
    Record, RecordSchema = _model_and_schema()
    return type(
        "RecordView", (base,), {"model": Record, "schema": RecordSchema, name: override}
    )


@pytest.mark.parametrize("name", HANDLERS)
def test_async_view_defining_a_handler_fails_at_class_definition(name):
    async def override(self, *args, **kwargs):
        return None

    with pytest.raises(
        fr.exc.RestlyConfigurationError,
        match=rf"RecordView defines {name}, which is a final handler",
    ):
        _define(fr.AsyncRestView, name, override)


@pytest.mark.parametrize("name", HANDLERS)
def test_sync_view_defining_a_handler_fails_at_class_definition(name):
    def override(self, *args, **kwargs):
        return None

    with pytest.raises(
        fr.exc.RestlyConfigurationError,
        match=rf"RecordView defines {name}, which is a final handler",
    ):
        _define(fr.RestView, name, override)


@pytest.mark.parametrize("base", [fr.RestView, fr.AsyncRestView])
@pytest.mark.parametrize("name", HANDLERS)
def test_the_message_names_the_business_method_the_gate_and_the_contract(base, name):
    """Domain logic, a gate, and the HTTP contract each get their own seam."""
    verb = name.removeprefix("handle_")

    with pytest.raises(fr.exc.RestlyConfigurationError) as info:
        _define(base, name, lambda self, *args, **kwargs: None)

    message = str(info.value)
    assert "it is the tier a custom route calls" in message
    assert f"{verb} business method" in message
    assert "authorize" in message
    assert f"{verb}_endpoint" in message


@pytest.mark.parametrize("base", [fr.RestView, fr.AsyncRestView])
@pytest.mark.parametrize("name", WRITE_HANDLERS)
def test_a_write_handler_message_names_the_hooks_and_the_commit_bracket(base, name):
    """Side-effect timing and one commit over several writes have seams too."""
    with pytest.raises(fr.exc.RestlyConfigurationError) as info:
        _define(base, name, lambda self, *args, **kwargs: None)

    message = str(info.value)
    assert "before_action_commit or after_action_commit" in message
    assert f"call {name} inside defer_write_action_commit()" in message


@pytest.mark.parametrize("name", ["handle_get_many", "handle_get_one"])
def test_a_read_handler_message_leaves_out_the_write_seams(name):
    """A read never commits, so the commit advice would be noise."""
    with pytest.raises(fr.exc.RestlyConfigurationError) as info:
        _define(fr.AsyncRestView, name, lambda self, *args, **kwargs: None)

    message = str(info.value)
    assert "action_commit" not in message
    assert "defer_write_action_commit" not in message


def test_a_handler_from_a_mixin_names_the_mixin():
    """The shared-override mixin shape is the case the guard exists for."""
    Record, RecordSchema = _model_and_schema()

    class NotifyMixin:
        async def handle_create(self, schema_obj):
            obj = await super().handle_create(schema_obj)  # type: ignore[misc]
            obj.notified = True
            return obj

    with pytest.raises(
        fr.exc.RestlyConfigurationError,
        match=r"RecordView defines handle_create \(from NotifyMixin\)",
    ):

        class RecordView(NotifyMixin, fr.AsyncRestView):
            model = Record
            schema = RecordSchema


def test_a_handler_two_levels_up_still_names_its_class():
    """A base view in the middle of the MRO is reported at the subclass."""
    Record, RecordSchema = _model_and_schema()

    class _Base(fr.AsyncRestView):
        model = Record
        schema = RecordSchema

    # set after definition, so only the subclass below trips the guard
    _Base.handle_delete = lambda self, id: None  # type: ignore[assignment]

    with pytest.raises(
        fr.exc.RestlyConfigurationError,
        match=r"RecordView defines handle_delete \(from _Base\)",
    ):

        class RecordView(_Base):
            prefix = "/records"


def test_the_replacement_seams_define_cleanly():
    """Everything the message points at is still an override point."""
    Record, RecordSchema = _model_and_schema()

    class RecordView(fr.AsyncRestView):
        model = Record
        schema = RecordSchema

        async def create(self, schema_obj):
            return await super().create(schema_obj)

        async def delete(self, obj):
            obj.deleted = True

        async def authorize(self, action, obj=None, data=None):
            pass

        async def after_action_commit(self, action, new, old=None):
            pass

        @fr.post("/")
        async def create_endpoint(self, schema_obj):
            return self.to_response(await self.handle_create(schema_obj))

    assert RecordView.handle_create is fr.AsyncRestView.handle_create


def test_calling_a_handler_from_a_custom_route_is_the_replacement():
    """Calling stays fine; only defining is rejected."""
    Record, RecordSchema = _model_and_schema()

    class RecordView(fr.AsyncRestView):
        model = Record
        schema = RecordSchema

        @fr.post("/import")
        async def bulk_import(self, items: list[fr.BaseSchema]):
            async with self.defer_write_action_commit():
                return [await self.handle_create(item) for item in items]

    assert RecordView.handle_create is fr.AsyncRestView.handle_create
