from datetime import datetime

import pytest
from sqlalchemy import (
    ColumnElement,
    ForeignKey,
    bindparam,
    delete,
    exists,
    select,
    true,
    update,
)
from sqlalchemy import false as sql_false
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from fastapi_restly.clauses import (
    UNSCOPED,
    ClauseNamespace,
    ContextNamespace,
    ContextParam,
    WhereClause,
    all_of,
    any_of,
    apply_clauses,
    none_of,
    where_clause,
)
from fastapi_restly.clauses._scopes import _NAMESPACES


class Base(DeclarativeBase):
    pass


class Collection(Base):
    __tablename__ = "collection"

    id: Mapped[int] = mapped_column(primary_key=True)
    archived_at: Mapped[datetime | None]


class Item(Base):
    __tablename__ = "item"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int]
    collection_id: Mapped[int] = mapped_column(ForeignKey("collection.id"))
    created_at: Mapped[datetime | None]
    deleted_at: Mapped[datetime | None]


class Ctx(ContextNamespace):
    tenant_id: ContextParam[int]


owned_by_tenant = where_clause(Item.tenant_id == Ctx.tenant_id)

soft_deleted = where_clause(Item.deleted_at.is_(None))

collection_active = where_clause(Collection.archived_at.is_(None))


@where_clause
def owned_by_tenant_in_body() -> ColumnElement[bool]:
    return Item.tenant_id == Ctx.tenant_id()


def params_of(stmt):
    return stmt.compile().params


def values_of(stmt):
    # a member's placeholder is a unique bind: its name gets a number
    return sorted(stmt.compile().params.values())


# --- basics ---------------------------------------------------------------


def test_bare_condition():
    stmt = apply_clauses(select(Item), soft_deleted)
    assert "deleted_at IS NULL" in str(stmt)


def test_member_binding():
    with Ctx.bind(tenant_id=7):
        stmt = apply_clauses(select(Item), owned_by_tenant)
    assert 7 in params_of(stmt).values()


def test_unbound_member_raises():
    with pytest.raises(LookupError):
        apply_clauses(select(Item), owned_by_tenant)


def test_statement_carries_its_values_past_the_bind():
    with Ctx.bind(tenant_id=7):
        stmt = apply_clauses(select(Item), owned_by_tenant)
    # the bind has ended: the value was read when the clause was applied
    assert values_of(stmt) == [7]


def test_clause_is_not_a_boolean():
    with pytest.raises(TypeError, match="not a boolean"):
        bool(soft_deleted)
    with pytest.raises(TypeError, match="not a boolean"):
        if soft_deleted:
            pass


def test_direct_construction_is_closed():
    with pytest.raises(TypeError, match="where_clause"):
        WhereClause()


# --- one bind serves every clause -------------------------------------------


def test_one_bind_serves_every_composite():
    q = any_of(
        all_of(owned_by_tenant, soft_deleted),
        all_of(owned_by_tenant, none_of(soft_deleted)),
    )
    with Ctx.bind(tenant_id=7):
        stmt = apply_clauses(select(Item), q)
    tenant_params = [v for k, v in params_of(stmt).items() if k.startswith("tenant_id")]
    assert tenant_params and all(v == 7 for v in tenant_params)


def test_inner_bind_replaces_the_value_until_it_exits():
    with Ctx.bind(tenant_id=1):
        with Ctx.bind(tenant_id=2):
            inner = apply_clauses(select(Item), owned_by_tenant)
        outer = apply_clauses(select(Item), owned_by_tenant)
    assert values_of(inner) == [2]
    assert values_of(outer) == [1]


# --- clause kinds ---------------------------------------------------------


def test_constructor_types():
    assert isinstance(owned_by_tenant, WhereClause)
    assert isinstance(owned_by_tenant_in_body, WhereClause)
    assert isinstance(soft_deleted, WhereClause)


@pytest.mark.parametrize("op", [all_of, any_of, none_of])
def test_composites_are_where_clauses(op):
    assert isinstance(op(owned_by_tenant, soft_deleted), WhereClause)


# --- calling a WhereClause -------------------------------------------------


def test_call_returns_expression():
    with Ctx.bind(tenant_id=7):
        stmt = select(Item).where(owned_by_tenant())
    assert 7 in params_of(stmt).values()


