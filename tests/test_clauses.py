from datetime import datetime

import pytest
from sqlalchemy import ColumnElement, ForeignKey, Select, delete, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from fastapi_restly.clauses import (
    Clause,
    ClauseNamespace,
    CombinedClause,
    ContextParam,
    TransformClause,
    WhereClause,
    all_of,
    any_of,
    apply_clauses,
    combine,
    context_param,
    none_of,
    transform_clause,
    where_clause,
)


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


soft_deleted = where_clause(Item.deleted_at.is_(None))


@where_clause
def in_period(start: int, end: int) -> ColumnElement[bool]:
    return Item.created_at.between(start, end)


@transform_clause
def join_collection(stmt: Select) -> Select:
    return stmt.join(Collection, Collection.id == Item.collection_id)


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
    with pytest.raises(TypeError):
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


def test_combine_routes_context():
    bundle = combine(join_collection, tenant_filter)
    with bundle.bind(tenant_id=9):
        stmt = apply_clauses(select(Item), bundle)
    assert 9 in params_of(stmt).values()


# --- aliases --------------------------------------------------------------


def test_leaf_alias_independent():
    period_b = in_period.alias("period_b")
    with in_period.bind(start=1, end=2), period_b.bind(start=3, end=4):
        stmt = apply_clauses(select(Item), any_of(in_period, period_b))
    assert sorted(params_of(stmt).values()) == [1, 2, 3, 4]


def test_alias_on_composite_raises():
    with pytest.raises(TypeError, match="leaf"):
        all_of(tenant_filter, soft_deleted).alias("x")


def test_alias_ambiguity_raises():
    q = any_of(in_period, in_period.alias("period_c"))
    with pytest.raises(TypeError, match="alias"):
        with q.bind(start=1, end=2):
            pass


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


def test_named_alias_shares_binding():
    a = in_period.alias("shared")
    b = in_period.alias("shared")
    with a.bind(start=1, end=2):
        stmt = apply_clauses(select(Item), b)
    assert sorted(params_of(stmt).values()) == [1, 2]


def test_same_named_alias_twice_in_tree_not_ambiguous():
    q = any_of(in_period.alias("dup"), in_period.alias("dup"))
    with q.bind(start=1, end=2):
        stmt = apply_clauses(select(Item), q)
    assert sorted(params_of(stmt).values()) == [1, 1, 2, 2]


# --- transforms and combine -----------------------------------------------


def test_combine_transform_and_where():
    bundle = combine(join_collection, collection_active)
    stmt = apply_clauses(select(Item), bundle)
    assert "JOIN collection" in str(stmt)
    assert "archived_at IS NULL" in str(stmt)


def test_transform_context_param():
    @transform_clause
    def limited(stmt: Select, n: int) -> Select:
        return stmt.limit(n)

    with limited.bind(n=10):
        stmt = apply_clauses(select(Item), limited)
    assert "LIMIT" in str(stmt)


def test_transform_only_operand_rejected():
    with pytest.raises(TypeError, match="where"):
        all_of(join_collection, soft_deleted)
    with pytest.raises(TypeError, match="where"):
        any_of(join_collection, soft_deleted)
    with pytest.raises(TypeError, match="where"):
        none_of(join_collection)


# --- transforms inside composites -----------------------------------------


def test_and_propagates_transform():
    bundle = combine(join_collection, collection_active)
    q = all_of(bundle, tenant_filter)
    with q.bind(tenant_id=5):
        stmt = apply_clauses(select(Item), q)
    s = str(stmt)
    assert "JOIN collection" in s
    assert "archived_at IS NULL" in s
    assert 5 in params_of(stmt).values()


def test_shared_join_applied_once():
    bundle_a = combine(join_collection, collection_active)
    bundle_b = combine(join_collection, tenant_filter)
    with tenant_filter.bind(tenant_id=5):
        stmt = apply_clauses(select(Item), bundle_a, bundle_b)
    assert str(stmt).count("JOIN collection") == 1


def test_or_rejects_transform_operand():
    bundle = combine(join_collection, collection_active)
    with pytest.raises(TypeError, match="EXISTS"):
        any_of(bundle, soft_deleted)


def test_not_rejects_transform_operand():
    bundle = combine(join_collection, collection_active)
    with pytest.raises(TypeError, match="EXISTS"):
        none_of(bundle)


def test_stmt_param_not_routable():
    @transform_clause
    def limited(stmt: Select, n: int) -> Select:
        return stmt.limit(n)

    with pytest.raises(TypeError, match="no clause accepts: stmt"):
        with limited.bind(stmt=1):
            pass


# --- clause kinds ---------------------------------------------------------


def test_constructor_types():
    assert isinstance(tenant_filter, WhereClause)
    assert isinstance(join_collection, TransformClause)
    assert isinstance(combine(join_collection, collection_active), CombinedClause)


