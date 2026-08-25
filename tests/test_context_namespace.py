"""ContextNamespace: the declaration surface for ContextParams.

A member is declared by annotation (`name: ContextParam[T]`), so the
attribute name is the bind name; assignment adopts an existing member so
namespaces can share one slot. The namespace binds and explains as a
unit, and the standalone construction path is closed: a slot is pure
name and identity, and the namespace is its address.
"""

import inspect

import pytest
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

import fastapi_restly as fr


class Base(DeclarativeBase):
    pass


class Thing(Base):
    __tablename__ = "ctxns_thing"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int]


class Context(fr.ContextNamespace):
    tenant_id: fr.ContextParam[int]
    locale: fr.ContextParam[str]


def test_annotation_declares_a_param_under_the_attribute_name():
    assert isinstance(Context.tenant_id, fr.ContextParam)
    with Context.tenant_id.bind(tenant_id=7):
        assert Context.tenant_id() == 7


def test_the_declared_type_lands_in_the_synthesized_signature():
    signature = inspect.signature(Context.tenant_id._param_fn)
    assert signature.parameters["tenant_id"].annotation is int


def test_namespace_bind_binds_several_members_at_once():
    with Context.bind(tenant_id=1, locale="nl"):
        assert Context.tenant_id() == 1
        assert Context.locale() == "nl"
    with pytest.raises(TypeError):
        Context.tenant_id()


def test_namespace_bind_rejects_an_unknown_member():
    with pytest.raises(TypeError, match="no member"):
        with Context.bind(nope=1):
            pass


def test_assignment_adopts_the_same_slot():
    class Other(fr.ContextNamespace):
        tenant_id = Context.tenant_id

    assert Other.tenant_id is Context.tenant_id
    with Other.bind(tenant_id=9):
        assert Context.tenant_id() == 9


def test_members_are_inherited():
    class Wider(Context):
        region: fr.ContextParam[str]

    with Wider.bind(tenant_id=2, region="eu"):
        assert Wider.tenant_id() == 2
        assert Wider.region() == "eu"
    assert Wider.tenant_id is Context.tenant_id


def test_non_context_param_annotation_is_rejected():
    with pytest.raises(TypeError, match="ContextParam"):

        class _Bad(fr.ContextNamespace):
            x: int


def test_non_context_param_assignment_is_rejected():
    with pytest.raises(TypeError, match="ContextParam"):

        class _Bad(fr.ContextNamespace):
            x = 3


def test_direct_construction_is_closed():
    with pytest.raises(TypeError, match="ContextNamespace"):
        fr.ContextParam()


def test_member_works_in_expressions_and_markers():
    visible = fr.where_clause(Thing.tenant_id == Context.tenant_id)
    with Context.bind(tenant_id=4):
        sql = str(visible().compile(compile_kwargs={"literal_binds": True}))
    assert "4" in sql


def test_alias_of_a_member_is_an_independent_slot():
    aliased = Context.tenant_id.alias("ctxns_alias")
    with Context.bind(tenant_id=1), aliased.bind(tenant_id=2):
        assert Context.tenant_id() == 1
        assert aliased() == 2


def test_explain_shows_values_origins_and_unbound_members():
    with Context.bind(tenant_id=5):
        rendered = Context.explain()
    assert "tenant_id = 5" in rendered
    assert "test_context_namespace.py" in rendered  # the binding line
    assert "locale: UNBOUND" in rendered


# ---------------------------------------------------------------------------
# depends(): the generated bind dependency
# ---------------------------------------------------------------------------


def test_depends_binds_per_request_and_respects_overrides(client):
    from sqlalchemy.orm import Mapped

    class NsNote(fr.IDBase):
        tenant_id: Mapped[int]
        name: Mapped[str]

    class Ctx(fr.ContextNamespace):
        tenant_id: fr.ContextParam[int]

    class NsNoteClauses(fr.ClauseNamespace):
        model = NsNote

        default_scope = fr.where_clause(NsNote.tenant_id == Ctx.tenant_id)

    class NsNoteSchema(fr.IDSchema):
        tenant_id: int
        name: str

    def get_tenant_id() -> int:
        return 1

    @fr.include_view(client.app)
    class NsNoteView(fr.AsyncRestView):
        prefix = "/ns-notes"
        model = NsNote
        schema = NsNoteSchema
        dependencies = [Ctx.depends(tenant_id=get_tenant_id)]

    from .conftest import create_tables

    create_tables()

    client.post("/ns-notes/", json={"tenant_id": 1, "name": "mine"})
    client.post("/ns-notes/", json={"tenant_id": 2, "name": "theirs"})

    listed = client.get("/ns-notes/").json()
    assert [row["name"] for row in listed["data"]] == ["mine"]

    # the sources are the caller's own dependencies, so overrides work
    client.app.dependency_overrides[get_tenant_id] = lambda: 2
    try:
        listed = client.get("/ns-notes/").json()
        assert [row["name"] for row in listed["data"]] == ["theirs"]
    finally:
        client.app.dependency_overrides.pop(get_tenant_id)


def test_depends_rejects_an_unknown_member_and_a_non_dependency_source():
    with pytest.raises(TypeError, match="no member"):
        Context.depends(nope=lambda: 1)
    with pytest.raises(TypeError, match="dependency"):
        Context.tenant_id.depends(42)


def test_depends_origin_names_the_declaration_line():
    import asyncio

    dependency = Context.tenant_id.depends(lambda: 11)  # <- the origin line

    async def run():
        agen = dependency.dependency(tenant_id=11)
        await agen.__anext__()  # enter: the bind is now active
        try:
            _, origin = Context.tenant_id._param_fn.bindings()["tenant_id"]
            assert origin is not None
            assert "test_context_namespace.py" in origin
        finally:
            await agen.aclose()

    asyncio.run(run())
