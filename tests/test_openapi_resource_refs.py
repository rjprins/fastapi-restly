"""
Tests for automatic x-resource-ref OpenAPI extension.

Verifies that include_view() causes FK columns and IDSchema relationship fields
to be annotated with x-resource-ref in the generated OpenAPI spec.
"""

import fastapi
import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

import fastapi_restly as fr

from .conftest import create_tables


def _build_app():
    """Return a fresh FastAPI app with Author/Book views registered."""

    class Author(fr.IDBase):
        name: Mapped[str]
        books: Mapped[list["Book"]] = relationship(
            back_populates="author", lazy="selectin", default_factory=list
        )

    class Book(fr.IDBase):
        title: Mapped[str]
        author_id: Mapped[int] = mapped_column(sa.ForeignKey(Author.id))
        # Full nested object — should NOT get x-resource-ref
        author: Mapped[Author] = relationship(back_populates="books", lazy="selectin")

    class AuthorSchema(fr.IDSchema):
        name: str
        books: fr.ReadOnly[list[fr.IDRef[Book]]] = []

    class BookSchema(fr.IDSchema):
        title: str
        author_id: int
        author: fr.ReadOnly[AuthorSchema | None]

    app = fastapi.FastAPI()

    @fr.include_view(app)
    class AuthorView(fr.AsyncReactAdminView):
        prefix = "/authors"
        model = Author
        schema = AuthorSchema

    @fr.include_view(app)
    class BookView(fr.AsyncReactAdminView):
        prefix = "/books"
        model = Book
        schema = BookSchema

    create_tables()
    return app


# ---------------------------------------------------------------------------
# Main schema
# ---------------------------------------------------------------------------


def test_fk_column_gets_x_resource_ref():
    app = _build_app()
    props = app.openapi()["components"]["schemas"]["BookResponse"]["properties"]
    assert props["author_id"].get("x-resource-ref") == "authors"


def test_relationship_with_idref_gets_x_resource_ref():
    app = _build_app()
    props = app.openapi()["components"]["schemas"]["AuthorResponse"]["properties"]
    assert props["books"].get("x-resource-ref") == "books"


def test_view_schema_nested_in_another_schema_gets_x_resource_ref():
    """BookSchema nests AuthorSchema, so the spec holds AuthorSchema next to
    AuthorResponse. Both get the annotation."""
    app = _build_app()
    props = app.openapi()["components"]["schemas"]["AuthorSchema"]["properties"]
    assert props["books"].get("x-resource-ref") == "books"


def test_idref_openapi_schema_is_scalar():
    spec = _build_app().openapi()
    props = spec["components"]["schemas"]["AuthorResponse"]["properties"]
    item_ref = props["books"]["items"]["$ref"]
    item_schema_name = item_ref.removeprefix("#/components/schemas/")

    assert spec["components"]["schemas"][item_schema_name] == {"type": "integer"}


def test_nested_schema_relationship_not_annotated():
    """Full nested object (author: AuthorSchema) must not get x-resource-ref."""
    app = _build_app()
    props = app.openapi()["components"]["schemas"]["BookResponse"]["properties"]
    assert "x-resource-ref" not in props["author"]


def test_plain_field_not_annotated():
    app = _build_app()
    props = app.openapi()["components"]["schemas"]["BookResponse"]["properties"]
    assert "x-resource-ref" not in props["title"]


# ---------------------------------------------------------------------------
# Derived schemas (Create / Update)
# ---------------------------------------------------------------------------


def test_create_schema_fk_gets_x_resource_ref():
    app = _build_app()
    props = app.openapi()["components"]["schemas"]["BookCreate"]["properties"]
    assert props["author_id"].get("x-resource-ref") == "authors"


def test_update_schema_fk_gets_x_resource_ref():
    """BookUpdate wraps author_id in anyOf — annotation goes on the property root."""
    app = _build_app()
    props = app.openapi()["components"]["schemas"]["BookUpdate"]["properties"]
    assert props["author_id"].get("x-resource-ref") == "authors"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_unregistered_fk_target_not_annotated():
    """If a FK target model has no view registered, the field is silently skipped."""

    class Tag(fr.IDBase):
        label: Mapped[str]

    class Item(fr.IDBase):
        name: Mapped[str]
        tag_id: Mapped[int] = mapped_column(sa.ForeignKey(Tag.id))

    class ItemSchema(fr.IDSchema):
        name: str
        tag_id: int

    app = fastapi.FastAPI()

    @fr.include_view(app)
    class ItemView(fr.AsyncReactAdminView):
        prefix = "/items"
        model = Item
        schema = ItemSchema

    create_tables()
    props = app.openapi()["components"]["schemas"]["ItemResponse"]["properties"]
    assert "x-resource-ref" not in props["tag_id"]


def test_each_class_gets_the_refs_of_its_own_fields():
    """The view's schema takes a team id, its own response class sends the
    nested team. Only the id gets x-resource-ref."""

    class Team(fr.IDBase):
        name: Mapped[str]

    class Player(fr.IDBase):
        name: Mapped[str]
        team_id: Mapped[int] = mapped_column(sa.ForeignKey(Team.id))
        team: Mapped[Team] = relationship(lazy="selectin")

    class TeamSchema(fr.IDSchema):
        name: str

    class PlayerSchema(fr.IDSchema):
        name: str
        team: fr.IDRef[Team]

    class PlayerResponse(fr.IDSchema):
        name: str
        team: TeamSchema

    app = fastapi.FastAPI()

    @fr.include_view(app)
    class TeamView(fr.AsyncRestView):
        prefix = "/teams"
        model = Team
        schema = TeamSchema

    @fr.include_view(app)
    class PlayerView(fr.AsyncRestView):
        prefix = "/players"
        model = Player
        schema = PlayerSchema
        schema_response = PlayerResponse

    create_tables()
    schemas = app.openapi()["components"]["schemas"]
    assert "x-resource-ref" not in schemas["PlayerResponse"]["properties"]["team"]
    assert schemas["PlayerCreate"]["properties"]["team"]["x-resource-ref"] == "teams"


def test_spec_is_idempotent_on_multiple_openapi_calls():
    """Calling app.openapi() twice must not duplicate or corrupt annotations."""
    app = _build_app()
    spec1 = app.openapi()
    spec2 = app.openapi()
    ref1 = spec1["components"]["schemas"]["BookResponse"]["properties"][
        "author_id"
    ].get("x-resource-ref")
    ref2 = spec2["components"]["schemas"]["BookResponse"]["properties"][
        "author_id"
    ].get("x-resource-ref")
    assert ref1 == ref2 == "authors"
