import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import ForeignKey, ForeignKeyConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

import fastapi_restly as fr
from fastapi_restly.schemas._base import create_model_with_optional_fields
from fastapi_restly.testing import RestlyTestClient
from fastapi_restly.views._base import (
    build_create_plan,
    validate_resolved_reference_consistency,
)
from fastapi_restly.views._sync import make_new_object, save_object, update_object


def test_sync_rest_view_404_response_includes_detail(sync_db):
    engine, _make_session = sync_db

    class Widget(fr.IDBase):
        name: Mapped[str]

    class WidgetSchema(fr.IDSchema):
        name: str

    app = FastAPI()

    @fr.include_view(app)
    class WidgetView(fr.RestView):
        prefix = "/widgets"
        model = Widget
        schema = WidgetSchema

    fr.DataclassBase.metadata.create_all(engine)

    client = RestlyTestClient(app)
    response = client.get("/widgets/123", assert_status_code=404)
    assert response.json() == {"detail": "Widget with id 123 was not found"}


def test_sync_object_helpers_handle_readonly_and_relationship_inputs(sync_db):
    engine, make_session = sync_db

    class Author(fr.IDBase):
        name: Mapped[str]

    class Article(fr.IDBase):
        title: Mapped[str]
        author_id: Mapped[int] = mapped_column(ForeignKey("author.id"))
        author: Mapped[Author] = relationship(default=None)

    class Assignment(fr.IDBase):
        owner_id: Mapped[int]

    class ArticleSchema(fr.IDSchema):
        id: fr.ReadOnly[int]
        title: str
        author_id: fr.IDSchema[Author]

    class AssignmentSchema(fr.BaseSchema):
        owner_id: fr.IDSchema

    fr.DataclassBase.metadata.create_all(engine)

    with make_session() as session:
        original_author = Author(name="Alice")
        replacement_author = Author(name="Bob")
        session.add_all([original_author, replacement_author])
        session.flush()

        create_payload = ArticleSchema(
            id=999, title="Draft", author_id={"id": original_author.id}
        )
        article = make_new_object(session, Article, create_payload, ArticleSchema)
        session.flush()

        assert article.id != 999
        assert article.author_id == original_author.id
        assert article.author.id == original_author.id

        update_payload = ArticleSchema(
            id=12345, title="Published", author_id={"id": replacement_author.id}
        )
        updated_article = update_object(session, article, update_payload, ArticleSchema)

        assert updated_article.id == article.id
        assert updated_article.title == "Published"
        assert updated_article.author_id == replacement_author.id
        assert updated_article.author.id == replacement_author.id

        assignment = make_new_object(
            session,
            Assignment,
            AssignmentSchema(owner_id=fr.IDSchema(id=original_author.id)),
        )
        session.flush()
        assert assignment.owner_id == original_author.id

        updated_assignment = update_object(
            session,
            assignment,
            AssignmentSchema(owner_id=fr.IDSchema(id=replacement_author.id)),
        )
        assert updated_assignment.owner_id == replacement_author.id


def test_sync_save_object_flushes_and_refreshes(sync_db):
    """save_object should flush the session so server-generated defaults like
    autoincrement PKs are populated on the returned object."""
    engine, make_session = sync_db

    class Widget(fr.IDBase):
        name: Mapped[str]

    class WidgetSchema(fr.IDSchema):
        id: fr.ReadOnly[int]
        name: str

    fr.DataclassBase.metadata.create_all(engine)

    with make_session() as session:
        obj = make_new_object(
            session, Widget, WidgetSchema(id=0, name="gizmo"), WidgetSchema
        )
        # Before save_object the PK should not be assigned.
        assert obj.id is None or obj.id == 0
        saved = save_object(session, obj)
        assert saved is obj
        assert isinstance(saved.id, int)
        assert saved.id > 0
        assert saved.name == "gizmo"