def test_call_composite():
    q = all_of(owned_by_tenant, soft_deleted)
    with Ctx.bind(tenant_id=7):
        stmt = select(Item).where(q())
    assert 7 in params_of(stmt).values()
    assert "deleted_at IS NULL" in str(stmt)


def test_call_raises_when_the_member_is_unbound():
    with pytest.raises(LookupError, match="tenant_id"):
        owned_by_tenant()


# --- clause functions -------------------------------------------------------


def test_function_clause_reads_the_member_in_its_body():
    with Ctx.bind(tenant_id=7):
        stmt = apply_clauses(select(Item), owned_by_tenant_in_body)
    assert 7 in params_of(stmt).values()
    with pytest.raises(LookupError, match="tenant_id"):
        apply_clauses(select(Item), owned_by_tenant_in_body)


def test_function_clause_runs_each_time_it_is_applied():
    # a Python branch on a bound value: tenant 0 stands for "every tenant"
    @where_clause
    def owned_or_everything() -> ColumnElement[bool]:
        if Ctx.tenant_id() == 0:
            return true()
        return Item.tenant_id == Ctx.tenant_id()

    with Ctx.bind(tenant_id=0):
        everything = apply_clauses(select(Item), owned_or_everything)
    with Ctx.bind(tenant_id=7):
        owned = apply_clauses(select(Item), owned_or_everything)
    assert "tenant_id" not in str(everything.whereclause)
    assert 7 in params_of(owned).values()


def test_embedded_member_and_body_read_see_the_same_value():
    q = all_of(owned_by_tenant, owned_by_tenant_in_body)
    with Ctx.bind(tenant_id=5):
        stmt = apply_clauses(select(Item), q)
    assert set(params_of(stmt).values()) == {5}


@pytest.mark.parametrize(
    "make",
    [
        lambda: where_clause(lambda tenant_id: Item.tenant_id == tenant_id),
        lambda: where_clause(lambda tenant_id=0: Item.tenant_id == tenant_id),
        lambda: where_clause(lambda *rest: Item.tenant_id == 1),
        lambda: where_clause(lambda **kw: Item.tenant_id == 1),
    ],
    ids=["positional", "default", "var-positional", "var-keyword"],
)
def test_clause_function_with_parameters_is_rejected(make):
    with pytest.raises(TypeError, match="a clause function takes none"):
        make()


def test_rejected_parameters_point_at_the_replacements():
    with pytest.raises(TypeError) as error:

        @where_clause
        def f(tenant_id: int) -> ColumnElement[bool]:
            return Item.tenant_id == tenant_id

    message = str(error.value)
    assert "f takes parameters (tenant_id)" in message
    assert "ContextNamespace" in message
    assert "where_clause(Model.column == value)" in message


def test_async_clause_function_rejected():
    with pytest.raises(TypeError, match="sync"):

        @where_clause
        async def f() -> ColumnElement[bool]:
            return Item.tenant_id == 1


def test_value_known_at_the_call_site_needs_no_clause_function():
    def in_collection(collection_id: int) -> ColumnElement[bool]:
        return Item.collection_id == collection_id

    q = all_of(owned_by_tenant, where_clause(in_collection(3)))
    with Ctx.bind(tenant_id=7):
        stmt = apply_clauses(select(Item), q)
    assert set(params_of(stmt).values()) == {3, 7}


# --- cross-model validation -----------------------------------------------


def test_cross_model_clause_rejected():
    with pytest.raises(TypeError, match="collection"):
        apply_clauses(select(Item), collection_active)


def test_exists_subquery_not_a_false_positive():
    has_collection = where_clause(exists().where(Collection.id == Item.collection_id))
    stmt = apply_clauses(select(Item), has_collection)
    assert "EXISTS" in str(stmt)


def test_cross_model_rejected_on_update():
    with pytest.raises(TypeError, match="collection"):
        apply_clauses(update(Item), collection_active)


def test_cross_model_error_shows_plain_table_name():
    with pytest.raises(TypeError, match=r"not in the statement: collection;"):
        apply_clauses(select(Item), collection_active)


# --- update / delete statements -------------------------------------------


