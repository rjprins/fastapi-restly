"""Markers that stand for the view's own types in a route.

A route on a mixin or a base view cannot name the classes of each view that
uses it, because the decorator runs in the class body. A marker names the
role instead. When a view registers, Restly puts the view's class in place
of each marker, so each subclass gets its own classes.
"""

from typing import Annotated, Any, TypeAlias


class _ViewType:
    """The metadata of a marker: the role of the view's class it stands for."""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return f"fastapi_restly.views.{self.name}"


#: The view's id type: :attr:`~fastapi_restly.views.BaseRestView.id_type`,
#: else the type of the model's primary key. A path parameter with this type
#: gets the view's path converter, as the CRUD item routes do: ``{id:int}``
#: for ``int`` and ``{id:uuid}`` for ``UUID``.
ViewID: TypeAlias = Annotated[Any, _ViewType("ViewID")]

#: The view's response class,
#: :attr:`~fastapi_restly.views.BaseRestView.schema_response`.
ViewResponse: TypeAlias = Annotated[Any, _ViewType("ViewResponse")]

#: The view's list response: the envelope of the view's pagination, with
#: items of the view's response class.
ViewListResponse: TypeAlias = Annotated[Any, _ViewType("ViewListResponse")]

#: The view's create body,
#: :attr:`~fastapi_restly.views.BaseRestView.schema_create`.
ViewCreate: TypeAlias = Annotated[Any, _ViewType("ViewCreate")]

#: The view's update body,
#: :attr:`~fastapi_restly.views.BaseRestView.schema_update`.
ViewUpdate: TypeAlias = Annotated[Any, _ViewType("ViewUpdate")]
