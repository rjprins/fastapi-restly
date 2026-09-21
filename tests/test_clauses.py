from datetime import datetime

import pytest
from sqlalchemy import ColumnElement, ForeignKey, delete, func, select, update
from sqlalchemy import false as sql_false
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# This file unit-tests the slot primitive itself; consumer code declares
# slots in a ContextNamespace (see test_context_namespace.py).
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
from fastapi_restly.clauses._declarations import (  # noqa: E402
    _context_param as context_param,
)
from fastapi_restly.clauses._scopes import _NAMESPACES  # noqa: E402


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


@where_clause
def tenant_filter(tenant_id: int) -> ColumnElement[bool]:
    return Item.tenant_id == tenant_id


class Ctx(ContextNamespace):
    tenant_id: ContextParam[int]


owned_by_tenant = where_clause(Item.tenant_id == Ctx.tenant_id)

soft_deleted = where_clause(Item.deleted_at.is_(None))


@where_clause
def in_period(start: int, end: int) -> ColumnElement[bool]:
    return Item.created_at.between(start, end)


collection_active = where_clause(Collection.archived_at.is_(None))


def params_of(stmt):
    return stmt.compile().params


# --- basics ---------------------------------------------------------------


def test_bare_condition():
    stmt = apply_clauses(select(Item), soft_deleted)
    assert "deleted_at IS NULL" in str(stmt)


def test_leaf_context_binding():
    with tenant_filter.bind(tenant_id=7):
        stmt = apply_clauses(select(Item), tenant_filter)
    assert 7 in params_of(stmt).values()


def test_unbound_context_param_raises():
    with pytest.raises(LookupError):
        apply_clauses(select(Item), tenant_filter)


def test_clause_is_not_a_boolean():
    with pytest.raises(TypeError, match="not a boolean"):
        bool(soft_deleted)
    with pytest.raises(TypeError, match="not a boolean"):
        if soft_deleted:
            pass


# --- tree routing ---------------------------------------------------------


def test_tree_context_routes_to_leaf():
    project = all_of(tenant_filter, soft_deleted)
    with project.bind(tenant_id=7):
        stmt = apply_clauses(select(Item), project)
    assert 7 in params_of(stmt).values()


def test_tree_and_leaf_binding_equivalent():
    project = all_of(tenant_filter, soft_deleted)
    with tenant_filter.bind(tenant_id=7):
        via_leaf = apply_clauses(select(Item), project)
    with project.bind(tenant_id=7):
        via_tree = apply_clauses(select(Item), project)
    assert str(via_leaf) == str(via_tree)
    assert params_of(via_leaf) == params_of(via_tree)


def test_unclaimed_key_raises():
    project = all_of(tenant_filter, soft_deleted)
    with pytest.raises(TypeError, match="nope"):
        with project.bind(nope=1):
            pass


def test_shared_leaf_in_two_branches_binds_once():
    q = any_of(
        all_of(tenant_filter, soft_deleted),
        all_of(tenant_filter, none_of(soft_deleted)),
    )
    with q.bind(tenant_id=7):
        stmt = apply_clauses(select(Item), q)
    tenant_params = [v for k, v in params_of(stmt).items() if k.startswith("tenant_id")]
    assert tenant_params and all(v == 7 for v in tenant_params)


def test_unrelated_families_same_name_raises():
    @where_clause
    def f1(limit: int) -> ColumnElement[bool]:
        return Item.tenant_id < limit

    @where_clause
    def f2(limit: int) -> ColumnElement[bool]:
        return Item.collection_id < limit

    with pytest.raises(TypeError, match="unrelated"):
        with all_of(f1, f2).bind(limit=5):
            pass


# --- clause kinds ---------------------------------------------------------


def test_constructor_types():
    assert isinstance(tenant_filter, WhereClause)
    assert isinstance(soft_deleted, WhereClause)


@pytest.mark.parametrize("op", [all_of, any_of, none_of])
def test_composites_are_where_clauses(op):
    assert isinstance(op(tenant_filter, soft_deleted), WhereClause)


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


def test_call_uses_ambient_bind():
    with tenant_filter.bind(tenant_id=7):
        expr = tenant_filter()
    assert "tenant_id" in str(expr)


def test_body_read_clause_is_ambient_only():
    """A slot called inside the body resolves ambiently; routing cannot see it."""
    slot = context_param("body_tenant", int)

    @where_clause
    def body_read() -> ColumnElement[bool]:
        return Item.tenant_id == slot()

    with slot.bind(body_tenant=7):
        stmt = body_read.select(Item)
        assert 7 in params_of(stmt).values()

    with pytest.raises(TypeError, match="no clause accepts"):
        with body_read.bind(body_tenant=7):
            pass


# --- cross-model validation -----------------------------------------------