def test_update_with_wheres():
    with Ctx.bind(tenant_id=5):
        stmt = apply_clauses(update(Item), owned_by_tenant, soft_deleted).values(
            collection_id=6
        )
    s = str(stmt)
    assert s.startswith("UPDATE item")
    assert "deleted_at IS NULL" in s
    assert 5 in params_of(stmt).values()


def test_delete_with_wheres():
    with Ctx.bind(tenant_id=5):
        stmt = apply_clauses(delete(Item), owned_by_tenant)
    assert str(stmt).startswith("DELETE FROM item")
    assert 5 in params_of(stmt).values()


# --- apply_clauses operands --------------------------------------------------


@pytest.mark.parametrize("invalid", [None, "not a clause", object()])
def test_apply_clauses_rejects_invalid_clause_operands(invalid):
    with pytest.raises(TypeError, match="accepts only clauses or UNSCOPED"):
        apply_clauses(select(Item), UNSCOPED, invalid)


def test_a_member_is_not_a_clause():
    with pytest.raises(TypeError, match="a ContextParam carries a value"):
        apply_clauses(select(Item), Ctx.tenant_id)  # type: ignore[arg-type]


def test_apply_clauses_stmt_is_positional_only():
    with pytest.raises(TypeError):
        apply_clauses(stmt=select(Item))  # type: ignore[call-overload]


# --- clause namespaces -----------------------------------------------------


class Gadget(Base):
    __tablename__ = "gadget"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int]
    deleted_at: Mapped[datetime | None]


class GadgetClauses(ClauseNamespace):
    model = Gadget

    is_deleted = where_clause(Gadget.deleted_at.is_not(None))
    owned_by_tenant = where_clause(Gadget.tenant_id == Ctx.tenant_id)

    visible = all_of(owned_by_tenant, none_of(is_deleted))


def test_namespace_leaves_the_model_untouched():
    assert not hasattr(Gadget, "C")
    assert isinstance(GadgetClauses.visible, WhereClause)


def test_namespace_clauses_work():
    with Ctx.bind(tenant_id=7):
        stmt = apply_clauses(select(Gadget), GadgetClauses.visible)
    assert 7 in params_of(stmt).values()
    assert "deleted_at IS NULL" in str(stmt)


def test_namespace_function_clause_with_staticmethod():
    """The typed in-body spelling: the clause decorator over @staticmethod.

    A type checker reads a bare def in a class body as a method and
    reports the missing self; the staticmethod marker suppresses that,
    and where_clause unwraps it.
    """

    class Gear(Base):
        __tablename__ = "gear_staticmethod"
        id: Mapped[int] = mapped_column(primary_key=True)
        tenant_id: Mapped[int]

    class GearClauses(ClauseNamespace):
        model = Gear

        @where_clause
        @staticmethod
        def owned_by_tenant() -> ColumnElement[bool]:
            return Gear.tenant_id == Ctx.tenant_id()

        visible = all_of(owned_by_tenant)

    assert isinstance(GearClauses.owned_by_tenant, WhereClause)
    with Ctx.bind(tenant_id=7):
        stmt = apply_clauses(select(Gear), GearClauses.owned_by_tenant)
    assert 7 in params_of(stmt).values()


def test_namespace_without_model_is_a_plain_group():
    # a clause needs no model: the namespace validates and registers nothing
    class NoModelClauses(ClauseNamespace):
        something = where_clause(Item.deleted_at.is_(None))

    assert isinstance(NoModelClauses.something, WhereClause)
    assert NoModelClauses not in _NAMESPACES.values()


def test_namespace_rejects_bare_expression():
    class Widget(Base):
        __tablename__ = "widget"
        id: Mapped[int] = mapped_column(primary_key=True)
        deleted_at: Mapped[datetime | None]

    with pytest.raises(TypeError, match="where_clause"):

        class WidgetClauses(ClauseNamespace):
            model = Widget
            is_deleted = Widget.deleted_at.is_not(
                None
            )  # forgot the where_clause() wrapper


def test_namespace_rejects_a_context_member():
    with pytest.raises(TypeError, match="not a clause"):

        class _MemberInside(ClauseNamespace):
            tenant = Ctx.tenant_id


def test_namespace_double_attach_rejected():
    with pytest.raises(TypeError, match="already has"):

        class SecondGadgetClauses(ClauseNamespace):
            model = Gadget
            anything = where_clause(Gadget.deleted_at.is_(None))