def test_all_of_type_follows_operands():
    assert isinstance(all_of(tenant_filter, soft_deleted), WhereClause)
    bundle = combine(join_collection, collection_active)
    assert isinstance(all_of(bundle, tenant_filter), CombinedClause)


def test_alias_preserves_type():
    assert isinstance(in_period.alias("typed_alias"), WhereClause)
    assert isinstance(join_collection.alias("typed_join"), TransformClause)


def test_clause_is_abstract():
    with pytest.raises(TypeError, match="abstract"):
        Clause()


def test_combine_requires_transform():
    with pytest.raises(TypeError, match="all_of"):
        combine(tenant_filter, soft_deleted)


# --- calling a WhereClause -------------------------------------------------


def test_call_returns_expression():
    stmt = select(Item).where(tenant_filter(tenant_id=7))
    assert 7 in params_of(stmt).values()


def test_call_composite():
    q = all_of(tenant_filter, soft_deleted)
    stmt = select(Item).where(q(tenant_id=7))
    assert 7 in params_of(stmt).values()
    assert "deleted_at IS NULL" in str(stmt)


def test_call_uses_ambient_bind():
    with tenant_filter.bind(tenant_id=7):
        expr = tenant_filter()
    assert "tenant_id" in str(expr)


def test_combined_clause_not_callable():
    bundle = combine(join_collection, collection_active)
    with pytest.raises(TypeError):
        bundle()


# --- cross-model validation -----------------------------------------------


def test_cross_model_clause_rejected():
    with pytest.raises(TypeError, match="collection"):
        apply_clauses(select(Item), collection_active)


def test_cross_model_clause_passes_with_join():
    bundle = combine(join_collection, collection_active)
    stmt = apply_clauses(select(Item), bundle)
    assert "JOIN collection" in str(stmt)


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


def test_update_delete_reject_transform():
    bundle = combine(join_collection, collection_active)
    with pytest.raises(TypeError, match="join"):
        apply_clauses(update(Item), bundle)
    with pytest.raises(TypeError, match="join"):
        apply_clauses(delete(Item), bundle)


# --- Clause methods and ephemeral bind ------------------------------------


def test_select_method_with_ephemeral_bind():
    q = all_of(tenant_filter, soft_deleted)
    stmt = q.select(Item, tenant_id=7)
    assert 7 in params_of(stmt).values()
    assert "deleted_at IS NULL" in str(stmt)


def test_select_method_uses_ambient_bind():
    q = all_of(tenant_filter, soft_deleted)
    with q.bind(tenant_id=7):
        stmt = q.select(Item)
    assert 7 in params_of(stmt).values()


def test_select_method_applies_transforms():
    bundle = combine(join_collection, collection_active)
    stmt = bundle.select(Item)
    assert "JOIN collection" in str(stmt)


def test_update_method():
    stmt = tenant_filter.update(Item, tenant_id=5).values(tenant_id=6)
    assert str(stmt).startswith("UPDATE item")
    assert 5 in params_of(stmt).values()


def test_delete_method():
    stmt = tenant_filter.delete(Item, tenant_id=5)
    assert str(stmt).startswith("DELETE FROM item")
    assert 5 in params_of(stmt).values()


def test_combined_clause_has_no_update_or_delete():
    bundle = combine(join_collection, collection_active)
    assert not hasattr(bundle, "update")
    assert not hasattr(bundle, "delete")


def test_ephemeral_bind_stays_strict():
    with pytest.raises(TypeError, match="nope"):
        tenant_filter.select(Item, tenant_id=7, nope=1)


def test_ephemeral_bind_does_not_leak():
    tenant_filter.select(Item, tenant_id=7)
    with pytest.raises(TypeError):
        tenant_filter.select(Item)


# --- clause namespaces -----------------------------------------------------


class Gadget(Base):
    __tablename__ = "gadget"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int]
    deleted_at: Mapped[datetime | None]


class GadgetClauses(ClauseNamespace):
    model = Gadget

    is_deleted = where_clause(Gadget.deleted_at.is_not(None))

    @where_clause
    def owned_by_tenant(tenant_id: int) -> ColumnElement[bool]:
        return Gadget.tenant_id == tenant_id

    visible = all_of(owned_by_tenant, none_of(is_deleted))


def test_namespace_attaches_as_C():
    assert Gadget.C is GadgetClauses
    assert isinstance(Gadget.C.visible, WhereClause)


def test_namespace_clauses_work():
    stmt = Gadget.C.visible.select(Gadget, tenant_id=7)
    assert 7 in params_of(stmt).values()
    assert "deleted_at IS NULL" in str(stmt)


def test_namespace_requires_model():
    with pytest.raises(TypeError, match="model"):

        class NoModelClauses(ClauseNamespace):
            something = where_clause(Item.deleted_at.is_(None))


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


