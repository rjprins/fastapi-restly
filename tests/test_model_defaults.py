"""Tests for ``fr.ModelDefaults`` on a declarative base of the user's own."""

import asyncio
import dataclasses
import enum
import inspect
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import DateTime, ForeignKey, MetaData, Numeric, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import (
    DeclarativeBase,
    DeclarativeBaseNoMeta,
    Mapped,
    MappedAsDataclass,
    mapped_column,
    registry,
    relationship,
)

import fastapi_restly as fr


class Color(enum.Enum):
    RED = "red"


def test_each_base_has_its_own_registry_and_metadata():
    class BaseA(fr.ModelDefaults, DeclarativeBase):
        pass

    class BaseB(fr.ModelDefaults, DeclarativeBase):
        pass

    class User(fr.IDMixin, BaseA):
        name: Mapped[str]

    # The same class and table name on another base does not clash.
    class User(fr.IDMixin, BaseB):  # type: ignore[no-redef]  # noqa: F811
        email: Mapped[str]

    assert BaseA.registry is not BaseB.registry
    assert list(BaseA.metadata.tables) == ["user"]
    assert list(BaseB.metadata.tables) == ["user"]


def test_models_get_restly_defaults():
    class Base(fr.ModelDefaults, DeclarativeBase):
        pass

    class BlogPost(fr.IDMixin, Base):
        version: Mapped[int] = mapped_column(default=1)
        title: Mapped[str]
        published_at: Mapped[datetime]
        color: Mapped[Color]

    table = BlogPost.__table__
    assert table.name == "blog_post"
    assert table.c.published_at.type.timezone is True
    assert table.c.color.type.native_enum is False
    assert table.c.color.type.length == 64

    # A keyword-only dataclass constructor: a required field may follow a
    # field with a default.
    assert dataclasses.is_dataclass(BlogPost)
    parameters = inspect.signature(BlogPost.__init__).parameters
    assert all(
        p.kind is inspect.Parameter.KEYWORD_ONLY
        for name, p in parameters.items()
        if name != "self"
    )
    post = BlogPost(title="Hello", published_at=datetime.now(), color=Color.RED)
    assert hasattr(post, "awaitable_attrs")


def test_own_type_annotation_map_entries_win():
    class Base(fr.ModelDefaults, DeclarativeBase):
        type_annotation_map = {Decimal: Numeric(12, 2), datetime: DateTime()}

    class Invoice(fr.IDMixin, Base):
        amount: Mapped[Decimal]
        issued_at: Mapped[datetime]
        color: Mapped[Color]

    table = Invoice.__table__
    assert table.c.amount.type.precision == 12
    assert table.c.issued_at.type.timezone is False
    # Restly's entry for the key the base leaves out still applies.
    assert table.c.color.type.native_enum is False


def test_entries_of_an_explicit_registry_win():
    class Base(fr.ModelDefaults, DeclarativeBase):
        registry = registry(type_annotation_map={datetime: DateTime()})

    class Event(fr.IDMixin, Base):
        happened_at: Mapped[datetime]

    assert Event.__table__.c.happened_at.type.timezone is False


def test_own_metadata_is_used():
    app_metadata = MetaData(naming_convention={"pk": "pk_%(table_name)s"}, schema="app")

    class Base(fr.ModelDefaults, DeclarativeBase):
        metadata = app_metadata

    class Item(fr.IDMixin, Base):
        name: Mapped[str]

    assert Item.__table__.metadata is app_metadata
    assert Item.__table__.schema == "app"
    assert Item.__table__.primary_key.name == "pk_item"


def test_order_of_the_bases_does_not_matter():
    class Base(DeclarativeBase, fr.ModelDefaults):
        pass

    class Reading(fr.IDMixin, Base):
        taken_at: Mapped[datetime]

    assert Reading.__table__.c.taken_at.type.timezone is True


def test_models_compare_by_identity():
    class Base(fr.ModelDefaults, DeclarativeBase):
        pass

    class Tag(Base):
        id: Mapped[int] = mapped_column(primary_key=True, init=False)
        name: Mapped[str]

    class Post(fr.TimestampsMixin, fr.IDMixin, Base):
        title: Mapped[str]

    for model, kwargs in [(Tag, {"name": "a"}), (Post, {"title": "a"})]:
        first, second = model(**kwargs), model(**kwargs)
        assert first != second
        assert first == first
        assert len({first, second}) == 2


