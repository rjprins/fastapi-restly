"""
Tests for class-based view inheritance in FastAPI-Restly.

Views are real Python classes — not decorators or function wrappers — so the
full Python inheritance model applies:

  - Class variables (model, schema, exclude_routes, include_pagination_metadata,
    id_type, dependencies) are inherited and can be overridden per-subclass.
  - perform_* handlers and custom routes defined on a base view are shared by all
    subclasses; each subclass can further override and call super().
  - Instance-level FastAPI dependencies declared as annotations on a base class
    are available on all subclasses as self.<name>.
  - Class-level `dependencies = [Depends(...)]` is inherited and applied to all
    routes on every registered subclass.
  - URL prefixes concatenate from base to derived, enabling a common namespace
    prefix (e.g. "/api/v1") to be declared once on a shared base.
"""

from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

import fastapi_restly as fr

# ---------------------------------------------------------------------------
# 1. Class-variable inheritance: model, schema
# ---------------------------------------------------------------------------


def test_inherit_model_and_schema(sync_db):
    """A subclass that only defines prefix uses the base's model and schema."""
    engine, _ = sync_db

    class Widget(fr.IDBase):
        name: Mapped[str]

    class WidgetSchema(fr.IDSchema):
        name: str

    class WidgetBase(fr.RestView):
        model = Widget
        schema = WidgetSchema

    class WidgetView(WidgetBase):
        prefix = "/widgets"

    fr.DataclassBase.metadata.create_all(engine)

    with fr.open_session() as session:
        view = WidgetView()
        view.session = session

        created = view.create(WidgetSchema(id=0, name="Cog"))
        assert created.name == "Cog"
        assert created.id is not None

        fetched = view.get(created.id)
        assert fetched.name == "Cog"

        items = view.listing({})
        assert len(items) == 1


# ---------------------------------------------------------------------------
# 2. perform_* override shared across multiple subclasses
# ---------------------------------------------------------------------------


def test_handler_override_shared_across_subclasses(sync_db):
    """
    A perform_* override on a base view applies to every subclass.
    Two independent views that inherit the same base both exhibit the override.
    """
    engine, _ = sync_db

    call_log: list[str] = []

    class Tag(fr.IDBase):
        label: Mapped[str]

    class TagSchema(fr.IDSchema):
        label: str

    class AuditBase(fr.RestView):
        model = Tag
        schema = TagSchema

        def perform_create(self, schema_obj):
            call_log.append("audit")
            return super().perform_create(schema_obj)

    class ViewA(AuditBase):
        prefix = "/tags-a"

    class ViewB(AuditBase):
        prefix = "/tags-b"

    fr.DataclassBase.metadata.create_all(engine)

    with fr.open_session() as session:
        view_a = ViewA()
        view_a.session = session
        view_a.create(TagSchema(id=0, label="alpha"))

        view_b = ViewB()
        view_b.session = session
        view_b.create(TagSchema(id=0, label="beta"))

    assert call_log == ["audit", "audit"]


# ---------------------------------------------------------------------------
# 3. super() chaining between subclass and base override
# ---------------------------------------------------------------------------


def test_super_chain_in_handler_override(sync_db):
    """Subclass can override perform_* and call super() to chain with the base."""
    engine, _ = sync_db

    call_log: list[str] = []

    class Note(fr.IDBase):
        text: Mapped[str]

    class NoteSchema(fr.IDSchema):
        text: str

    class LogBase(fr.RestView):
        model = Note
        schema = NoteSchema

        def perform_create(self, schema_obj):
            call_log.append("base_pre")
            result = super().perform_create(schema_obj)
            call_log.append("base_post")
            return result

    class NoteView(LogBase):
        prefix = "/notes"

        def perform_create(self, schema_obj):
            call_log.append("sub_pre")
            result = super().perform_create(schema_obj)
            call_log.append("sub_post")
            return result

    fr.DataclassBase.metadata.create_all(engine)

    with fr.open_session() as session:
        view = NoteView()
        view.session = session
        view.create(NoteSchema(id=0, text="hello"))

    assert call_log == ["sub_pre", "base_pre", "base_post", "sub_post"]