def test_context_param_kind_and_call():
    slot = context_param("tenant_id", int)
    assert isinstance(slot, ContextParam)
    assert slot(tenant_id=7) == 7


def test_param_unbound_raises_with_name():
    slot = context_param("tenant_id")
    with pytest.raises(TypeError, match="tenant_id"):
        slot()


def test_embedded_slot_in_bare_condition():
    slot = context_param("embed_tenant")
    owned = where_clause(Item.tenant_id == slot)
    with slot.bind(embed_tenant=7):
        stmt = apply_clauses(select(Item), owned)
    assert 7 in params_of(stmt).values()


def test_embedded_slot_visible_in_repr():
    slot = context_param("repr_tenant")
    owned = where_clause(Item.tenant_id == slot)
    assert repr(owned) == "<WhereClause binds: repr_tenant>"


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


def test_embedded_slot_ephemeral_via_select_method():
    slot = context_param("method_tenant")
    q = where_clause(Item.tenant_id == slot)
    stmt = q.select(Item, method_tenant=9)
    assert 9 in params_of(stmt).values()


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


def test_annotated_marker_feeds_from_slot():
    from typing import Annotated

    slot = context_param("marker_tenant")

    @where_clause
    def owned(tid: Annotated[int, slot]) -> ColumnElement[bool]:
        return Item.tenant_id == tid

    with owned.bind(marker_tenant=4):  # the slot's name, not the local one
        stmt = apply_clauses(select(Item), owned)
    assert 4 in params_of(stmt).values()


def test_annotated_marker_local_name_not_routable():
    from typing import Annotated

    slot = context_param("marker_tenant_b")

    @where_clause
    def owned(tid: Annotated[int, slot]) -> ColumnElement[bool]:
        return Item.tenant_id == tid

    with pytest.raises(TypeError, match="no clause accepts: tid"):
        with owned.bind(tid=4):
            pass


def test_annotated_marker_mixed_with_own_param():
    from typing import Annotated

    from sqlalchemy import and_

    slot = context_param("marker_tenant_c")

    @where_clause
    def q(term: str, tid: Annotated[int, slot]) -> ColumnElement[bool]:
        return and_(Item.tenant_id == tid, Item.collection_id == term)

    with q.bind(term="x", marker_tenant_c=6):
        stmt = apply_clauses(select(Item), q)
    assert {"x", 6} <= set(params_of(stmt).values())


def test_aliased_slot_embeds_independently():
    slot = context_param("alias_embed")
    other = slot.alias("alias_embed_b")
    a = where_clause(Item.tenant_id == slot)
    b = where_clause(Item.collection_id == other)
    with slot.bind(alias_embed=1), other.bind(alias_embed=2):
        assert params_of(apply_clauses(select(Item), a)) == {"alias_embed": 1}
        assert params_of(apply_clauses(select(Item), b)) == {"alias_embed": 2}


def test_aliased_and_original_slot_in_one_expression_raises():
    slot = context_param("alias_embed_c")
    other = slot.alias("alias_embed_d")
    cond = where_clause((Item.tenant_id == slot) | (Item.collection_id == other))
    with slot.bind(alias_embed_c=1), other.bind(alias_embed_c=2):
        with pytest.raises(TypeError, match="alias_embed_c"):
            apply_clauses(select(Item), cond)


def test_param_rejected_in_boolean_composites():
    slot = context_param("nope_tenant")
    with pytest.raises(TypeError, match="where"):
        all_of(slot, soft_deleted)


def test_param_alias_is_independent():
    slot = context_param("aliased_tenant")
    other = slot.alias("param_alias_b")
    with slot.bind(aliased_tenant=1), other.bind(aliased_tenant=2):
        assert slot() == 1
        assert other() == 2


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
    with pytest.raises(TypeError, match="contributes no"):
        slot.select(Item)


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

    with pytest.raises(TypeError, match="variadic"):

        @transform_clause
        def g(stmt: Select, *rest) -> Select:
            return stmt


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


def test_marker_slot_from_local_scope_resolves():
    from typing import Annotated

    local_slot = context_param("xlocal")

    @where_clause
    def f(tid: Annotated[int, local_slot]) -> ColumnElement[bool]:
        return Item.tenant_id == tid

    with local_slot.bind(xlocal=5):
        stmt = apply_clauses(select(Item), f)
    assert 5 in params_of(stmt).values()


def test_memoized_alias_pair_in_one_expression_ok():
    slot = context_param("xmemo")
    a = slot.alias("xmemo_alias")
    b = slot.alias("xmemo_alias")  # same name, same namespace
    cond = where_clause((Item.tenant_id == a) | (Item.collection_id == b))
    with a.bind(xmemo=4):
        stmt = apply_clauses(select(Item), cond)
    assert params_of(stmt) == {"xmemo": 4}


