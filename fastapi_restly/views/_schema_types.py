"""Types that depend on the view: :class:`SchemaType` and the markers.

A route on a mixin or a base view cannot name the classes of each view that
uses it, because the decorator runs in the class body. A marker names the
role instead, and a :class:`SchemaType` is a function of the view. When a view
registers, Restly puts the view's type in place of each one, so each subclass
gets its own types. A setting of the view can hold a :class:`SchemaType` too.
"""

import inspect
import types
from contextvars import ContextVar
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

from ..exc import RestlyConfigurationError
from ..schemas._base import _model_id_type


class SchemaType:
    """A type that depends on the view: a function that gets the view class
    and returns the type.

    A setting that takes a schema class also takes a ``SchemaType``. Here each
    view gets a create body of its own::

        class ApiView(fr.AsyncRestView):
            create_schema = SchemaType(lambda view: with_tenant(view.schema))

    A setting can also use it as a decorator::

        class ApiView(fr.AsyncRestView):
            @SchemaType
            def create_schema(view):
                return with_tenant(view.schema)

    In a route, it goes in the metadata of an annotation::

        Single = Annotated[Any, SchemaType(lambda view: Data[view.response_schema])]


        class ApiView(fr.AsyncRestView):
            @fr.get("/{id}")
            async def get_one_endpoint(self, id: IDType) -> Single: ...

    Restly calls the function once for each view class, when something first
    reads the type. It can read any setting of the view, in any order. A setting
    must get a Pydantic class. A route can get any type that FastAPI accepts.

    :param fn: The function. It gets the view class and returns the type.
    """

    __slots__ = ("fn", "name")

    fn: Callable[[Any], Any]
    #: The name of a marker of this module.
    name: str | None

    # Returns Any for the type checkers: the settings say they hold a class, and
    # reading one gives the class.
    def __new__(cls, fn: Callable[[Any], Any]) -> Any:
        self = super().__new__(cls)
        self.fn = fn
        self.name = None
        return self

    def __repr__(self) -> str:
        if self.name is not None:
            return f"fastapi_restly.views.{self.name}"
        return f"SchemaType({self.fn!r})"

    def __get__(self, instance: Any, owner: type | None = None) -> Any:
        """The type for the view that reads the setting."""
        return _filled(self, owner if owner is not None else type(instance))


#: The functions being called now, for the check of a function that reads itself.
_BUILDING: ContextVar[frozenset[tuple[Callable[[Any], Any], type]]] = ContextVar(
    "_BUILDING", default=frozenset()
)


def _filled(schema_type: SchemaType, view_cls: type) -> Any:
    """What ``schema_type`` gives on ``view_cls``, from one call for each view.

    So a class that the function builds is one class in OpenAPI. The results are
    on the view class, as a subclass has its own.
    """
    fn = schema_type.fn
    results = view_cls.__dict__.get("_fr_schema_types")
    if results is None:
        results = {}
        cast(Any, view_cls)._fr_schema_types = results
    if fn in results:
        return results[fn]
    building = _BUILDING.get()
    if (fn, view_cls) in building:
        raise RestlyConfigurationError(
            f"The SchemaType function {getattr(fn, '__qualname__', fn)} of "
            f"{view_cls.__name__} reads its own result, directly or through "
            "another setting."
        )
    token = _BUILDING.set(building | {(fn, view_cls)})
    try:
        result = fn(view_cls)
    finally:
        _BUILDING.reset(token)
    # Two threads can read at the same time. Both then use the first result.
    return results.setdefault(fn, result)


def _marker(name: str, fn: Callable[[Any], Any]) -> SchemaType:
    marker = SchemaType(fn)
    marker.name = name
    return marker


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


#: The view's id type: :attr:`~fastapi_restly.views.BaseRestView.id_type`,
#: else the type of the model's primary key. A path parameter with this type
#: gets the view's path converter, as the CRUD item routes do: ``{id:int}``
#: for ``int`` and ``{id:uuid}`` for ``UUID``.
IDType: TypeAlias = Annotated[Any, _marker("IDType", _view_id_type)]