def test_cross_model_clause_rejected():
    with pytest.raises(TypeError, match="collection"):
        apply_clauses(select(Item), collection_active)


def test_exists_subquery_not_a_false_positive():
    from sqlalchemy import exists

    has_collection = where_clause(exists().where(Collection.id == Item.collection_id))
    stmt = apply_clauses(select(Item), has_collection)
    assert "EXISTS" in str(stmt)


def test_cross_model_rejected_on_update():
    with pytest.raises(TypeError, match="collection"):
        apply_clauses(update(Item), collection_active)


# --- update / delete statements -------------------------------------------


def test_update_with_wheres():
    with tenant_filter.bind(tenant_id=5):
        stmt = apply_clauses(update(Item), tenant_filter, soft_deleted).values(
            tenant_id=6
        )
    s = str(stmt)
    assert s.startswith("UPDATE item")
    assert "deleted_at IS NULL" in s
    assert 5 in params_of(stmt).values()


def test_delete_with_wheres():
    with tenant_filter.bind(tenant_id=5):
        stmt = apply_clauses(delete(Item), tenant_filter)
    assert str(stmt).startswith("DELETE FROM item")
    assert 5 in params_of(stmt).values()


# --- Clause methods --------------------------------------------------------


def test_select_method_uses_ambient_bind():
    q = all_of(tenant_filter, soft_deleted)
    with q.bind(tenant_id=7):
        stmt = q.select(Item)
    assert 7 in params_of(stmt).values()


def test_update_method():
    with Ctx.bind(tenant_id=5):
        stmt = owned_by_tenant.update(Item).values(collection_id=6)
    assert str(stmt).startswith("UPDATE item")
    assert 5 in params_of(stmt).values()


def test_delete_method():
    with Ctx.bind(tenant_id=5):
        stmt = owned_by_tenant.delete(Item)
    assert str(stmt).startswith("DELETE FROM item")
    assert 5 in params_of(stmt).values()


def test_select_method_takes_sqlalchemy_entities():
    with Ctx.bind(tenant_id=7):
        stmt = owned_by_tenant.select(Item.id, Item.tenant_id)
        count_stmt = owned_by_tenant.select(func.count(Item.id))
    assert "item.id" in str(stmt)
    assert 7 in params_of(stmt).values()
    assert "count(item.id)" in str(count_stmt)


def test_select_method_no_entities_raises():
    # empty select brings no FROMs, so the table validation catches it
    with Ctx.bind(tenant_id=7):
        with pytest.raises(TypeError, match="not in the statement"):
            owned_by_tenant.select()


# --- apply_clauses operands --------------------------------------------------


@pytest.mark.parametrize("invalid", [None, "not a clause", object()])
def test_apply_clauses_rejects_invalid_clause_operands(invalid):
    with pytest.raises(TypeError, match="accepts only clauses or UNSCOPED"):
        apply_clauses(select(Item), UNSCOPED, invalid)


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
    checks its first parameter against the class; the staticmethod
    marker suppresses that, and the clause constructors unwrap it.
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


def test_namespace_double_attach_rejected():
    with pytest.raises(TypeError, match="already has"):

        class SecondGadgetClauses(ClauseNamespace):
            model = Gadget
            anything = where_clause(Gadget.deleted_at.is_(None))


# --- shared params ---------------------------------------------------------


def test_param_unbound_raises_with_name():
    slot = context_param("tenant_id")
    with pytest.raises(LookupError, match="tenant_id"):
        slot()


def test_embedded_slot_in_bare_condition():
    slot = context_param("embed_tenant")
    owned = where_clause(Item.tenant_id == slot)
    with slot.bind(embed_tenant=7):
        stmt = apply_clauses(select(Item), owned)
    assert 7 in params_of(stmt).values()
    with pytest.raises(LookupError, match="embed_tenant"):
        apply_clauses(select(Item), owned)


def test_embedded_slot_visible_in_repr():
    slot = context_param("repr_tenant")
    owned = where_clause(Item.tenant_id == slot)
    assert (
        repr(owned) == "<WhereClause item.tenant_id = :repr_tenant binds: repr_tenant>"
    )


def test_embedded_slot_shared_across_clauses():
    slot = context_param("share_tenant")
    on_item = where_clause(Item.tenant_id == slot)
    on_collection = where_clause(Collection.id == slot)
    with on_item.bind(share_tenant=7):
        item_stmt = apply_clauses(select(Item), on_item)
        collection_stmt = apply_clauses(select(Collection), on_collection)
    assert 7 in params_of(item_stmt).values()
    assert 7 in params_of(collection_stmt).values()


