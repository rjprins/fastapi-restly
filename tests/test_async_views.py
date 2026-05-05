import asyncio

import pytest
import sqlalchemy
from fastapi import HTTPException
from sqlalchemy import ForeignKey
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

import fastapi_restly as fr
from fastapi_restly.schemas._base import create_model_with_optional_fields
from fastapi_restly.views._async import (
    async_make_new_object,
    async_save_object,
    async_update_object,
)


def _make_engine_and_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    make_session = async_sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False
    )
    return engine, make_session


def test_async_object_helpers_handle_readonly_and_relationship_inputs():
    """Directly call make_new_object and update_object to cover the IDSchema and
    DeclarativeBase FK resolution branches, mirroring test_sync_views.py."""

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

    class ArticleView(fr.AsyncRestView):
        prefix = "/articles"
        model = Article
        schema = ArticleSchema

    class AssignmentView(fr.AsyncRestView):
        prefix = "/assignments"
        model = Assignment
        schema = AssignmentSchema

    async def run():
        engine, make_session = _make_engine_and_session()
        async with engine.begin() as conn:
            await conn.run_sync(fr.DataclassBase.metadata.create_all)

        async with make_session() as session:
            original_author = Author(name="Alice")
            replacement_author = Author(name="Bob")
            session.add_all([original_author, replacement_author])
            await session.flush()

            article_view = ArticleView()
            article_view.session = session

            create_payload = ArticleSchema(
                id=999, title="Draft", author_id={"id": original_author.id}
            )
            article = await article_view.make_new_object(create_payload)
            await session.flush()

            assert article.id != 999
            assert article.author_id == original_author.id
            assert article.author.id == original_author.id

            update_payload = ArticleSchema(
                id=12345, title="Published", author_id={"id": replacement_author.id}
            )
            updated_article = await article_view.update_object(article, update_payload)

            assert updated_article.id == article.id
            assert updated_article.title == "Published"
            assert updated_article.author_id == replacement_author.id
            assert updated_article.author.id == replacement_author.id

            # Bare IDSchema (no model annotation) stays as IDSchema — covers lines 161-162
            assign_view = AssignmentView()
            assign_view.session = session

            assignment = await assign_view.make_new_object(
                AssignmentSchema(owner_id=fr.IDSchema(id=original_author.id))
            )
            await session.flush()
            assert assignment.owner_id == original_author.id

            updated_assignment = await assign_view.update_object(
                assignment,
                AssignmentSchema(owner_id=fr.IDSchema(id=replacement_author.id)),
            )
            assert updated_assignment.owner_id == replacement_author.id

        await engine.dispose()

    asyncio.run(run())


def test_async_free_functions_handle_readonly_and_relationship_inputs():
    """Mirror of test_sync_object_helpers_handle_readonly_and_relationship_inputs
    but exercising the module-level async_make_new_object / async_update_object
    / async_save_object free functions instead of the view methods."""

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

    async def run():
        engine, make_session = _make_engine_and_session()
        async with engine.begin() as conn:
            await conn.run_sync(fr.DataclassBase.metadata.create_all)

        async with make_session() as session:
            original_author = Author(name="Alice")
            replacement_author = Author(name="Bob")
            session.add_all([original_author, replacement_author])
            await session.flush()

            create_payload = ArticleSchema(
                id=999, title="Draft", author_id={"id": original_author.id}
            )
            article = await async_make_new_object(
                session, Article, create_payload, ArticleSchema
            )
            saved = await async_save_object(session, article)
            assert saved is article
            assert article.id != 999
            assert isinstance(article.id, int)
            assert article.author_id == original_author.id

            update_payload = ArticleSchema(
                id=12345, title="Published", author_id={"id": replacement_author.id}
            )
            await async_update_object(session, article, update_payload, ArticleSchema)
            assert article.title == "Published"
            assert article.author_id == replacement_author.id
            assert article.author.id == replacement_author.id

            # Bare IDSchema (no model annotation) should still resolve via .id.
            assignment = await async_make_new_object(
                session,
                Assignment,
                AssignmentSchema(owner_id=fr.IDSchema(id=original_author.id)),
            )
            await session.flush()
            assert assignment.owner_id == original_author.id

            await async_update_object(
                session,
                assignment,
                AssignmentSchema(owner_id=fr.IDSchema(id=replacement_author.id)),
            )
            assert assignment.owner_id == replacement_author.id

        await engine.dispose()

    asyncio.run(run())


def test_async_update_object_only_applies_set_fields():
    """async_update_object should ignore fields the caller did not explicitly
    set, matching get_writable_inputs / PATCH partial-update semantics."""

    class Item(fr.IDBase):
        name: Mapped[str]
        notes: Mapped[str]

    class ItemSchema(fr.IDSchema):
        id: fr.ReadOnly[int]
        name: str
        notes: str

    UpdateItemSchema = create_model_with_optional_fields(ItemSchema)

    async def run():
        engine, make_session = _make_engine_and_session()
        async with engine.begin() as conn:
            await conn.run_sync(fr.DataclassBase.metadata.create_all)

        async with make_session() as session:
            item = await async_make_new_object(
                session, Item, ItemSchema(id=0, name="orig", notes="keep"), ItemSchema
            )
            await async_save_object(session, item)

            partial = UpdateItemSchema(name="renamed")
            await async_update_object(session, item, partial, ItemSchema)
            assert item.name == "renamed"
            assert item.notes == "keep"

        await engine.dispose()

    asyncio.run(run())