def test_unrelated_C_attribute_does_not_collide():
    class Odd(Base):
        __tablename__ = "odd_c_attr"
        id: Mapped[int] = mapped_column(primary_key=True)

    Odd.C = 5  # unrelated user attribute; the registry ignores it

    class OddClauses(ClauseNamespace):
        model = Odd
        anything = where_clause(Odd.id == 1)

    assert Odd.C == 5
    assert isinstance(OddClauses.anything, WhereClause)


def test_namespace_registers_on_restly_idbase():
    from sqlalchemy.orm import Mapped as M
    from sqlalchemy.orm import mapped_column as mc

    import fastapi_restly as fr
    from fastapi_restly.clauses._scopes import _default_scope

    class Gizmo(fr.IDBase):
        __tablename__ = "gizmo_ns_test"
        tenant_id: M[int] = mc()

    class GizmoClauses(ClauseNamespace):
        model = Gizmo
        owned = where_clause(Gizmo.tenant_id == 1)
        default_scope = owned

    assert not hasattr(Gizmo, "C")
    assert _default_scope(Gizmo) is GizmoClauses.owned


# --- members embedded in a condition -----------------------------------------


class Other(ContextNamespace):
    """A second, local namespace that reuses the name `tenant_id`."""

    tenant_id: ContextParam[int]
    wanted: ContextParam[list[int]]


def test_embedded_member_in_bare_condition():
    with Ctx.bind(tenant_id=7):
        stmt = apply_clauses(select(Item), owned_by_tenant)
    assert 7 in params_of(stmt).values()
    with pytest.raises(LookupError, match="tenant_id"):
        apply_clauses(select(Item), owned_by_tenant)


def test_embedded_member_shared_across_clauses():
    on_item = where_clause(Item.tenant_id == Ctx.tenant_id)
    on_collection = where_clause(Collection.id == Ctx.tenant_id)
    with Ctx.bind(tenant_id=7):
        item_stmt = apply_clauses(select(Item), on_item)
        collection_stmt = apply_clauses(select(Collection), on_collection)
    assert 7 in params_of(item_stmt).values()
    assert 7 in params_of(collection_stmt).values()


def test_one_member_twice_in_one_tree():
    a = where_clause(Item.tenant_id == Ctx.tenant_id)
    b = where_clause(Item.collection_id == Ctx.tenant_id)
    q = all_of(a, b)
    with Ctx.bind(tenant_id=5):
        stmt = apply_clauses(select(Item), q)
    # one member is one parameter, however often the statement uses it
    assert values_of(stmt) == [5]
    assert str(stmt).count(":tenant_id_1") == 2


def test_embedded_member_in_exists_subquery():
    cond = where_clause(exists().where(Collection.id == Ctx.tenant_id))
    with Ctx.bind(tenant_id=3):
        stmt = apply_clauses(select(Item), cond)
    assert 3 in params_of(stmt).values()


def test_embedded_member_in_in_list():
    cond = where_clause(Item.tenant_id.in_(Other.wanted))
    with Other.bind(wanted=[1, 2]):
        stmt = apply_clauses(select(Item), cond)
    assert values_of(stmt) == [[1, 2]]


def test_embedded_member_in_an_expression_a_function_returns():
    @where_clause
    def owned() -> ColumnElement[bool]:
        return Item.tenant_id == Ctx.tenant_id  # embedded, not called

    with Ctx.bind(tenant_id=4):
        stmt = apply_clauses(select(Item), owned)
    assert values_of(stmt) == [4]


def test_member_rejected_in_boolean_composites():
    for op in (all_of, any_of, none_of):
        with pytest.raises(TypeError, match="a ContextParam carries a value"):
            op(Ctx.tenant_id, soft_deleted)  # type: ignore[arg-type]


# --- member names are local to their namespace ---------------------------------


def test_two_members_same_name_in_one_expression():
    cond = where_clause(
        (Item.tenant_id == Ctx.tenant_id) | (Item.collection_id == Other.tenant_id)
    )
    with Ctx.bind(tenant_id=1), Other.bind(tenant_id=2):
        stmt = apply_clauses(select(Item), cond)
    assert values_of(stmt) == [1, 2]
    assert "item.tenant_id = :tenant_id_1" in str(stmt)
    assert "item.collection_id = :tenant_id_2" in str(stmt)