# ---------------------------------------------------------------------------
# 4. Inherited class-variable: exclude_routes
# ---------------------------------------------------------------------------


def test_inherit_exclude_routes(sync_db):
    """exclude_routes set on a base view is inherited; excluded endpoints are absent."""
    engine, _ = sync_db

    class Entry(fr.IDBase):
        value: Mapped[str]

    class EntrySchema(fr.IDSchema):
        value: str

    class ReadOnlyBase(fr.RestView):
        model = Entry
        schema = EntrySchema
        exclude_routes = ("create", "update", "delete")

    app = FastAPI()

    @fr.include_view(app)
    class EntryView(ReadOnlyBase):
        prefix = "/entries"

    fr.DataclassBase.metadata.create_all(engine)

    client = TestClient(app)

    # List and detail endpoints exist
    assert client.get("/entries/").status_code == 200

    # Mutating endpoints are excluded → 405 or 404
    resp = client.post("/entries/", json={"value": "x"})
    assert resp.status_code in (404, 405)

    resp = client.delete("/entries/1")
    assert resp.status_code in (404, 405)


def test_exclude_routes_accepts_view_route_enum(sync_db):
    engine, _ = sync_db

    class Event(fr.IDBase):
        title: Mapped[str]

    class EventSchema(fr.IDSchema):
        title: str

    class EventView(fr.RestView):
        prefix = "/events"
        model = Event
        schema = EventSchema
        exclude_routes = (fr.ViewRoute.DELETE,)

    app = FastAPI()
    fr.include_view(app, EventView)

    fr.DataclassBase.metadata.create_all(engine)

    client = TestClient(app)
    assert client.get("/events/").status_code == 200
    assert client.delete("/events/1").status_code in (404, 405)


# ---------------------------------------------------------------------------
# 5. Inherited class-variable: include_pagination_metadata
# ---------------------------------------------------------------------------


def test_inherit_include_pagination_metadata(sync_db):
    """include_pagination_metadata=True on a base view is inherited by subclasses."""
    engine, _ = sync_db

    class Ticket(fr.IDBase):
        title: Mapped[str]

    class TicketSchema(fr.IDSchema):
        title: str

    class PaginatedBase(fr.RestView):
        model = Ticket
        schema = TicketSchema
        include_pagination_metadata = True

    class TicketView(PaginatedBase):
        prefix = "/tickets"

    fr.DataclassBase.metadata.create_all(engine)

    with fr.open_session() as session:
        view = TicketView()
        view.session = session
        view.create(TicketSchema(id=0, title="Bug"))
        view.create(TicketSchema(id=0, title="Feature"))

        result = view.listing({"page": "1", "page_size": "10"})

    assert isinstance(result, dict)
    assert result["total"] == 2
    assert result["page"] == 1
    assert {item.title for item in result["items"]} == {"Bug", "Feature"}


# ---------------------------------------------------------------------------
# 6. Inherited soft-delete via delete_object override
# ---------------------------------------------------------------------------


def test_inherit_soft_delete_via_delete_object(sync_db):
    """
    A delete_object override on a base class (e.g. soft-delete) is inherited
    by all subclasses without repeating the implementation.
    """
    engine, _ = sync_db

    class Record(fr.IDBase):
        name: Mapped[str]
        deleted: Mapped[bool] = mapped_column(default=False)

    class RecordSchema(fr.IDSchema):
        name: str
        deleted: bool

    class SoftDeleteBase(fr.RestView):
        model = Record
        schema = RecordSchema

        def delete_object(self, obj):
            obj.deleted = True
            self.session.flush()

    class RecordView(SoftDeleteBase):
        prefix = "/records"

    fr.DataclassBase.metadata.create_all(engine)

    with fr.open_session() as session:
        view = RecordView()
        view.session = session

        rec = view.create(RecordSchema(id=0, name="doc", deleted=False))
        assert rec.deleted is False

        view.delete(rec.id)

        # Still exists in database but is flagged deleted
        fetched = view.get(rec.id)
        assert fetched.deleted is True