def test_embedded_slot_not_ambiguous_in_one_tree():
    slot = context_param("tree_tenant")
    a = where_clause(Item.tenant_id == slot)
    b = where_clause(Item.collection_id == slot)
    q = all_of(a, b)
    with q.bind(tree_tenant=5):
        stmt = apply_clauses(select(Item), q)
    assert params_of(stmt) == {"tree_tenant": 5}
    assert str(stmt).count(":tree_tenant") == 2


def test_embedded_slot_in_exists_subquery():
    from sqlalchemy import exists

    slot = context_param("exists_tenant")
    cond = where_clause(exists().where(Collection.id == slot))
    with slot.bind(exists_tenant=3):
        stmt = apply_clauses(select(Item), cond)
    assert 3 in params_of(stmt).values()


def test_two_slots_same_name_in_one_expression_raises():
    a = context_param("dupname")
    b = context_param("dupname")
    cond = where_clause((Item.tenant_id == a) | (Item.collection_id == b))
    with pytest.raises(TypeError, match="dupname"):
        with a.bind(dupname=1), b.bind(dupname=2):
            apply_clauses(select(Item), cond)


def test_embedded_slot_in_in_list():
    slot = context_param("status_list")
    cond = where_clause(Item.tenant_id.in_(slot))
    with slot.bind(status_list=[1, 2]):
        stmt = apply_clauses(select(Item), cond)
    assert params_of(stmt)["status_list"] == [1, 2]


def test_param_rejected_in_boolean_composites():
    slot = context_param("nope_tenant")
    with pytest.raises(TypeError, match="where"):
        all_of(slot, soft_deleted)


# --- review round 1: regression tests ---------------------------------------


def test_cross_clause_same_name_slots_raise():
    a = context_param("xdup")
    b = context_param("xdup")
    ca = where_clause(Item.tenant_id == a)
    cb = where_clause(Item.collection_id == b)
    with a.bind(xdup=1), b.bind(xdup=0):
        with pytest.raises(TypeError, match="xdup"):
            apply_clauses(select(Item), ca, cb)


def test_cross_clause_shared_slot_is_fine():
    slot = context_param("xshared")
    ca = where_clause(Item.tenant_id == slot)
    cb = where_clause(Item.collection_id == slot)
    with slot.bind(xshared=7):
        stmt = apply_clauses(select(Item), ca, cb)
    assert params_of(stmt) == {"xshared": 7}


def test_slot_colliding_with_handwritten_bindparam_raises():
    from sqlalchemy import bindparam

    slot = context_param("xhand")
    ca = where_clause(Item.tenant_id == slot)
    cb = where_clause(Item.collection_id == bindparam("xhand"))
    with slot.bind(xhand=1):
        with pytest.raises(TypeError, match="hand-written"):
            apply_clauses(select(Item), ca, cb)


def test_contributing_nothing_is_rejected():
    slot = context_param("xnothing")
    with pytest.raises(TypeError, match="contributes no"):
        apply_clauses(select(Item), slot)


def test_default_parameter_rejected():
    with pytest.raises(TypeError, match="default"):

        @where_clause
        def f(tenant_id: int = 0) -> ColumnElement[bool]:
            return Item.tenant_id == tenant_id


def test_variadic_parameters_rejected():
    with pytest.raises(TypeError, match="variadic"):

        @where_clause
        def f(**kw) -> ColumnElement[bool]:
            return Item.tenant_id == kw["t"]


def test_async_clause_function_rejected():
    with pytest.raises(TypeError, match="sync"):

        @where_clause
        async def f(tenant_id: int) -> ColumnElement[bool]:
            return Item.tenant_id == tenant_id


def test_unresolvable_annotation_tolerated():
    @where_clause
    def f(tenant_id: "NoSuchTypeAnywhere") -> ColumnElement[bool]:  # noqa: F821
        return Item.tenant_id == tenant_id

    with f.bind(tenant_id=3):
        stmt = apply_clauses(select(Item), f)
    assert 3 in params_of(stmt).values()


def test_dag_composition_binds_fast():
    q = all_of(tenant_filter, soft_deleted)
    for _ in range(22):  # zonder visited-set: 2**22 walks
        q = all_of(q, q)
    with q.bind(tenant_id=1):
        pass


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


# --- self-revealing errors and reprs ---------------------------------------


def test_unbound_error_teaches_bind():
    with pytest.raises(LookupError, match=r"\.bind\(tenant_id="):
        apply_clauses(select(Item), tenant_filter)


def test_unbound_param_error_teaches_bind():
    slot = context_param("teach_tenant")
    with pytest.raises(LookupError, match=r"\.bind\(teach_tenant="):
        slot()


def test_repr_shows_kind_name_and_binds():
    assert repr(tenant_filter) == "<WhereClause tenant_filter binds: tenant_id>"
    assert repr(soft_deleted) == "<WhereClause item.deleted_at IS NULL>"