def test_sync_update_object_only_applies_set_fields(sync_db):
    """update_object should ignore fields the caller did not explicitly set,
    matching get_writable_inputs semantics — i.e. PATCH partial-update behaviour."""
    engine, make_session = sync_db

    class Item(fr.IDBase):
        name: Mapped[str]
        notes: Mapped[str]

    class ItemSchema(fr.IDSchema):
        id: fr.ReadOnly[int]
        name: str
        notes: str

    UpdateItemSchema = create_model_with_optional_fields(ItemSchema)

    fr.DataclassBase.metadata.create_all(engine)

    with make_session() as session:
        item = make_new_object(
            session, Item, ItemSchema(id=0, name="orig", notes="keep"), ItemSchema
        )
        save_object(session, item)

        # Only ``name`` is set in the partial payload; ``notes`` is unset and
        # should not be overwritten.
        partial = UpdateItemSchema(name="renamed")
        update_object(session, item, partial, ItemSchema)
        assert item.name == "renamed"
        assert item.notes == "keep"


def test_sync_object_helpers_are_dataclass_init_aware_for_resolved_refs(sync_db):
    engine, make_session = sync_db

    class DeclarativeModelBase(DeclarativeBase):
        pass

    class Dd8SyncAuthor(fr.IDBase):
        name: Mapped[str]

    class Dd8SyncFkFirstArticle(fr.IDBase):
        title: Mapped[str]
        author_id: Mapped[int] = mapped_column(ForeignKey("dd8_sync_author.id"))
        author: Mapped[Dd8SyncAuthor] = relationship(default=None, init=False)

    class Dd8SyncRelationshipFirstArticle(fr.IDBase):
        title: Mapped[str]
        author_id: Mapped[int] = mapped_column(
            ForeignKey("dd8_sync_author.id"), init=False
        )
        author: Mapped[Dd8SyncAuthor] = relationship(default=None)

    class Dd8SyncPostAssignArticle(fr.IDBase):
        title: Mapped[str]
        author_id: Mapped[int] = mapped_column(
            ForeignKey("dd8_sync_author.id"), init=False
        )
        author: Mapped[Dd8SyncAuthor] = relationship(default=None, init=False)

    class Dd8SyncBothInitArticle(fr.IDBase):
        title: Mapped[str]
        author_id: Mapped[int] = mapped_column(ForeignKey("dd8_sync_author.id"))
        author: Mapped[Dd8SyncAuthor] = relationship(default=None)

    class Dd8SyncRelationshipFieldFirstArticle(fr.IDBase):
        title: Mapped[str]
        author_id: Mapped[int] = mapped_column(
            ForeignKey("dd8_sync_author.id"), init=False
        )
        author: Mapped[Dd8SyncAuthor] = relationship(default=None)

    class Dd8SyncRelationshipFieldFallbackArticle(fr.IDBase):
        title: Mapped[str]
        author_id: Mapped[int] = mapped_column(ForeignKey("dd8_sync_author.id"))
        author: Mapped[Dd8SyncAuthor] = relationship(default=None, init=False)

    class Dd8SyncDeclarativeAuthor(DeclarativeModelBase):
        __tablename__ = "dd8_sync_declarative_author"

        id: Mapped[int] = mapped_column(primary_key=True)
        name: Mapped[str]

    class Dd8SyncDeclarativeArticle(DeclarativeModelBase):
        __tablename__ = "dd8_sync_declarative_article"

        id: Mapped[int] = mapped_column(primary_key=True)
        title: Mapped[str]
        author_id: Mapped[int] = mapped_column(
            ForeignKey("dd8_sync_declarative_author.id")
        )
        author: Mapped[Dd8SyncDeclarativeAuthor] = relationship()

    class Dd8SyncCompositeParent(fr.DataclassBase):
        __tablename__ = "dd8_sync_composite_parent"

        id1: Mapped[int] = mapped_column(primary_key=True)
        id2: Mapped[int] = mapped_column(primary_key=True)
        name: Mapped[str]

    class Dd8SyncCompositeChild(fr.IDBase):
        __tablename__ = "dd8_sync_composite_child"
        __table_args__ = (
            ForeignKeyConstraint(
                ["parent_id1", "parent_id2"],
                ["dd8_sync_composite_parent.id1", "dd8_sync_composite_parent.id2"],
            ),
        )

        title: Mapped[str]
        parent_id1: Mapped[int]
        parent_id2: Mapped[int]
        parent: Mapped[Dd8SyncCompositeParent] = relationship(default=None)

    class FKSchema(fr.BaseSchema):
        title: str
        author_id: fr.IDRef[Dd8SyncAuthor]

    class RelationshipSchema(fr.BaseSchema):
        title: str
        author: fr.IDSchema[Dd8SyncAuthor]

    class BothReferenceSchema(fr.BaseSchema):
        title: str
        author_id: fr.IDRef[Dd8SyncAuthor]
        author: fr.IDSchema[Dd8SyncAuthor]

    class OptionalBothReferenceSchema(fr.BaseSchema):
        title: str
        author_id: fr.IDRef[Dd8SyncAuthor] | None = None
        author: fr.IDSchema[Dd8SyncAuthor] | None = None

    class DeclarativeFKSchema(fr.BaseSchema):
        title: str
        author_id: fr.IDRef[Dd8SyncDeclarativeAuthor]

    class CompositeRelationshipSchema(fr.BaseSchema):
        title: str
        parent: fr.IDSchema[Dd8SyncCompositeParent]

    fr.DataclassBase.metadata.create_all(engine)
    DeclarativeModelBase.metadata.create_all(engine)

    with make_session() as session:
        first = Dd8SyncAuthor(name="Alice")
        second = Dd8SyncAuthor(name="Bob")
        declarative_author = Dd8SyncDeclarativeAuthor(name="Declarative Alice")
        session.add_all([first, second, declarative_author])
        session.flush()

        for model_cls in (
            Dd8SyncFkFirstArticle,
            Dd8SyncRelationshipFirstArticle,
            Dd8SyncPostAssignArticle,
        ):
            article = make_new_object(
                session,
                model_cls,
                FKSchema(title=model_cls.__name__, author_id=first.id),
                FKSchema,
            )
            assert article.author_id == first.id
            assert article.author is first

            update_object(
                session,
                article,
                FKSchema(title="updated", author_id=second.id),
                FKSchema,
            )
            assert article.author_id == second.id
            assert article.author is second

        both_init_plan = build_create_plan(
            Dd8SyncBothInitArticle,
            FKSchema.model_construct(title="both", author_id=first),
            FKSchema,
        )
        assert both_init_plan.kwargs["author_id"] == first.id
        assert "author" not in both_init_plan.kwargs
        assert both_init_plan.post_assignments["author"] is first

        def validate_optional_payload(
            fields_set: set[str], *, author_id=..., author=...
        ):
            values = {"title": "optional"}
            if author_id is not ...:
                values["author_id"] = author_id
            if author is not ...:
                values["author"] = author
            payload = OptionalBothReferenceSchema.model_construct(
                _fields_set=fields_set, **values
            )
            validate_resolved_reference_consistency(
                Dd8SyncRelationshipFirstArticle, payload, OptionalBothReferenceSchema
            )

        validate_optional_payload({"title", "author"}, author=first)
        validate_optional_payload({"title", "author_id"}, author_id=first)
        validate_optional_payload({"title", "author"}, author=None)
        validate_optional_payload({"title", "author_id"}, author_id=None)
        validate_optional_payload(
            {"title", "author_id", "author"}, author_id=None, author=None
        )
        validate_optional_payload(
            {"title", "author_id", "author"}, author_id=first, author=first
        )
        with pytest.raises(HTTPException) as mismatch_exc:
            validate_optional_payload(
                {"title", "author_id", "author"}, author_id=first, author=second
            )
        assert mismatch_exc.value.status_code == 422
        with pytest.raises(HTTPException) as fk_row_relation_null_exc:
            validate_optional_payload(
                {"title", "author_id", "author"}, author_id=first, author=None
            )
        assert fk_row_relation_null_exc.value.status_code == 422
        with pytest.raises(HTTPException) as fk_null_relation_row_exc:
            validate_optional_payload(
                {"title", "author_id", "author"}, author_id=None, author=first
            )
        assert fk_null_relation_row_exc.value.status_code == 422

        with pytest.raises(ValueError, match="Cannot infer a single local FK"):
            build_create_plan(
                Dd8SyncCompositeChild,
                CompositeRelationshipSchema.model_construct(
                    title="composite",
                    parent=Dd8SyncCompositeParent(id1=1, id2=2, name="Composite"),
                ),
                CompositeRelationshipSchema,
            )

        both_explicit = make_new_object(
            session,
            Dd8SyncRelationshipFirstArticle,
            BothReferenceSchema(
                title="both explicit", author_id=first.id, author={"id": first.id}
            ),
            BothReferenceSchema,
        )
        assert both_explicit.author_id == first.id
        assert both_explicit.author is first

        with pytest.raises(HTTPException) as create_exc:
            make_new_object(
                session,
                Dd8SyncRelationshipFirstArticle,
                BothReferenceSchema(
                    title="conflict", author_id=first.id, author={"id": second.id}
                ),
                BothReferenceSchema,
            )
        assert create_exc.value.status_code == 422

        with pytest.raises(HTTPException) as update_exc:
            update_object(
                session,
                both_explicit,
                BothReferenceSchema(
                    title="conflict", author_id=first.id, author={"id": second.id}
                ),
                BothReferenceSchema,
            )
        assert update_exc.value.status_code == 422

        relation_first = make_new_object(
            session,
            Dd8SyncRelationshipFieldFirstArticle,
            RelationshipSchema(title="relation", author={"id": first.id}),
            RelationshipSchema,
        )
        assert relation_first.author_id == first.id
        assert relation_first.author is first

        relation_fallback = make_new_object(
            session,
            Dd8SyncRelationshipFieldFallbackArticle,
            RelationshipSchema(title="fallback", author={"id": first.id}),
            RelationshipSchema,
        )
        assert relation_fallback.author_id == first.id
        assert relation_fallback.author is first

        declarative_article = make_new_object(
            session,
            Dd8SyncDeclarativeArticle,
            DeclarativeFKSchema(title="declarative", author_id=declarative_author.id),
            DeclarativeFKSchema,
        )
        assert declarative_article.author_id == declarative_author.id
        assert declarative_article.author is declarative_author


