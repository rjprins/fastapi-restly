"""
Async parity for view inheritance / handle_* hook chaining.

The Python super() mechanism is identical for sync and async methods, but the
framework copies endpoint methods onto subclasses at @include_view time and
rewrites their signatures. That registration-time class mutation is the part
that could plausibly break MRO traversal of awaitable handlers — so the tests
here go through real registered FastAPI routes (not direct method calls) and
use a three-level MRO via a mixin so the chain has to traverse more than the
trivial subclass→base step.
"""

import pytest
from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr

from .conftest import create_tables


@pytest.fixture
def call_log() -> list[str]:
    return []


def _model_and_schema():
    class Item(fr.IDBase):
        name: Mapped[str]

    class ItemSchema(fr.IDSchema):
        name: str

    return Item, ItemSchema


@pytest.mark.parametrize(
    "hook_name, exercise",
    [
        (
            "handle_create",
            lambda c: c.post("/items/", json={"name": "x"}, assert_status_code=201),
        ),
        (
            "handle_retrieve",
            lambda c: (
                c.post("/items/", json={"name": "x"}, assert_status_code=201),
                c.get("/items/1"),
            )[-1],
        ),
        ("handle_listing", lambda c: c.get("/items/")),
        (
            "handle_update",
            lambda c: (
                c.post("/items/", json={"name": "x"}, assert_status_code=201),
                c.patch("/items/1", json={"name": "y"}),
            )[-1],
        ),
        (
            "handle_destroy",
            lambda c: (
                c.post("/items/", json={"name": "x"}, assert_status_code=201),
                c.delete("/items/1", assert_status_code=204),
            )[-1],
        ),
    ],
)
def test_async_super_chain_three_level_mro(
    client, call_log: list[str], hook_name: str, exercise
) -> None:
    """
    Three-level chain (Child → Mixin → Base) for an AsyncRestView.

    Verifies cooperative ``await super().<handle_*>(...)`` traverses the full
    MRO under the framework's endpoint-rewriting registration path, for every
    handle_* hook.
    """
    Item, ItemSchema = _model_and_schema()

    class _ChainBase(fr.AsyncRestView):
        model = Item
        schema = ItemSchema

    async def _base_hook(self, *args, **kwargs):
        call_log.append("base_pre")
        result = await getattr(super(_ChainBase, self), hook_name)(*args, **kwargs)
        call_log.append("base_post")
        return result

    async def _mixin_hook(self, *args, **kwargs):
        call_log.append("mixin_pre")
        result = await getattr(super(_ChainMixin, self), hook_name)(*args, **kwargs)
        call_log.append("mixin_post")
        return result

    async def _child_hook(self, *args, **kwargs):
        call_log.append("child_pre")
        result = await getattr(super(ItemView, self), hook_name)(*args, **kwargs)
        call_log.append("child_post")
        return result

    # Build the base override directly on _ChainBase (one level above the
    # framework's RestView). super() from the mixin then walks into the
    # framework's default implementation via Python's MRO.
    setattr(_ChainBase, hook_name, _base_hook)

    class _ChainMixin(_ChainBase):
        pass

    setattr(_ChainMixin, hook_name, _mixin_hook)

    @fr.include_view(client.app)
    class ItemView(_ChainMixin):
        prefix = "/items"

    setattr(ItemView, hook_name, _child_hook)

    create_tables()

    response = exercise(client)
    assert response.status_code in (200, 201, 204)

    assert call_log == [
        "child_pre",
        "mixin_pre",
        "base_pre",
        "base_post",
        "mixin_post",
        "child_post",
    ]


def test_async_super_chain_mutates_object_through_chain(client) -> None:
    """
    A base override that mutates the new object before calling super() must
    persist the mutation through the await chain. This pins the contract that
    awaitable hooks are not bypassing or short-circuiting one another.
    """

    class Stamped(fr.IDBase):
        name: Mapped[str]
        suffix: Mapped[str] = mapped_column(default="")

    class StampedSchema(fr.IDSchema):
        name: str
        suffix: str = ""

    class StampingBase(fr.AsyncRestView):
        model = Stamped
        schema = StampedSchema

        async def handle_create(self, schema_obj):
            object.__setattr__(schema_obj, "suffix", "from_base")
            return await super().handle_create(schema_obj)

    @fr.include_view(client.app)
    class StampingView(StampingBase):
        prefix = "/stamped"

        async def handle_create(self, schema_obj):
            # Child runs first, awaits super(), so the base override below
            # overwrites ``suffix`` after this method's mutation. The
            # response value proves the base ran after the child awaited.
            object.__setattr__(schema_obj, "suffix", "from_child")
            return await super().handle_create(schema_obj)

    create_tables()

    response = client.post("/stamped/", json={"name": "thing"})
    assert response.json()["suffix"] == "from_base"