def test_a_model_can_compare_by_value():
    class Base(fr.ModelDefaults, DeclarativeBase):
        pass

    class Post(fr.IDMixin, Base, eq=True):
        title: Mapped[str]

    assert Post(title="a") == Post(title="a")


def test_a_mixin_with_the_options_of_id_mixin_keeps_identity_equality():
    class Base(fr.ModelDefaults, DeclarativeBase):
        pass

    class Stamped(MappedAsDataclass, kw_only=True, eq=False, repr=False):
        note: Mapped[str] = mapped_column(default="")

    class Post(Stamped, fr.IDMixin, Base):
        title: Mapped[str]

    first, second = Post(title="a"), Post(title="a")
    assert first != second
    assert len({first, second}) == 2


def test_a_mixin_without_eq_false_first_in_the_bases_raises():
    """SQLAlchemy takes the model's options from the mixin, so the model
    compared its fields, and comparing two objects could load a relationship.
    Before, nothing said so."""

    class Base(fr.ModelDefaults, DeclarativeBase):
        pass

    class Stamped(MappedAsDataclass, kw_only=True):
        note: Mapped[str] = mapped_column(default="")

    message = (
        r"compares its objects field by field, because its dataclass base "
        r"Stamped does not set eq=False.*"
        r"class Stamped\(MappedAsDataclass, kw_only=True, eq=False, repr=False\)"
    )
    # First in the bases: SQLAlchemy takes the model's options from it.
    with pytest.raises(fr.exc.RestlyConfigurationError, match="Post " + message):

        class Post(Stamped, fr.IDMixin, Base):
            title: Mapped[str]

    # Later in the bases: the model inherits the mixin's __eq__, which
    # compares the mixin's fields only. eq=False on the model does not help.
    with pytest.raises(fr.exc.RestlyConfigurationError, match="Note " + message):

        class Note(fr.IDMixin, Stamped, Base, eq=False):
            title: Mapped[str]


def test_a_model_with_such_a_mixin_can_ask_for_field_equality():
    class Base(fr.ModelDefaults, DeclarativeBase):
        pass

    class Stamped(MappedAsDataclass, kw_only=True, repr=False):
        note: Mapped[str] = mapped_column(default="")

    class Post(Stamped, fr.IDMixin, Base, eq=True):
        title: Mapped[str]
        kind: Mapped[str] = mapped_column(default="post", init=False)
        __mapper_args__ = {"polymorphic_on": "kind", "polymorphic_identity": "post"}

    assert Post(title="a") == Post(title="a")

    # A subclass of the model (single table) takes the model's choice.
    class Article(Post):
        __tablename__ = None  # type: ignore[assignment]
        __mapper_args__ = {"polymorphic_identity": "article"}

    assert Article(title="a") == Article(title="a")


def test_an_abstract_model_can_ask_for_field_equality():
    """An ``__abstract__`` model has no mapper, but it is a model of the
    base, not a mixin."""

    class Base(fr.ModelDefaults, DeclarativeBase):
        pass

    class Content(fr.IDMixin, Base, eq=True):
        __abstract__ = True
        title: Mapped[str]

    class Post(Content):
        pass

    assert Post(title="a") == Post(title="a")


def test_a_model_that_writes_its_own_eq_keeps_it():
    class Base(fr.ModelDefaults, DeclarativeBase):
        pass

    class Stamped(MappedAsDataclass, kw_only=True, repr=False):
        note: Mapped[str] = mapped_column(default="")

    class Post(fr.IDMixin, Stamped, Base):
        title: Mapped[str]

        def __eq__(self, other: object) -> bool:
            return isinstance(other, Post) and other.title == self.title

        __hash__ = object.__hash__

    assert Post(title="a") == Post(title="a", note="other")


