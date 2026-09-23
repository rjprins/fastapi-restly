import sys
from typing import ClassVar

import pytest

import fastapi_restly as fr


def test_local_annotation_alias_and_classvar_declare_members():
    Member = fr.ContextParam[int]

    class Current(fr.ContextNamespace):
        user_id: Member
        role: ClassVar[fr.ContextParam[str]]

    with Current.bind(user_id=7, role="editor"):
        assert Current.user_id() == 7
        assert Current.role() == "editor"


def test_subclass_without_annotations_preserves_inherited_members():
    class Parent(fr.ContextNamespace):
        user_id: fr.ContextParam[int]

    class Child(Parent):
        pass

    assert Child.user_id is Parent.user_id
    assert "user_id" not in vars(Child)
    with Child.bind(user_id=7):
        assert Parent.user_id() == 7


def test_stringified_annotations_allow_unresolved_member_types():
    namespace = {"fr": fr, "__name__": __name__}
    exec(
        "from __future__ import annotations\n"
        "class Current(fr.ContextNamespace):\n"
        "    user: fr.ContextParam[User]\n",
        namespace,
    )
    current = namespace["Current"]
    user = object()
    with current.bind(user=user):
        assert current.user() is user


@pytest.mark.skipif(sys.version_info < (3, 14), reason="Deferred annotations need 3.14")
def test_deferred_annotations_allow_unresolved_member_types():
    namespace = {"fr": fr, "__name__": __name__}
    exec(
        "class Current(fr.ContextNamespace):\n"
        "    user: fr.ContextParam[User]\n"
        "    _helper: UndefinedHelper\n",
        namespace,
    )
    current = namespace["Current"]
    user = object()
    with current.bind(user=user):
        assert current.user() is user