def test_same_name_members_across_clauses():
    ca = where_clause(Item.tenant_id == Ctx.tenant_id)
    cb = where_clause(Item.collection_id == Other.tenant_id)
    with Ctx.bind(tenant_id=1), Other.bind(tenant_id=0):
        stmt = apply_clauses(select(Item), ca, cb)
    assert values_of(stmt) == [0, 1]


def test_one_member_across_clauses():
    ca = where_clause(Item.tenant_id == Ctx.tenant_id)
    cb = where_clause(Item.collection_id == Ctx.tenant_id)
    with Ctx.bind(tenant_id=7):
        stmt = apply_clauses(select(Item), ca, cb)
    assert values_of(stmt) == [7]


def test_member_beside_a_handwritten_bindparam_of_the_same_name():
    cb = where_clause(Item.collection_id == bindparam("tenant_id"))
    with Ctx.bind(tenant_id=1):
        stmt = apply_clauses(select(Item), owned_by_tenant, cb)
    # the hand-written bind keeps its name and still waits for its value
    assert stmt.compile().params == {"tenant_id_1": 1, "tenant_id": None}
    assert stmt.compile().construct_params({"tenant_id": 99}) == {
        "tenant_id_1": 1,
        "tenant_id": 99,
    }


def test_layered_apply_with_same_name_members():
    cb = where_clause(Item.collection_id == Other.tenant_id)
    with Ctx.bind(tenant_id=1), Other.bind(tenant_id=2):
        stmt = apply_clauses(apply_clauses(select(Item), owned_by_tenant), cb)
    assert values_of(stmt) == [1, 2]


def test_layered_apply_with_one_member():
    cb = where_clause(Item.collection_id == Ctx.tenant_id)
    with Ctx.bind(tenant_id=5):
        stmt = apply_clauses(apply_clauses(select(Item), owned_by_tenant), cb)
    assert values_of(stmt) == [5]


def test_a_bind_the_statement_already_carries_keeps_its_value():
    stmt = select(Item).where(Item.collection_id == bindparam("tenant_id", value=99))
    with Ctx.bind(tenant_id=1):
        stmt = apply_clauses(stmt, owned_by_tenant)
    assert stmt.compile().params == {"tenant_id": 99, "tenant_id_1": 1}


def test_member_named_like_an_updated_column():
    # a plain bind named tenant_id would collide with the SET tenant_id bind
    with Ctx.bind(tenant_id=5):
        stmt = apply_clauses(update(Item), owned_by_tenant).values(tenant_id=6)
    assert values_of(stmt) == [5, 6]
    assert "SET tenant_id=" in str(stmt)


def test_applying_a_clause_leaves_its_condition_unfilled():
    # the module-level condition is shared: a fill must not write into it
    with Ctx.bind(tenant_id=1):
        first = apply_clauses(select(Item), owned_by_tenant)
    with Ctx.bind(tenant_id=2):
        second = apply_clauses(select(Item), owned_by_tenant)
    assert values_of(first) == [1]
    assert values_of(second) == [2]


# --- self-revealing errors and reprs ---------------------------------------


def test_unbound_error_teaches_bind():
    with pytest.raises(LookupError, match=r"Ctx\.bind\(tenant_id=") as error:
        apply_clauses(select(Item), owned_by_tenant)
    assert "Ctx.depends(tenant_id=" in str(error.value)


def test_unbound_member_error_teaches_bind():
    with pytest.raises(LookupError, match=r"Ctx\.bind\(tenant_id="):
        Ctx.tenant_id()


def test_repr_shows_kind_and_name():
    assert repr(owned_by_tenant_in_body) == "<WhereClause owned_by_tenant_in_body>"
    assert repr(soft_deleted) == "<WhereClause item.deleted_at IS NULL>"
    assert repr(owned_by_tenant) == "<WhereClause item.tenant_id = :tenant_id_1>"
    assert repr(all_of(owned_by_tenant, soft_deleted)) == "<WhereClause all_of>"