def test_repr_never_loads():
    """A dataclass repr reads every field, so in async code it raised
    MissingGreenlet for a relationship or a column that was not loaded."""

    class Base(fr.ModelDefaults, DeclarativeBase):
        pass

    class Author(fr.IDMixin, Base):
        name: Mapped[str]
        posts: Mapped[list["Post"]] = relationship(
            default_factory=list, back_populates="author"
        )

    class Post(fr.TimestampsMixin, fr.IDMixin, Base):
        title: Mapped[str]
        author_id: Mapped[int] = mapped_column(ForeignKey("author.id"))
        author: Mapped[Author] = relationship(init=False, back_populates="posts")

    async def run() -> list[str]:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        make_session = async_sessionmaker(engine)
        async with make_session() as session:
            author = Author(name="Anna")
            new = repr(author)
            session.add(author)
            await session.flush()
            session.add(Post(title="Hello", author_id=author.id))
            await session.commit()
            expired = repr(author)
        async with make_session() as session:
            loaded = repr((await session.scalars(select(Author))).one())
            post = repr((await session.scalars(select(Post))).one())
        await engine.dispose()
        return [new, expired, loaded, post]

    new, expired, loaded, post = asyncio.run(run())
    assert new == "Author(name='Anna', ...)"
    # the primary key is known without a query
    assert expired == "Author(id=1, ...)"
    # the posts are not loaded, and a relationship is never shown
    assert loaded == "Author(id=1, name='Anna')"
    # the primary key comes first
    assert post.startswith("Post(id=1, title='Hello', author_id=1, created_at=")


def test_a_model_can_ask_for_the_dataclass_repr():
    class Base(fr.ModelDefaults, DeclarativeBase):
        pass

    class Post(fr.IDMixin, Base, repr=True):
        title: Mapped[str]

    assert repr(Post(title="a")).endswith("Post(id=None, title='a')")


def test_a_mixin_without_repr_false_raises():
    """The model got a dataclass repr that read every field, or the mixin's
    repr, which showed the mixin's fields only. In async code either could
    raise MissingGreenlet. Before, nothing said so."""

    class Base(fr.ModelDefaults, DeclarativeBase):
        pass

    class Stamped(MappedAsDataclass, kw_only=True, eq=False):
        note: Mapped[str] = mapped_column(default="")

    message = (
        r"gets a dataclass repr, because its dataclass base Stamped does not "
        r"set repr=False.*"
        r"class Stamped\(MappedAsDataclass, kw_only=True, eq=False, repr=False\)"
    )
    # First in the bases: SQLAlchemy takes the model's options from it.
    with pytest.raises(fr.exc.RestlyConfigurationError, match="Post " + message):

        class Post(Stamped, fr.IDMixin, Base):
            title: Mapped[str]

    # Later in the bases: the model inherits the mixin's __repr__.
    # repr=False on the model does not help.
    with pytest.raises(fr.exc.RestlyConfigurationError, match="Note " + message):

        class Note(fr.IDMixin, Stamped, Base, repr=False):
            title: Mapped[str]


def test_a_model_with_such_a_mixin_can_ask_for_the_dataclass_repr():
    class Base(fr.ModelDefaults, DeclarativeBase):
        pass

    class Stamped(MappedAsDataclass, kw_only=True, eq=False):
        note: Mapped[str] = mapped_column(default="")

    class Post(Stamped, fr.IDMixin, Base, repr=True):
        title: Mapped[str]

    class Note(fr.IDMixin, Stamped, Base, repr=True):
        title: Mapped[str]

    assert repr(Post(title="a")).endswith("Post(id=None, note='', title='a')")
    assert repr(Note(title="a")).endswith("Note(note='', id=None, title='a')")


def test_a_model_that_writes_its_own_repr_keeps_it():
    class Base(fr.ModelDefaults, DeclarativeBase):
        pass

    class Stamped(MappedAsDataclass, kw_only=True, eq=False):
        note: Mapped[str] = mapped_column(default="")

    class Post(Stamped, fr.IDMixin, Base):
        title: Mapped[str]

        def __repr__(self) -> str:
            return "from the model"

    assert repr(Post(title="a")) == "from the model"


def test_a_parent_model_that_writes_its_own_method_does_not_exempt_its_children():
    """The parent's own __repr__ and __eq__ were fine. A model below it still
    got the dataclass methods from the parent's mixin, and nothing raised."""

    class Base(fr.ModelDefaults, DeclarativeBase):
        pass

    class Stamped(MappedAsDataclass, kw_only=True):
        note: Mapped[str] = mapped_column(default="")

    class Content(Stamped, fr.IDMixin, Base):
        __abstract__ = True
        title: Mapped[str]

        def __eq__(self, other: object) -> bool:
            return self is other

        __hash__ = object.__hash__

        def __repr__(self) -> str:
            return "from Content"

    with pytest.raises(fr.exc.RestlyConfigurationError, match="Post compares"):

        class Post(Content):
            pass