#: The view's response class,
#: :attr:`~fastapi_restly.views.BaseRestView.response_schema`.
ResponseSchema: TypeAlias = Annotated[
    Any, _marker("ResponseSchema", lambda view: view.response_schema)
]

#: The view's list response,
#: :attr:`~fastapi_restly.views.BaseRestView.list_response_schema`.
ListResponseSchema: TypeAlias = Annotated[
    Any, _marker("ListResponseSchema", lambda view: view.list_response_schema)
]

#: The view's create body,
#: :attr:`~fastapi_restly.views.BaseRestView.create_schema`.
CreateSchema: TypeAlias = Annotated[
    Any, _marker("CreateSchema", lambda view: view.create_schema)
]

#: The view's update body,
#: :attr:`~fastapi_restly.views.BaseRestView.update_schema`.
UpdateSchema: TypeAlias = Annotated[
    Any, _marker("UpdateSchema", lambda view: view.update_schema)
]


def _fill_schema_types(func: Callable, view_cls: type, list_params: Any) -> None:
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
            annotation = _with_schema_types(written[param.name], func, view_cls)
        else:
            annotation = param.annotation
        changed = changed or annotation is not param.annotation
        params.append(param.replace(annotation=annotation))
    returns = sig.return_annotation
    if "return" in written:
        returns = _with_schema_types(written["return"], func, view_cls)
    if changed or returns is not sig.return_annotation:
        func.__signature__ = sig.replace(  # type: ignore[attr-defined]
            parameters=params, return_annotation=returns
        )


def _with_schema_types(annotation: Any, func: Callable, view_cls: type) -> Any:
    """``annotation`` with the view's classes in place of its markers.

    It is ``annotation`` itself when it holds no marker. So an annotation
    that is a string, under ``from __future__ import annotations``, stays a
    string for FastAPI to resolve.
    """
    evaluated = _evaluated(annotation, func)
    filled = _replace_schema_types(evaluated, view_cls)
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


def _pydantic_generic_args(annotation: Any) -> tuple[Any, ...]:
    """The arguments of a filled Pydantic generic class: ``(ResponseSchema,)`` for
    ``Data[ResponseSchema]``. It is a class, so ``typing.get_args`` finds nothing."""
    metadata = getattr(annotation, "__pydantic_generic_metadata__", None)
    return metadata["args"] if metadata is not None and metadata["origin"] else ()


def _replace_schema_types(annotation: Any, view_cls: type) -> Any:
    """Replace each marker in ``annotation``, also inside a type such as
    ``list[ResponseSchema]`` or ``Data[ResponseSchema]``. Returns ``annotation``
    itself when it holds no marker."""
    origin = get_origin(annotation)
    if origin is Annotated:
        inner, *metadata = get_args(annotation)
        marker = next((m for m in metadata if isinstance(m, SchemaType)), None)
        if marker is not None:
            # Annotated[IDType, Path(ge=1)] keeps the Path(ge=1)
            rest = [m for m in metadata if not isinstance(m, SchemaType)]
            filled = _filled(marker, view_cls)
            return Annotated[(filled, *rest)] if rest else filled
        filled_inner = _replace_schema_types(inner, view_cls)
        if filled_inner is inner:
            return annotation
        return Annotated[(filled_inner, *metadata)]
    generic = _pydantic_generic_args(annotation)
    if generic:
        filled_args = tuple(_replace_schema_types(arg, view_cls) for arg in generic)
        if all(new is old for new, old in zip(filled_args, generic)):
            return annotation
        return annotation.__pydantic_generic_metadata__["origin"][filled_args]
    args = get_args(annotation)
    if not args or origin is Literal:
        return annotation
    filled_args = tuple(_replace_schema_types(arg, view_cls) for arg in args)
    if all(new is old for new, old in zip(filled_args, args)):
        return annotation
    if origin in (Union, types.UnionType):
        return Union[filled_args]
    if isinstance(annotation, types.GenericAlias):
        return types.GenericAlias(origin, filled_args)
    return annotation.copy_with(filled_args)