def test_repr_of_a_long_condition_is_cut():
    long = where_clause(
        (Item.deleted_at.is_(None)) & (Item.created_at.is_not(None)) & (Item.id > 0)
    )
    assert len(repr(long)) <= len("<WhereClause >") + 50
    assert repr(long).endswith("...>")


def test_repr_of_member():
    assert repr(Ctx.tenant_id) == "<ContextParam Ctx.tenant_id>"


def test_member_repr_names_bind_site():
    with Ctx.bind(tenant_id=1):
        text = repr(Ctx.tenant_id)
    assert text.startswith("<ContextParam Ctx.tenant_id, bound at ")
    assert "test_clauses.py" in text
    assert repr(Ctx.tenant_id) == "<ContextParam Ctx.tenant_id>"  # reset after exit


# --- composition ----------------------------------------------------------


def test_none_of():
    stmt = apply_clauses(select(Item), none_of(soft_deleted))
    assert "deleted_at IS NOT NULL" in str(stmt) or "NOT" in str(stmt)


def test_none_of_multiple_operands():
    q = none_of(soft_deleted, owned_by_tenant)
    with Ctx.bind(tenant_id=1):
        stmt = apply_clauses(select(Item), q)
    s = str(stmt)
    assert "NOT" in s and "OR" in s


def test_empty_composites_rejected():
    for op in (all_of, any_of, none_of):
        with pytest.raises(TypeError, match="at least one"):
            op()


def test_nested_composition():
    q = all_of(owned_by_tenant, any_of(soft_deleted, none_of(soft_deleted)))
    with Ctx.bind(tenant_id=3):
        stmt = apply_clauses(select(Item), q)
    s = str(stmt)
    assert "OR" in s and 3 in params_of(stmt).values()


def test_composite_of_a_composite_resolves():
    inner = all_of(owned_by_tenant, soft_deleted)
    outer = all_of(inner, inner)  # the same subtree twice
    with Ctx.bind(tenant_id=2):
        stmt = apply_clauses(select(Item), outer)
    assert values_of(stmt) == [2]


# --- UNSCOPED composition -------------------------------------------------


@pytest.mark.parametrize("args", [(UNSCOPED,), (UNSCOPED, UNSCOPED)])
def test_unscoped_all_and_any_preserve_the_sentinel(args):
    assert all_of(*args) is UNSCOPED
    assert any_of(*args) is UNSCOPED


@pytest.mark.parametrize(
    "args",
    [
        (UNSCOPED, owned_by_tenant),
        (owned_by_tenant, UNSCOPED),
        (UNSCOPED, owned_by_tenant, UNSCOPED),
    ],
)
def test_unscoped_all_returns_the_remaining_clause_itself(args):
    clause = all_of(*args)
    assert clause is owned_by_tenant
    with Ctx.bind(tenant_id=7):
        stmt = apply_clauses(select(Item), clause)
    assert 7 in params_of(stmt).values()


@pytest.mark.parametrize("op", [any_of, none_of])
@pytest.mark.parametrize("reverse", [False, True])
def test_unscoped_or_and_not_do_not_read_an_unneeded_member(op, reverse):
    args = (owned_by_tenant, UNSCOPED) if reverse else (UNSCOPED, owned_by_tenant)
    clause = op(*args)
    if op is any_of:
        assert clause is UNSCOPED
    else:
        assert isinstance(clause, WhereClause)
        # nothing is bound: the discarded operand is never resolved
        assert clause().compare(sql_false())


def test_unscoped_none_alone_is_a_false_where_clause():
    clause = none_of(UNSCOPED)
    assert isinstance(clause, WhereClause)
    assert clause().compare(sql_false())


@pytest.mark.parametrize("op", [all_of, any_of, none_of])
def test_unscoped_does_not_hide_invalid_composition_operands(op):
    for invalid in (None, "not a clause", Ctx.tenant_id):
        with pytest.raises(TypeError):
            op(UNSCOPED, invalid)


def test_unscoped_nested_composition_and_existing_statement_filter():
    clause = all_of(any_of(UNSCOPED, owned_by_tenant), soft_deleted)
    assert clause is soft_deleted
    stmt = apply_clauses(select(Item).where(Item.id == 9), UNSCOPED, clause)
    assert "item.id =" in str(stmt)
    assert "deleted_at IS NULL" in str(stmt)
    assert list(params_of(stmt).values()) == [9]