def test_sync_rest_view_crud_and_pagination(sync_db):
    engine, _make_session = sync_db

    class Base(DeclarativeBase):
        pass

    class Customer(Base):
        __tablename__ = "customer"

        id: Mapped[int] = mapped_column(primary_key=True)
        name: Mapped[str]

    class Order(Base):
        __tablename__ = "order"

        id: Mapped[int] = mapped_column(primary_key=True)
        item_name: Mapped[str]
        quantity: Mapped[int]
        customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"))
        customer: Mapped[Customer] = relationship()

    class CustomerSchema(fr.IDSchema):
        name: str

    class OrderSchema(fr.IDSchema):
        item_name: str
        quantity: int
        customer_id: int
        customer: CustomerSchema | None = None

    class OrderInputSchema(fr.BaseSchema):
        item_name: str
        quantity: int
        customer_id: int

    class OrderView(fr.RestView):
        prefix = "/sync-orders"
        model = Order
        schema = OrderSchema
        creation_schema = OrderInputSchema
        update_schema = OrderInputSchema
        include_pagination_metadata = True

    Base.metadata.create_all(engine)

    with fr.open_session() as session:
        customer = Customer(name="Acme")
        session.add(customer)
        session.commit()
        customer_id = customer.id

    with fr.open_session() as session:
        view = OrderView()
        view.session = session

        first = view.create(
            OrderInputSchema(item_name="Keyboard", quantity=1, customer_id=customer_id)
        )
        second = view.create(
            OrderInputSchema(item_name="Mouse", quantity=2, customer_id=customer_id)
        )

        assert first.customer.name == "Acme"
        assert second.customer.name == "Acme"

        paginated = view.listing({"page": "1", "page_size": "10"})
        assert paginated["total"] == 2
        assert paginated["page"] == 1
        assert paginated["page_size"] == 10
        assert paginated["total_pages"] == 1
        assert {item.item_name for item in paginated["items"]} == {"Keyboard", "Mouse"}

        detail = view.retrieve(first.id)
        assert detail.customer.name == "Acme"
        assert detail.quantity == 1

        updated = view.update(
            first.id,
            OrderInputSchema(
                item_name="Keyboard Pro", quantity=3, customer_id=customer_id
            ),
        )
        assert updated.item_name == "Keyboard Pro"
        assert updated.quantity == 3

        delete_response = view.destroy(second.id)
        assert delete_response.status_code == 204

        with pytest.raises(HTTPException):
            view.retrieve(second.id)


