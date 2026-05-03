from collections.abc import Iterator

import pytest
from fastapi import HTTPException
from sqlalchemy import ForeignKey, create_engine
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship, sessionmaker
from sqlalchemy.pool import StaticPool

import fastapi_restly as fr
from fastapi_restly.db import fr_globals
from fastapi_restly.views._sync import make_new_object, save_object, update_object


@pytest.fixture
def sync_db() -> Iterator[tuple[object, sessionmaker[Session]]]:
    original_database_url = fr_globals.database_url
    original_make_session = fr_globals.make_session
    original_sync_session_generator = fr_globals.sync_session_generator

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    make_session = sessionmaker(bind=engine, expire_on_commit=False)
    fr.configure(make_session=make_session)

    try:
        yield engine, make_session
    finally:
        fr_globals.database_url = original_database_url
        fr_globals.make_session = original_make_session
        fr_globals.sync_session_generator = original_sync_session_generator
        engine.dispose()


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
            id=999,
            title="Draft",
            author_id={"id": original_author.id},
        )
        article = make_new_object(session, Article, create_payload, ArticleSchema)
        session.flush()

        assert article.id != 999
        assert article.author_id == original_author.id
        assert article.author.id == original_author.id

        update_payload = ArticleSchema(
            id=12345,
            title="Published",
            author_id={"id": replacement_author.id},
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

    UpdateItemSchema = fr.schemas.create_model_with_optional_fields(ItemSchema)

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


def test_sync_rest_view_crud_and_pagination(sync_db):
    engine, _make_session = sync_db

    class Customer(fr.PlainIDBase):
        name: Mapped[str]

    class Order(fr.PlainIDBase):
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

    fr.PlainBase.metadata.create_all(engine)

    with fr.session() as session:
        customer = Customer(name="Acme")
        session.add(customer)
        session.commit()
        customer_id = customer.id

    with fr.session() as session:
        view = OrderView()
        view.session = session

        first = view.post(
            OrderInputSchema(
                item_name="Keyboard",
                quantity=1,
                customer_id=customer_id,
            )
        )
        second = view.post(
            OrderInputSchema(
                item_name="Mouse",
                quantity=2,
                customer_id=customer_id,
            )
        )

        assert first.customer.name == "Acme"
        assert second.customer.name == "Acme"

        paginated = view.index({"page": "1", "page_size": "10"})
        assert paginated["total"] == 2
        assert paginated["page"] == 1
        assert paginated["page_size"] == 10
        assert paginated["total_pages"] == 1
        assert paginated["limit"] == 10
        assert paginated["offset"] == 0
        assert {item.item_name for item in paginated["items"]} == {"Keyboard", "Mouse"}

        detail = view.get(first.id)
        assert detail.customer.name == "Acme"
        assert detail.quantity == 1

        updated = view.patch(
            first.id,
            OrderInputSchema(
                item_name="Keyboard Pro",
                quantity=3,
                customer_id=customer_id,
            ),
        )
        assert updated.item_name == "Keyboard Pro"
        assert updated.quantity == 3

        delete_response = view.delete(second.id)
        assert delete_response.status_code == 204

        with pytest.raises(HTTPException):
            view.get(second.id)


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

        def handle_list(self, query_params, query=None):
            call_log.append("list")
            return super().handle_list(query_params, query=query)

        def handle_get(self, id):
            call_log.append("get")
            return super().handle_get(id)

        def handle_create(self, schema_obj):
            call_log.append("create")
            return super().handle_create(schema_obj)

        def handle_update(self, id, schema_obj):
            call_log.append("update")
            return super().handle_update(id, schema_obj)

        def handle_delete(self, id):
            call_log.append("delete")
            return super().handle_delete(id)

    fr.DataclassBase.metadata.create_all(engine)

    with make_session() as session:
        view = WidgetView()
        view.session = session

        created = view.post(WidgetSchema(id=0, name="alpha"))
        view.index({})
        view.get(created.id)
        view.patch(created.id, WidgetSchema(id=created.id, name="beta"))
        view.delete(created.id)

    assert call_log == [
        "create",
        "list",
        "get",
        "update",
        "get",
        "delete",
        "get",
    ]


def test_sync_build_list_query_is_consulted_by_list_and_count(sync_db):
    """Both handle_list and count_index must route through build_list_query so a
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

        def build_list_query(self):
            return super().build_list_query().where(Gadget.active.is_(True))

    fr.DataclassBase.metadata.create_all(engine)

    with make_session() as session:
        session.add_all([
            Gadget(name="alpha", active=True),
            Gadget(name="beta", active=False),
            Gadget(name="gamma", active=True),
            Gadget(name="delta", active=False),
        ])
        session.flush()

        view = GadgetView()
        view.session = session

        # Default build_list_query returns select(self.model).
        assert str(fr.RestView.build_list_query(view)) == str(
            sqlalchemy.select(Gadget)
        )

        # Override is consulted by both list and count.
        results = view.handle_list({})
        assert len(results) == 2
        assert all(g.active for g in results)

        total = view.count_index({})
        assert total == 2