# ---------------------------------------------------------------------------
# 7. Custom route on base class inherited by subclass
# ---------------------------------------------------------------------------


def test_custom_route_on_base_inherited_by_subclass(sync_db):
    """A @fr.get endpoint defined on a base class is available on subclasses."""
    engine, _ = sync_db

    class Item(fr.IDBase):
        name: Mapped[str]

    class ItemSchema(fr.IDSchema):
        name: str

    class ItemBase(fr.RestView):
        model = Item
        schema = ItemSchema

        @fr.get("/ping")
        def ping(self):
            return {"pong": True}

    app = FastAPI()

    @fr.include_view(app)
    class ItemView(ItemBase):
        prefix = "/items"

    fr.DataclassBase.metadata.create_all(engine)

    client = TestClient(app)
    resp = client.get("/items/ping")
    assert resp.status_code == 200
    assert resp.json() == {"pong": True}


# ---------------------------------------------------------------------------
# 8. Instance-level dependency inherited from base class
# ---------------------------------------------------------------------------


def test_instance_level_dependency_inherited(sync_db):
    """
    An instance dependency (annotation with Depends) declared on a base view
    is injected into all subclasses as self.<name>.
    """
    engine, _ = sync_db

    captured: dict = {}

    def get_request_id() -> str:
        return "req-42"

    class Box(fr.IDBase):
        label: Mapped[str]

    class BoxSchema(fr.IDSchema):
        label: str

    class TrackedBase(fr.RestView):
        request_id: Annotated[str, Depends(get_request_id)]
        model = Box
        schema = BoxSchema

        def perform_create(self, schema_obj):
            captured["request_id"] = self.request_id
            return super().perform_create(schema_obj)

    app = FastAPI()

    @fr.include_view(app)
    class BoxView(TrackedBase):
        prefix = "/boxes"

    fr.DataclassBase.metadata.create_all(engine)

    client = TestClient(app)
    resp = client.post("/boxes/", json={"label": "small"})
    assert resp.status_code == 201
    assert captured["request_id"] == "req-42"


def test_subclass_can_override_the_restly_session_dependency():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    make_session = sessionmaker(bind=engine, expire_on_commit=False)

    def get_reporting_db():
        with make_session() as session:
            yield session

    class Report(fr.IDBase):
        title: Mapped[str]

    class ReportRead(fr.IDSchema):
        title: str

    app = FastAPI()

    @fr.include_view(app)
    class ReportView(fr.RestView):
        prefix = "/reports"
        model = Report
        schema = ReportRead
        session: Annotated[Session, Depends(get_reporting_db)]

    try:
        fr.DataclassBase.metadata.create_all(engine)
        with make_session() as session:
            session.add(Report(title="Revenue"))
            session.commit()

        response = TestClient(app).get("/reports/")

        assert response.status_code == 200
        assert response.json() == [{"id": 1, "title": "Revenue"}]
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# 9. Class-level dependencies applied to routes
# ---------------------------------------------------------------------------


def test_class_level_dependencies_applied_to_routes(sync_db):
    """
    dependencies = [Depends(fn)] on a view is applied to every route it registers.
    """
    engine, _ = sync_db

    call_log: list[str] = []

    def auth_guard():
        call_log.append("auth")

    class Peg(fr.IDBase):
        name: Mapped[str]

    class PegSchema(fr.IDSchema):
        name: str

    app = FastAPI()

    @fr.include_view(app)
    class PegView(fr.RestView):
        prefix = "/pegs"
        model = Peg
        schema = PegSchema
        dependencies = [Depends(auth_guard)]

    fr.DataclassBase.metadata.create_all(engine)

    client = TestClient(app)
    client.get("/pegs/")
    client.get("/pegs/")

    assert call_log == ["auth", "auth"]


# ---------------------------------------------------------------------------
# 10. Class-level dependencies inherited by subclass
# ---------------------------------------------------------------------------