def test_async_object_helpers_are_dataclass_init_aware_for_resolved_refs():
    class Dd8AsyncAuthor(fr.IDBase):
        name: Mapped[str]

    class Dd8AsyncRelationshipFirstArticle(fr.IDBase):
        title: Mapped[str]
        author_id: Mapped[int] = mapped_column(
            ForeignKey("dd8_async_author.id"), init=False
        )
        author: Mapped[Dd8AsyncAuthor] = relationship(default=None)

    class Dd8AsyncRelationshipFieldFallbackArticle(fr.IDBase):
        title: Mapped[str]
        author_id: Mapped[int] = mapped_column(ForeignKey("dd8_async_author.id"))
        author: Mapped[Dd8AsyncAuthor] = relationship(default=None, init=False)

    class FKSchema(fr.BaseSchema):
        title: str
        author_id: fr.IDRef[Dd8AsyncAuthor]

    class RelationshipSchema(fr.BaseSchema):
        title: str
        author: fr.IDSchema[Dd8AsyncAuthor]

    class BothReferenceSchema(fr.BaseSchema):
        title: str
        author_id: fr.IDRef[Dd8AsyncAuthor]
        author: fr.IDSchema[Dd8AsyncAuthor]

    async def run():
        engine, make_session = _make_engine_and_session()
        async with engine.begin() as conn:
            await conn.run_sync(fr.DataclassBase.metadata.create_all)

        async with make_session() as session:
            first = Dd8AsyncAuthor(name="Alice")
            second = Dd8AsyncAuthor(name="Bob")
            session.add_all([first, second])
            await session.flush()

            article = await async_make_new_object(
                session,
                Dd8AsyncRelationshipFirstArticle,
                FKSchema(title="async", author_id=first.id),
                FKSchema,
            )
            assert article.author_id == first.id
            assert article.author is first

            await async_update_object(
                session,
                article,
                FKSchema(title="updated", author_id=second.id),
                FKSchema,
            )
            assert article.author_id == second.id
            assert article.author is second

            with pytest.raises(HTTPException) as create_exc:
                await async_make_new_object(
                    session,
                    Dd8AsyncRelationshipFirstArticle,
                    BothReferenceSchema(
                        title="conflict", author_id=first.id, author={"id": second.id}
                    ),
                    BothReferenceSchema,
                )
            assert create_exc.value.status_code == 422

            with pytest.raises(HTTPException) as update_exc:
                await async_update_object(
                    session,
                    article,
                    BothReferenceSchema(
                        title="conflict", author_id=first.id, author={"id": second.id}
                    ),
                    BothReferenceSchema,
                )
            assert update_exc.value.status_code == 422

            fallback = await async_make_new_object(
                session,
                Dd8AsyncRelationshipFieldFallbackArticle,
                RelationshipSchema(title="fallback", author={"id": first.id}),
                RelationshipSchema,
            )
            assert fallback.author_id == first.id
            assert fallback.author is first

        await engine.dispose()

    asyncio.run(run())


def test_async_free_functions_importable_from_views_package():
    """Sanity check that the async free functions are exposed from
    ``fastapi_restly.views`` (mirroring the sync helpers)."""
    import fastapi_restly.views as fv

    assert fv.async_make_new_object is async_make_new_object
    assert fv.async_save_object is async_save_object
    assert fv.async_update_object is async_update_object


def test_async_rest_view_crud_and_pagination():
    """Call AsyncRestView methods directly (no HTTP) to cover get/create/update/delete
    and include_pagination_metadata=True, mirroring test_sync_views.py."""

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

    class OrderView(fr.AsyncRestView):
        prefix = "/async-orders"
        model = Order
        schema = OrderSchema
        creation_schema = OrderInputSchema
        update_schema = OrderInputSchema
        include_pagination_metadata = True

    async def run():
        engine, make_session = _make_engine_and_session()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with make_session() as session:
            customer = Customer(name="Acme")
            session.add(customer)
            await session.flush()
            customer_id = customer.id

            view = OrderView()
            view.session = session

            first = await view.create(
                OrderInputSchema(
                    item_name="Keyboard", quantity=1, customer_id=customer_id
                )
            )
            second = await view.create(
                OrderInputSchema(item_name="Mouse", quantity=2, customer_id=customer_id)
            )

            assert first.item_name == "Keyboard"
            assert second.item_name == "Mouse"

            paginated = await view.listing({"page": "1", "page_size": "10"})
            assert paginated["total"] == 2
            assert paginated["page"] == 1
            assert paginated["page_size"] == 10
            assert paginated["total_pages"] == 1
            assert {item.item_name for item in paginated["items"]} == {
                "Keyboard",
                "Mouse",
            }

            detail = await view.retrieve(first.id)
            assert detail.quantity == 1

            updated = await view.update(
                first.id,
                OrderInputSchema(
                    item_name="Keyboard Pro", quantity=3, customer_id=customer_id
                ),
            )
            assert updated.item_name == "Keyboard Pro"
            assert updated.quantity == 3

            delete_response = await view.destroy(second.id)
            assert delete_response.status_code == 204

            with pytest.raises(HTTPException):
                await view.retrieve(second.id)

        await engine.dispose()

    asyncio.run(run())