def test_repr_of_composite_aggregates_binds():
    q = all_of(tenant_filter, in_period)
    assert repr(q) == "<WhereClause all_of binds: end, start, tenant_id>"


def test_repr_of_param():
    assert repr(context_param("tenant_id")) == "<ContextParam tenant_id>"


# --- provenance ---------------------------------------------------------------


def test_context_param_repr_names_bind_site():
    slot = context_param("prov_repr")
    assert repr(slot) == "<ContextParam prov_repr>"
    with slot.bind(prov_repr=1):
        text = repr(slot)
    assert text.startswith("<ContextParam prov_repr, bound at ")
    assert "test_clauses.py" in text
    assert repr(slot) == "<ContextParam prov_repr>"  # reset after exit


# --- review round 2: statement-wide ownership -------------------------------


def test_layered_apply_same_name_slots_raise():
    a = context_param("layer_t")
    b = context_param("layer_t")
    ca = where_clause(Item.tenant_id == a)
    cb = where_clause(Item.collection_id == b)
    with a.bind(layer_t=1), b.bind(layer_t=2):
        stmt = apply_clauses(select(Item), ca)
        with pytest.raises(TypeError, match="layer_t"):
            apply_clauses(stmt, cb)


def test_layered_apply_shared_slot_is_fine():
    slot = context_param("layer_shared")
    ca = where_clause(Item.tenant_id == slot)
    cb = where_clause(Item.collection_id == slot)
    with slot.bind(layer_shared=5):
        stmt = apply_clauses(apply_clauses(select(Item), ca), cb)
    assert params_of(stmt) == {"layer_shared": 5}


def test_statement_bindparam_protected_from_slot_fill():
    from sqlalchemy import bindparam

    slot = context_param("prot_t")
    clause = where_clause(Item.tenant_id == slot)
    stmt = select(Item).where(Item.collection_id == bindparam("prot_t", value=99))
    with slot.bind(prot_t=1):
        with pytest.raises(TypeError, match="hand-written"):
            apply_clauses(stmt, clause)


def test_cross_model_error_shows_plain_table_name():
    with pytest.raises(TypeError, match=r"not in the statement: collection;"):
        apply_clauses(select(Item), collection_active)


# --- composition ----------------------------------------------------------


def test_none_of():
    stmt = apply_clauses(select(Item), none_of(soft_deleted))
    assert "deleted_at IS NOT NULL" in str(stmt) or "NOT" in str(stmt)


def test_none_of_multiple_operands():
    q = none_of(soft_deleted, tenant_filter)
    with tenant_filter.bind(tenant_id=1):
        stmt = apply_clauses(select(Item), q)
    s = str(stmt)
    assert "NOT" in s and "OR" in s


def test_empty_composites_rejected():
    for op in (all_of, any_of, none_of):
        with pytest.raises(TypeError, match="at least one"):
            op()


def test_nested_composition():
    q = all_of(tenant_filter, any_of(soft_deleted, none_of(soft_deleted)))
    with q.bind(tenant_id=3):
        stmt = apply_clauses(select(Item), q)
    s = str(stmt)
    assert "OR" in s and 3 in params_of(stmt).values()


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
def test_unscoped_all_returns_the_remaining_clause_with_its_binding(args):
    clause = all_of(*args)
    assert clause is owned_by_tenant
    with Ctx.bind(tenant_id=7):
        stmt = apply_clauses(select(Item), clause)
    assert 7 in params_of(stmt).values()


@pytest.mark.parametrize("op", [any_of, none_of])
@pytest.mark.parametrize("reverse", [False, True])
def test_unscoped_or_and_not_do_not_resolve_an_unneeded_binding(op, reverse):
    args = (tenant_filter, UNSCOPED) if reverse else (UNSCOPED, tenant_filter)
    clause = op(*args)
    if op is any_of:
        assert clause is UNSCOPED
    else:
        assert isinstance(clause, WhereClause)
        assert clause().compare(sql_false())


def test_unscoped_none_alone_is_a_false_where_clause():
    clause = none_of(UNSCOPED)
    assert isinstance(clause, WhereClause)
    assert clause().compare(sql_false())


@pytest.mark.parametrize("op", [all_of, any_of, none_of])
def test_unscoped_does_not_hide_invalid_composition_operands(op):
    for invalid in (None, "not a clause", context_param("not_a_predicate")):
        with pytest.raises(TypeError):
            op(UNSCOPED, invalid)


def test_unscoped_nested_composition_and_existing_statement_filter():
    clause = all_of(any_of(UNSCOPED, tenant_filter), soft_deleted)
    assert clause is soft_deleted
    stmt = apply_clauses(select(Item).where(Item.id == 9), UNSCOPED, clause)
    assert "item.id =" in str(stmt)
    assert "deleted_at IS NULL" in str(stmt)
    assert list(params_of(stmt).values()) == [9]