@pytest.mark.parametrize("option", ["eq", "repr"])
def test_the_check_works_in_either_order_of_the_bases(option):
    """With DeclarativeBase first, SQLAlchemy made the class a dataclass before
    the check looked for the methods that the model wrote, and nothing
    raised."""

    class Base(DeclarativeBase, fr.ModelDefaults):
        pass

    other = "repr" if option == "eq" else "eq"

    class Stamped(MappedAsDataclass, kw_only=True, **{other: False}):
        note: Mapped[str] = mapped_column(default="")

    with pytest.raises(fr.exc.RestlyConfigurationError, match=f"set {option}=False"):

        class Post(Stamped, fr.IDMixin, Base):
            title: Mapped[str]


def test_a_mixin_on_the_base_raises():
    """Each model got the mixin's options through the base, and nothing
    raised."""

    class Stamped(MappedAsDataclass, kw_only=True, eq=False):
        note: Mapped[str] = mapped_column(default="")

    message = r"Base gets a dataclass repr.*pass repr=True on each model"
    with pytest.raises(fr.exc.RestlyConfigurationError, match=message):

        class Base(Stamped, fr.ModelDefaults, DeclarativeBase):
            pass


def test_a_repr_of_your_own_wins():
    class Base(fr.ModelDefaults, DeclarativeBase):
        def __repr__(self) -> str:
            return "from the base"

    class Post(fr.IDMixin, Base):
        title: Mapped[str]

    class Tag(fr.IDMixin, Base):
        name: Mapped[str]

        def __repr__(self) -> str:
            return "from the model"

    assert repr(Post(title="a")) == "from the base"
    assert repr(Tag(name="a")) == "from the model"


def test_dataclass_options_on_the_base_raise():
    with pytest.raises(fr.exc.RestlyConfigurationError, match="on each model"):

        class Base(fr.ModelDefaults, DeclarativeBase, eq=True):
            pass


def test_model_defaults_below_a_declarative_base_raises():
    class AppBase(DeclarativeBase):
        pass

    with pytest.raises(fr.exc.RestlyConfigurationError, match="on the base itself"):

        class RestlyModel(fr.ModelDefaults, AppBase):
            __abstract__ = True


def test_model_defaults_on_a_base_without_a_metaclass():
    class Base(fr.ModelDefaults, DeclarativeBaseNoMeta):
        pass

    class Reading(fr.IDMixin, Base):
        taken_at: Mapped[datetime]

    assert Reading.__table__.c.taken_at.type.timezone is True


def test_model_mixins_on_a_plain_base_make_the_model_a_dataclass():
    class Base(DeclarativeBase):
        pass

    class Widget(fr.TimestampsMixin, fr.IDMixin, Base):
        __tablename__ = "widget"
        name: Mapped[str]

    assert dataclasses.is_dataclass(Widget)
    assert Widget(name="w").name == "w"
    assert Widget(name="w") != Widget(name="w")
    # the mixins do not generate a repr that can load
    assert " object at " in repr(Widget(name="w"))


def test_model_mixins_on_a_dataclass_base_without_restly_defaults():
    class Base(MappedAsDataclass, DeclarativeBase):
        pass

    class Widget(fr.TimestampsMixin, fr.IDMixin, Base):
        __tablename__ = "widget"
        name: Mapped[str]

    assert Widget(name="w").name == "w"


def test_views_serve_a_model_on_its_own_base(client):
    class Base(fr.ModelDefaults, DeclarativeBase):
        pass

    class Gadget(fr.TimestampsMixin, fr.IDMixin, Base):
        name: Mapped[str]

    @fr.include_view(client.app)
    class GadgetView(fr.AsyncRestView):
        prefix = "/gadgets"
        model = Gadget

    asyncio.run(fr.db.async_create_all(Base))

    created = client.post("/gadgets/", json={"name": "Lamp"}).json()
    assert created["name"] == "Lamp"
    assert "created_at" in created
    assert client.get(f"/gadgets/{created['id']}").json()["name"] == "Lamp"
