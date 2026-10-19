"""The view's ``Action`` class: the list of all its actions.

A view declares its own actions in a subclass of ``fr.Action``. The class
must list the actions of every base class that declares some, and
``write_action`` takes only a declared name.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Mapped

import fastapi_restly as fr
from fastapi_restly.exc import RestlyConfigurationError

from ._models import SharedBase
from ._write_env import call, enter, env, registered  # noqa: F401

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class TrashMixin:
    class Action(fr.Action):
        RESTORE = "restore"


class AuditMixin:
    class Action(fr.Action):
        REVIEW = "review"


# ---------------------------------------------------------------------------
# The list at class definition
# ---------------------------------------------------------------------------


def test_a_view_without_own_actions_has_the_crud_actions():
    class ItemView(fr.AsyncRestView):
        pass

    assert ItemView.Action is fr.Action
    assert ItemView.Action.UPDATE == "update"


def test_a_view_adds_actions_to_those_of_its_bases():
    class BaseView(TrashMixin, fr.AsyncRestView):
        pass

    class PostView(BaseView):
        class Action(BaseView.Action):
            PUBLISH = "publish"

    class DraftView(PostView):
        pass

    assert PostView.Action.RESTORE == "restore"
    assert PostView.Action.PUBLISH == "publish"
    assert DraftView.Action is PostView.Action


def test_a_view_that_leaves_out_a_mixin_action_fails():
    with pytest.raises(RestlyConfigurationError) as info:

        class PostView(TrashMixin, fr.RestView):
            class Action(fr.Action):
                PUBLISH = "publish"

    message = str(info.value)
    assert "PostView.Action does not inherit from TrashMixin.Action" in message
    assert "class Action(TrashMixin.Action)" in message


def test_two_bases_with_actions_need_an_own_action_class():
    # Python takes the Action of the first base and drops the other one
    with pytest.raises(RestlyConfigurationError) as info:

        class PostView(TrashMixin, AuditMixin, fr.AsyncRestView):
            pass

    message = str(info.value)
    assert (
        "PostView.Action (from TrashMixin) does not inherit from AuditMixin.Action"
        in message
    )
    assert "class Action(TrashMixin.Action, AuditMixin.Action)" in message


def test_an_action_class_with_every_base_passes():
    class PostView(TrashMixin, AuditMixin, fr.AsyncRestView):
        class Action(TrashMixin.Action, AuditMixin.Action):
            PUBLISH = "publish"

    assert {PostView.Action.RESTORE, PostView.Action.REVIEW} == {"restore", "review"}


def test_the_suggestion_keeps_a_base_that_the_own_action_already_has():
    with pytest.raises(RestlyConfigurationError) as info:

        class PostView(TrashMixin, AuditMixin, fr.AsyncRestView):
            class Action(AuditMixin.Action):
                PUBLISH = "publish"

    message = str(info.value)
    assert "does not inherit from TrashMixin.Action" in message
    assert "class Action(TrashMixin.Action, AuditMixin.Action)" in message


def test_the_suggestion_names_a_shared_action_class_once():
    class AliasMixin:
        Action = TrashMixin.Action

    with pytest.raises(RestlyConfigurationError) as info:

        class PostView(TrashMixin, AliasMixin, AuditMixin, fr.AsyncRestView):
            pass

    assert "class Action(TrashMixin.Action, AuditMixin.Action)." in str(info.value)


def test_an_action_class_that_does_not_inherit_fr_action_fails():
    with pytest.raises(
        RestlyConfigurationError, match="does not inherit from fr.Action"
    ):

        class PostView(fr.RestView):
            class Action:
                PUBLISH = "publish"


def test_an_action_that_is_not_a_class_fails():
    with pytest.raises(
        RestlyConfigurationError,
        match="PostView.Action does not inherit from fr.Action",
    ):

        class PostView(fr.RestView):
            Action = ("publish",)


# ---------------------------------------------------------------------------
# An action is an UPPER_CASE name with its name in lower case as the value
# ---------------------------------------------------------------------------


def test_the_value_of_an_action_is_its_name_in_lower_case():
    with pytest.raises(RestlyConfigurationError) as info:

        class Action(fr.Action):
            PUBLISH = "create"

    assert str(info.value).endswith(
        "Action.PUBLISH is 'create'. The value of an action is its name in "
        "lower case: PUBLISH = 'publish'."
    )


def test_an_action_class_has_only_actions():
    with pytest.raises(RestlyConfigurationError, match=r"Action.label: an Action"):

        class Action(fr.Action):
            PUBLISH = "publish"
            label = "not an action"


def test_a_plain_base_cannot_change_an_action():
    class Shadow:
        UPDATE = "edit"

    with pytest.raises(RestlyConfigurationError, match="UPDATE is 'edit'"):

        class Action(Shadow, fr.Action):
            pass


# ---------------------------------------------------------------------------
# write_action takes only a declared name
# ---------------------------------------------------------------------------


def _models():
    class Note(fr.IDMixin, SharedBase):
        title: Mapped[str]

    class NoteSchema(fr.IDSchema):
        title: str

    return Note, NoteSchema


def _note_view(env, Note, NoteSchema):
    calls: list[str] = []

    class Action(fr.Action):
        RETITLE = "retitle"

    def authorize(self, action, obj=None, data=None):
        calls.append(action)

    async def async_authorize(self, action, obj=None, data=None):
        calls.append(action)

    namespace = {
        "prefix": "/notes",
        "model": Note,
        "schema": NoteSchema,
        "Action": Action,
        "authorize": env.pick(authorize, async_authorize),
    }
    return registered(type("NoteView", (env.base,), namespace)), calls


async def test_write_action_refuses_an_undeclared_name(env):
    Note, NoteSchema = _models()
    await env.create_all()
    view_cls, calls = _note_view(env, Note, NoteSchema)
    session = env.make_session()
    async with enter(session):
        note = Note(title="draft")
        session.add(note)
        await call(session.commit())
        view = view_cls(request=None, session=session)
        with pytest.raises(ValueError) as info:
            async with enter(view.write_action("retitel", obj=note)):
                note.title = "changed"
    assert str(info.value) == (
        "NoteView.write_action('retitel', ...): unknown action 'retitel'. "
        "NoteView.Action does not declare it."
    )
    # refused before authorize and before the block ran
    assert calls == []
    assert note.title == "draft"


async def test_write_action_takes_a_declared_name_and_a_crud_name(env):
    Note, NoteSchema = _models()
    await env.create_all()
    view_cls, calls = _note_view(env, Note, NoteSchema)
    session = env.make_session()
    async with enter(session):
        note = Note(title="draft")
        session.add(note)
        await call(session.commit())
        view = view_cls(request=None, session=session)
        async with enter(view.write_action(view.Action.RETITLE, obj=note)):
            note.title = "final"
        async with enter(view.write_action(view.Action.UPDATE, obj=note)):
            note.title = "final!"
        titles = list(await call(session.scalars(select(Note.title))))
    assert calls == ["retitle", "update"]
    assert titles == ["final!"]


async def test_write_action_takes_only_the_value_of_an_action(env):
    Note, NoteSchema = _models()
    view_cls, _ = _note_view(env, Note, NoteSchema)
    view = view_cls(request=None, session=None)
    for action in ("RETITLE", "Retitle", None, ["retitle"]):
        with pytest.raises(ValueError, match="unknown action"):
            async with enter(view.write_action(action, obj=None)):
                pass


def test_a_view_with_a_base_view_and_a_mixin_inherits_both():
    class BaseView(TrashMixin, fr.AsyncRestView):
        pass

    with pytest.raises(RestlyConfigurationError) as info:

        class PostView(AuditMixin, BaseView):
            pass

    assert "class Action(AuditMixin.Action, TrashMixin.Action)" in str(info.value)

    class FixedView(AuditMixin, BaseView):
        class Action(AuditMixin.Action, TrashMixin.Action):
            pass

    assert (FixedView.Action.RESTORE, FixedView.Action.REVIEW) == ("restore", "review")
