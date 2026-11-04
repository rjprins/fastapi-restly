"""
Tests for automatic x-resource-ref OpenAPI extension.

Verifies that include_view() causes FK columns and IDSchema relationship fields
to be annotated with x-resource-ref in the generated OpenAPI spec.
"""

from typing import Annotated, Generic, TypeVar

import fastapi
import pydantic
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

import fastapi_restly as fr

from ._models import SharedBase
from .conftest import create_tables


def _build_app():
    """Return a fresh FastAPI app with Author/Book views registered."""

    class Author(fr.IDMixin, SharedBase):
        name: Mapped[str]
        books: Mapped[list["Book"]] = relationship(
            back_populates="author", lazy="selectin", default_factory=list
        )

    class Book(fr.IDMixin, SharedBase):
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

    class Tag(fr.IDMixin, SharedBase):
        label: Mapped[str]

    class Item(fr.IDMixin, SharedBase):
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


def test_plain_view_with_a_model_is_not_a_resource():
    """A plain View may set a model for its own routes. It registers, and a
    foreign key to that model gets no x-resource-ref."""

    class Label(fr.IDMixin, SharedBase):
        text: Mapped[str]

    class Note(fr.IDMixin, SharedBase):
        title: Mapped[str]
        label_id: Mapped[int] = mapped_column(sa.ForeignKey(Label.id))

    class NoteSchema(fr.IDSchema):
        title: str
        label_id: int

    app = fastapi.FastAPI()

    @fr.include_view(app)
    class LabelStatsView(fr.View):
        prefix = "/labels"
        model = Label

        @fr.get("/count")
        async def count(self) -> int:
            return 0

    @fr.include_view(app)
    class NoteView(fr.AsyncRestView):
        prefix = "/notes"
        model = Note
        schema = NoteSchema

    create_tables()
    props = app.openapi()["components"]["schemas"]["NoteResponse"]["properties"]
    assert "x-resource-ref" not in props["label_id"]


def test_each_class_gets_the_refs_of_its_own_fields():
    """The main schema takes a team id, its own response schema sends the
    nested team. Only the id gets x-resource-ref."""

    class Team(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class Player(fr.IDMixin, SharedBase):
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
        response_schema = PlayerResponse

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


def test_the_items_of_a_list_response_schema_get_x_resource_ref():
    class Author(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class Pet(fr.IDMixin, SharedBase):
        name: Mapped[str]
        owner_id: Mapped[int] = mapped_column(sa.ForeignKey(Author.id))

    class OwnerSchema(fr.IDSchema):
        name: str

    class PetSchema(fr.IDSchema):
        name: str
        owner_id: int
        note: str = "n"

    class PetSummary(fr.IDSchema):
        owner_id: int

    app = fastapi.FastAPI()

    @fr.include_view(app)
    class AuthorView(fr.AsyncRestView):
        prefix = "/authors"
        model = Author
        schema = OwnerSchema

    @fr.include_view(app)
    class PetView(fr.AsyncRestView):
        prefix = "/pets"
        model = Pet
        schema = PetSchema
        list_response_schema = fr.views.NumberedEnvelope[PetSummary]

    schemas = app.openapi()["components"]["schemas"]
    assert (
        schemas["PetResponse"]["properties"]["owner_id"]["x-resource-ref"] == "authors"
    )
    assert (
        schemas["PetSummary"]["properties"]["owner_id"]["x-resource-ref"] == "authors"
    )


def _list_view_with(make_shape):
    """The spec of a view with a foreign key, with the item class that
    ``make_shape`` makes from the item class ``PetSummary``."""

    class Owner2(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class Pet2(fr.IDMixin, SharedBase):
        name: Mapped[str]
        owner_id: Mapped[int] = mapped_column(sa.ForeignKey(Owner2.id))

    class OwnerView(fr.AsyncRestView):
        prefix = "/owners"
        model = Owner2

    T = TypeVar("T")

    class Wrap(pydantic.BaseModel, Generic[T]):
        owner_id: int
        extra: T | None = None

    class PetSummary(pydantic.BaseModel):
        owner_id: int

    class PetSchema(fr.IDSchema):
        name: str
        owner_id: int

    class PetView(fr.AsyncRestView):
        prefix = "/pets"
        model = Pet2
        schema = PetSchema
        list_response_schema = make_shape(PetSummary, Wrap)

    app = fastapi.FastAPI()
    fr.include_view(app, OwnerView)
    fr.include_view(app, PetView)
    create_tables()
    return app.openapi()["components"]["schemas"]


@pytest.mark.parametrize(
    ("make_shape", "name"),
    [
        (lambda item, wrap: list[item | None], "PetSummary"),
        (lambda item, wrap: list[Annotated[item, "note"]], "PetSummary"),
        (lambda item, wrap: list[wrap[int]], "Wrap_int_"),
    ],
    ids=["optional", "annotated", "generic"],
)
def test_a_wrapped_or_generic_item_class_gets_x_resource_ref(make_shape, name):
    schemas = _list_view_with(make_shape)
    assert schemas[name]["properties"]["owner_id"]["x-resource-ref"] == "owners"