def test_class_level_dependencies_inherited_by_subclass(sync_db):
    """
    dependencies defined on a base view are inherited; all registered subclasses
    get the same router-level guards without repeating the declaration.
    """
    engine, _ = sync_db

    call_log: list[str] = []

    def require_auth():
        call_log.append("auth")

    class Pin(fr.IDBase):
        name: Mapped[str]

    class PinSchema(fr.IDSchema):
        name: str

    class AuthBase(fr.RestView):
        model = Pin
        schema = PinSchema
        dependencies = [Depends(require_auth)]

    app = FastAPI()

    @fr.include_view(app)
    class PinView(AuthBase):
        prefix = "/pins"

    fr.DataclassBase.metadata.create_all(engine)

    client = TestClient(app)
    client.get("/pins/")
    assert call_log == ["auth"]


# ---------------------------------------------------------------------------
# 11. Prefix concatenation — two levels
# ---------------------------------------------------------------------------


def test_prefix_concatenation_two_levels(sync_db):
    """
    A prefix on a base class is prepended to the subclass prefix.
    Base "/api/v1" + child "/bolts" → routes at /api/v1/bolts/.
    """
    engine, _ = sync_db

    class Bolt(fr.IDBase):
        size: Mapped[str]

    class BoltSchema(fr.IDSchema):
        size: str

    class ApiV1(fr.RestView):
        prefix = "/api/v1"

    app = FastAPI()

    @fr.include_view(app)
    class BoltView(ApiV1):
        prefix = "/bolts"
        model = Bolt
        schema = BoltSchema

    fr.DataclassBase.metadata.create_all(engine)

    client = TestClient(app)
    resp = client.post("/api/v1/bolts/", json={"size": "M6"})
    assert resp.status_code == 201
    assert resp.json()["size"] == "M6"

    resp = client.get("/api/v1/bolts/")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    # Without the prefix, routes do not exist
    assert client.get("/bolts/").status_code == 404


# ---------------------------------------------------------------------------
# 12. Prefix concatenation — three levels
# ---------------------------------------------------------------------------


def test_prefix_concatenation_three_levels(sync_db):
    """
    Prefixes from three class levels are concatenated in MRO order.
    "/admin" + "/v2" + "/nuts" → /admin/v2/nuts/.
    """
    engine, _ = sync_db

    class Nut(fr.IDBase):
        grade: Mapped[str]

    class NutSchema(fr.IDSchema):
        grade: str

    class AdminBase(fr.RestView):
        prefix = "/admin"

    class V2Base(AdminBase):
        prefix = "/v2"

    app = FastAPI()

    @fr.include_view(app)
    class NutView(V2Base):
        prefix = "/nuts"
        model = Nut
        schema = NutSchema

    fr.DataclassBase.metadata.create_all(engine)

    client = TestClient(app)
    resp = client.post("/admin/v2/nuts/", json={"grade": "8.8"})
    assert resp.status_code == 201

    resp = client.get("/admin/v2/nuts/")
    assert resp.status_code == 200
    assert resp.json()[0]["grade"] == "8.8"


# ---------------------------------------------------------------------------
# 13. Base-only prefix (subclass adds no prefix segment)
# ---------------------------------------------------------------------------


def test_base_only_prefix(sync_db):
    """
    If only the base class defines prefix and the subclass does not, the base
    prefix alone is used — standard attribute inheritance, unchanged behavior.
    """
    engine, _ = sync_db

    class Screw(fr.IDBase):
        length: Mapped[int]

    class ScrewSchema(fr.IDSchema):
        length: int

    class ScrewBase(fr.RestView):
        prefix = "/screws"
        model = Screw
        schema = ScrewSchema

    app = FastAPI()

    @fr.include_view(app)
    class ScrewView(ScrewBase):
        pass  # no prefix override — inherits "/screws" from base

    fr.DataclassBase.metadata.create_all(engine)

    client = TestClient(app)
    resp = client.post("/screws/", json={"length": 10})
    assert resp.status_code == 201
    assert resp.json()["length"] == 10