def test_sync_rest_view_dispatches_to_handle_overrides(sync_db):
    engine, make_session = sync_db

    class DispatchWidget(fr.IDBase):
        name: Mapped[str]

    class WidgetSchema(fr.IDSchema):
        name: str

    call_log: list[str] = []

    class WidgetView(fr.RestView):
        prefix = "/widgets"
        model = DispatchWidget
        schema = WidgetSchema

        def handle_listing(self, query_params, query=None):
            call_log.append("list")
            return super().handle_listing(query_params, query=query)

        def handle_retrieve(self, id):
            call_log.append("get")
            return super().handle_retrieve(id)

        def handle_create(self, schema_obj):
            call_log.append("create")
            return super().handle_create(schema_obj)

        def handle_update(self, id, schema_obj):
            call_log.append("update")
            return super().handle_update(id, schema_obj)

        def handle_destroy(self, id):
            call_log.append("delete")
            return super().handle_destroy(id)

    fr.DataclassBase.metadata.create_all(engine)

    with make_session() as session:
        view = WidgetView()
        view.session = session

        created = view.create(WidgetSchema(id=0, name="alpha"))
        view.listing({})
        view.retrieve(created.id)
        view.update(created.id, WidgetSchema(id=created.id, name="beta"))
        view.destroy(created.id)

    assert call_log == ["create", "list", "get", "update", "get", "delete", "get"]


def test_sync_build_query_is_consulted_by_list_and_count(sync_db):
    """Both handle_listing and count_listing must route through build_query so a
    single override filters listing AND its pagination total."""
    import sqlalchemy

    engine, make_session = sync_db

    class Gadget(fr.IDBase):
        name: Mapped[str]
        active: Mapped[bool]

    class GadgetSchema(fr.IDSchema):
        name: str
        active: bool

    class GadgetView(fr.RestView):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema

        def build_query(self):
            return super().build_query().where(Gadget.active.is_(True))

    fr.DataclassBase.metadata.create_all(engine)

    with make_session() as session:
        session.add_all(
            [
                Gadget(name="alpha", active=True),
                Gadget(name="beta", active=False),
                Gadget(name="gamma", active=True),
                Gadget(name="delta", active=False),
            ]
        )
        session.flush()

        view = GadgetView()
        view.session = session

        # Default build_query returns select(self.model).
        assert str(fr.RestView.build_query(view)) == str(sqlalchemy.select(Gadget))

        # Override is consulted by both list and count.
        results = view.handle_listing({})
        assert len(results) == 2
        assert all(g.active for g in results)

        total = view.count_listing({})
        assert total == 2