def test_alias_on_leaf_with_embedded_slot_allowed():
    slot = context_param("xleafalias")
    owned = where_clause(Item.tenant_id == slot)
    other = owned.alias("xleafalias_b")
    with slot.bind(xleafalias=9):  # slot stays shared between original and alias
        s1 = apply_clauses(select(Item), owned)
        s2 = apply_clauses(select(Item), other)
    assert params_of(s1) == params_of(s2) == {"xleafalias": 9}


def test_kwargs_transform_cannot_claim_everything():
    # variadics are rejected outright, so the claims-everything routing
    # hole cannot be constructed any more
    with pytest.raises(TypeError, match="variadic"):

        @transform_clause
        def paged_all(stmt: Select, **kw) -> Select:
            return stmt


def test_dag_composition_binds_fast():
    q = all_of(tenant_filter, soft_deleted)
    for _ in range(22):  # zonder visited-set: 2**22 walks
        q = all_of(q, q)
    with q.bind(tenant_id=1):
        pass


def test_double_attach_with_non_class_C():
    class Odd(Base):
        __tablename__ = "odd_c_attr"
        id: Mapped[int] = mapped_column(primary_key=True)

    Odd.C = 5
    with pytest.raises(TypeError, match="already has"):

        class OddClauses(ClauseNamespace):
            model = Odd
            anything = where_clause(Odd.id == 1)


def test_namespace_attaches_on_restly_idbase():
    from sqlalchemy.orm import Mapped as M
    from sqlalchemy.orm import mapped_column as mc

    import fastapi_restly as fr

    class Gizmo(fr.IDBase):
        __tablename__ = "gizmo_ns_test"
        tenant_id: M[int] = mc()

    class GizmoClauses(ClauseNamespace):
        model = Gizmo
        owned = where_clause(Gizmo.tenant_id == 1)

    assert Gizmo.C is GizmoClauses


# --- self-revealing errors and reprs ---------------------------------------


def test_unbound_error_teaches_bind():
    with pytest.raises(TypeError, match=r"\.bind\(tenant_id="):
        apply_clauses(select(Item), tenant_filter)


def test_unbound_param_error_teaches_bind():
    slot = context_param("teach_tenant")
    with pytest.raises(TypeError, match=r"\.bind\(teach_tenant="):
        slot()


def test_unbound_transform_error_teaches_bind():
    @transform_clause
    def limited(stmt: Select, n: int) -> Select:
        return stmt.limit(n)

    with pytest.raises(TypeError, match=r"\.bind\(n="):
        apply_clauses(select(Item), limited)


def test_repr_shows_kind_name_and_binds():
    assert repr(tenant_filter) == "<WhereClause tenant_filter binds: tenant_id>"
    assert repr(soft_deleted) == "<WhereClause>"
    assert repr(join_collection) == "<TransformClause join_collection>"


def test_repr_of_composite_aggregates_binds():
    q = all_of(tenant_filter, in_period)
    assert repr(q) == "<WhereClause all_of binds: end, start, tenant_id>"


def test_repr_of_param():
    assert repr(context_param("tenant_id")) == "<ContextParam tenant_id>"


# --- provenance and explain -------------------------------------------------


def test_explain_shows_unbound():
    q = all_of(tenant_filter, soft_deleted)
    assert q.explain() == "<WhereClause all_of binds: tenant_id>\n  tenant_id: UNBOUND"


def test_explain_shows_value_and_origin():
    q = all_of(tenant_filter, soft_deleted)
    with q.bind(tenant_id=7):
        text = q.explain()
    assert "tenant_id = 7" in text
    assert "bound at" in text
    assert "test_clauses.py" in text


def test_context_param_repr_names_bind_site():
    slot = context_param("prov_repr")
    assert repr(slot) == "<ContextParam prov_repr>"
    with slot.bind(prov_repr=1):
        text = repr(slot)
    assert text.startswith("<ContextParam prov_repr, bound at ")
    assert "test_clauses.py" in text
    assert repr(slot) == "<ContextParam prov_repr>"  # reset after exit


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
    with pytest.raises(TypeError, match="transform"):
        combine()


def test_transforms_only_bundle():
    bundle = combine(join_collection, transform_clause(lambda stmt: stmt.limit(5)))
    stmt = apply_clauses(select(Item), bundle)
    assert "JOIN collection" in str(stmt)
    assert "LIMIT" in str(stmt)


def test_nested_composition():
    q = all_of(tenant_filter, any_of(soft_deleted, none_of(soft_deleted)))
    with q.bind(tenant_id=3):
        stmt = apply_clauses(select(Item), q)
    s = str(stmt)
    assert "OR" in s and 3 in params_of(stmt).values()
