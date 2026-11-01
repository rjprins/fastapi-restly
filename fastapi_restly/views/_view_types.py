"""Markers that stand for the view's own types in a route.

A route on a mixin or a base view cannot name the classes of each view that
uses it, because the decorator runs in the class body. A marker names the
role instead. When a view registers, Restly puts the view's class in place
of each marker, so each subclass gets its own classes.
"""

import inspect
import types
from collections.abc import Mapping
from typing import (
    Annotated,
    Any,
    Callable,
    Literal,
    TypeAlias,
    Union,
    cast,
    get_args,
    get_origin,
)

from sqlalchemy import inspect as sa_inspect

from ..schemas import derive_schema_list_response
from ..schemas._base import _model_id_type


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


def _view_id_type(view_cls: type) -> Any:
    """The ``{id}`` type: ``id_type`` when set, else the model's primary key's.

    Resolved per registration and never stored on the class, so a subclass
    with another model derives its own.
    """
    explicit = getattr(view_cls, "id_type", None)
    if explicit is not None:
        return explicit
    model = getattr(view_cls, "model", None)
    # a composite key has no scalar type; it keeps the int default
    if model is None or len(sa_inspect(model).primary_key) != 1:
        return int
    return _model_id_type(model) or int


def _view_types(view_cls: type) -> dict[str, Any]:
    """The class that each marker of ``fastapi_restly.views`` stands for."""
    view = cast(Any, view_cls)
    return {
        "ViewID": _view_id_type(view_cls),
        "ViewResponse": view.schema_response,
        "ViewListResponse": derive_schema_list_response(
            view.schema_response, pagination=view.pagination
        ),
        "ViewCreate": view.schema_create,
        "ViewUpdate": view.schema_update,
    }


def _fill_view_types(
    func: Callable, view_types: Mapping[str, Any], list_params: Any
) -> None:
    """Put the view's classes in place of the markers in ``func.__signature__``.

    A parameter named ``list_params`` always takes the view's list params,
    whatever its annotation says: the unknown-key guard checks the keys
    against them.
    """
    # Read what the author wrote, not the signature: functools.update_wrapper
    # copies a registered parent's filled-in signature onto a subclass's copy.
    written = inspect.get_annotations(func)
    sig = inspect.signature(func)
    changed = False
    params = []
    for param in sig.parameters.values():
        if param.name == "list_params":
            annotation = list_params
        elif param.name in written:
            annotation = _with_view_types(written[param.name], func, view_types)
        else:
            annotation = param.annotation
        changed = changed or annotation is not param.annotation
        params.append(param.replace(annotation=annotation))
    returns = sig.return_annotation
    if "return" in written:
        returns = _with_view_types(written["return"], func, view_types)
    if changed or returns is not sig.return_annotation:
        func.__signature__ = sig.replace(  # type: ignore[attr-defined]
            parameters=params, return_annotation=returns
        )


def _with_view_types(
    annotation: Any, func: Callable, view_types: Mapping[str, Any]
) -> Any:
    """``annotation`` with the view's classes in place of its markers.

    It is ``annotation`` itself when it holds no marker. So an annotation
    that is a string, under ``from __future__ import annotations``, stays a
    string for FastAPI to resolve.
    """
    evaluated = _evaluated(annotation, func)
    filled = _replace_view_types(evaluated, view_types)
    return annotation if filled is evaluated else filled


def _evaluated(annotation: Any, func: Callable) -> Any:
    """A string annotation, evaluated in the module of the route's author.

    A string that does not evaluate there, such as one that names a class of
    a function body, stays a string.
    """
    if not isinstance(annotation, str):
        return annotation
    try:
        return eval(annotation, inspect.unwrap(func).__globals__)  # noqa: S307
    except Exception:
        return annotation


def _replace_view_types(annotation: Any, view_types: Mapping[str, Any]) -> Any:
    """Replace each marker in ``annotation``, also inside a type such as
    ``list[ViewResponse]``. Returns ``annotation`` itself when it holds no
    marker."""
    origin = get_origin(annotation)
    if origin is Annotated:
        inner, *metadata = get_args(annotation)
        marker = next((m for m in metadata if isinstance(m, _ViewType)), None)
        if marker is not None:
            # Annotated[ViewID, Path(ge=1)] keeps the Path(ge=1)
            rest = [m for m in metadata if not isinstance(m, _ViewType)]
            filled = view_types[marker.name]
            return Annotated[(filled, *rest)] if rest else filled
        filled_inner = _replace_view_types(inner, view_types)
        if filled_inner is inner:
            return annotation
        return Annotated[(filled_inner, *metadata)]
    args = get_args(annotation)
    if not args or origin is Literal:
        return annotation
    filled_args = tuple(_replace_view_types(arg, view_types) for arg in args)
    if all(new is old for new, old in zip(filled_args, args)):
        return annotation
    if origin in (Union, types.UnionType):
        return Union[filled_args]
    if isinstance(annotation, types.GenericAlias):
        return types.GenericAlias(origin, filled_args)
    return annotation.copy_with(filled_args)