def test_async_rest_view_dispatches_to_handle_overrides():
    class DispatchWidget(fr.IDBase):
        name: Mapped[str]

    class WidgetSchema(fr.IDSchema):
        name: str

    call_log: list[str] = []

    class WidgetView(fr.AsyncRestView):
        prefix = "/widgets"
        model = DispatchWidget
        schema = WidgetSchema

        async def handle_listing(self, query_params, query=None):
            call_log.append("list")
            return await super().handle_listing(query_params, query=query)

        async def handle_retrieve(self, id):
            call_log.append("get")
            return await super().handle_retrieve(id)

        async def handle_create(self, schema_obj):
            call_log.append("create")
            return await super().handle_create(schema_obj)

        async def handle_update(self, id, schema_obj):
            call_log.append("update")
            return await super().handle_update(id, schema_obj)

        async def handle_destroy(self, id):
            call_log.append("delete")
            return await super().handle_destroy(id)

    async def run():
        engine, make_session = _make_engine_and_session()
        async with engine.begin() as conn:
            await conn.run_sync(fr.DataclassBase.metadata.create_all)

        async with make_session() as session:
            view = WidgetView()
            view.session = session

            created = await view.create(WidgetSchema(id=0, name="alpha"))
            await view.listing({})
            await view.retrieve(created.id)
            await view.update(created.id, WidgetSchema(id=created.id, name="beta"))
            await view.destroy(created.id)

        await engine.dispose()

    asyncio.run(run())

    assert call_log == ["create", "list", "get", "update", "get", "delete", "get"]


def test_async_handle_listing_with_custom_query():
    """Call handle_listing with an explicit query to cover the query-is-not-None branch."""

    class Widget(fr.IDBase):
        name: Mapped[str]
        active: Mapped[bool]

    class WidgetSchema(fr.IDSchema):
        name: str
        active: bool

    class WidgetView(fr.AsyncRestView):
        prefix = "/widgets"
        model = Widget
        schema = WidgetSchema

    async def run():
        engine, make_session = _make_engine_and_session()
        async with engine.begin() as conn:
            await conn.run_sync(fr.DataclassBase.metadata.create_all)

        async with make_session() as session:
            session.add_all(
                [
                    Widget(name="alpha", active=True),
                    Widget(name="beta", active=False),
                    Widget(name="gamma", active=True),
                ]
            )
            await session.flush()

            view = WidgetView()
            view.session = session

            # Pass an explicit custom query (covers the query-is-not-None branch)
            custom_query = sqlalchemy.select(Widget).where(Widget.active == True)  # noqa: E712
            results = await view.handle_listing({}, query=custom_query)

            assert len(results) == 2
            assert all(w.active for w in results)

        await engine.dispose()

    asyncio.run(run())


def test_async_build_listing_query_is_consulted_by_list_and_count():
    """Both handle_listing and count_listing must route through build_listing_query so a
    single override filters listing AND its pagination total."""

    class Gizmo(fr.IDBase):
        name: Mapped[str]
        active: Mapped[bool]

    class GizmoSchema(fr.IDSchema):
        name: str
        active: bool

    class GizmoView(fr.AsyncRestView):
        prefix = "/gizmos"
        model = Gizmo
        schema = GizmoSchema

        def build_listing_query(self):
            return super().build_listing_query().where(Gizmo.active.is_(True))

    async def run():
        engine, make_session = _make_engine_and_session()
        async with engine.begin() as conn:
            await conn.run_sync(fr.DataclassBase.metadata.create_all)

        async with make_session() as session:
            session.add_all(
                [
                    Gizmo(name="alpha", active=True),
                    Gizmo(name="beta", active=False),
                    Gizmo(name="gamma", active=True),
                    Gizmo(name="delta", active=False),
                ]
            )
            await session.flush()

            view = GizmoView()
            view.session = session

            # Default build_listing_query returns select(self.model).
            assert str(fr.AsyncRestView.build_listing_query(view)) == str(
                sqlalchemy.select(Gizmo)
            )

            # Override is consulted by both list and count.
            results = await view.handle_listing({})
            assert len(results) == 2
            assert all(g.active for g in results)

            total = await view.count_listing({})
            assert total == 2

        await engine.dispose()

    asyncio.run(run())
