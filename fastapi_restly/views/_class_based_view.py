"""Make the methods of a view class into FastAPI endpoints.

Each subclass gets its own copy of each endpoint. FastAPI creates the view
object for each request: the ``self`` parameter depends on the class, and
the annotated class attributes become dependencies of its ``__init__``. This
follows the class-based views of fastapi-utils.
"""

import inspect
from typing import (
    Annotated,
    Any,
    Callable,
    ClassVar,
    get_args,
    get_origin,
    get_type_hints,
)

import fastapi
from fastapi import BackgroundTasks, Request, Response, WebSocket
from fastapi.params import Body as _BodyMarker
from fastapi.params import Depends as _DependsMarker
from fastapi.params import Param as _ParamMarker

from ..exc import RestlyConfigurationError
from ._base import View, _copy_route


def _copy_all_parent_class_endpoints_into_this_subclass(view_cls: type[View]):
    """
    Override all methods with a @route decorator of the parent classes of view_cls
    with a new copy directly on view_cls . This allows us to change the
    annotations on these endpoints without affecting the parent endpoints.

    For example, FooView.get() delegates to AsyncRestView.get() if it is not
    overridden (this is called implicit delegation through method resolution). And if
    we add the annotation that FooView.get() returns FooSchema but do not make a copy
    then AsyncRestView.get() and all other subclasses will get the FooSchema
    annotation as well.
    """
    for name, endpoint in _get_all_parent_endpoints(view_cls).items():
        if name in view_cls.__dict__:
            # This endpoint is already overridden! fr.modify_route changes no
            # place: its route stands where the inherited route would stand, after
            # the routes that the class writes, such as GET /stats before GET /{id}.
            own = view_cls.__dict__[name]
            if getattr(own, "_from_modify_route", False):
                delattr(view_cls, name)
                setattr(view_cls, name, own)
            continue

        # The original endpoint might be shared between subclasses.
        # So make a copy and put that on the view_cls.
        setattr(view_cls, name, _copy_route(endpoint, view_cls, name))


def _init_all_endpoints(view_cls: type[View]):
    """
    Name every endpoint after its attribute and update the 'self' annotation.
    """
    for name, attr in view_cls.__dict__.items():
        if not hasattr(attr, "_api_route_args"):
            continue
        endpoint = attr
        # FastAPI makes the route name and the summary from this name.
        endpoint.__name__ = name
        _annotate_self(view_cls, endpoint)


def _get_all_parent_endpoints(view_cls: type[View]) -> dict[str, Callable]:
    """Map parent route endpoints by attribute name.

    Registered intermediate parents copy base endpoints into their ``__dict__``.
    Keying by most-derived attribute name prevents duplicate logical endpoints
    while still respecting overrides.
    """
    endpoints: dict[str, Callable] = {}
    seen: set[str] = set()
    for cls in view_cls.mro():
        if cls is view_cls:
            continue
        for name, value in cls.__dict__.items():
            if name in seen:
                # A more-derived class already defined this name; it shadows the
                # base regardless of whether that override is itself a route.
                continue
            seen.add(name)
            if hasattr(value, "_api_route_args"):
                endpoints[name] = value
    return endpoints


def _annotate_self(view_cls: type[View], endpoint: Callable) -> None:
    """
    Annotate the 'self' argument as 'self=Depends(view_cls)'. That way FastAPI instantiates the
    view_cls before calling the endpoint function and passes it as 'self'.
    Note that it sets endpoint.__signature__ which overrides any other inspection.

    Note: Copied (MIT license) and adjusted from: https://github.com/dmontagu/fastapi-utils/blob/master/fastapi_utils/cbv.py

    Fixes the endpoint signature to ensure FastAPI performs dependency injection properly.
    """
    sig = inspect.signature(endpoint)
    params: list[inspect.Parameter] = list(sig.parameters.values())
    self_param = params[0]
    new_self_param = self_param.replace(default=fastapi.Depends(view_cls))

    new_params = [new_self_param] + [
        param.replace(kind=inspect.Parameter.KEYWORD_ONLY) for param in params[1:]
    ]
    endpoint.__signature__ = sig.replace(parameters=new_params)  # type: ignore[attr-defined]


# Bare-typed annotations FastAPI special-cases for parameter injection
# (no ``Depends(...)`` marker required). Treated alongside ``Depends``-
# marked annotations as dependency class attributes; everything else is
# left as plain typing.
_FASTAPI_SPECIAL_INJECTABLE: tuple[type, ...] = (
    Request,
    Response,
    BackgroundTasks,
    WebSocket,
)

# Markers for the request inputs FastAPI reads into an endpoint parameter:
# ``Path``/``Query``/``Header``/``Cookie`` (``Param``) and ``Body``/``Form``/
# ``File`` (``Body``). FastAPI fills a class attribute only through ``Depends``
# or a special type, never through one of these, so the view is rejected.
_REQUEST_PARAMETER_MARKERS: tuple[type, ...] = (_ParamMarker, _BodyMarker)


def _init_class_based_view(view_cls: type[View]) -> None:
    """
    Note: Copied (MIT license) and adjusted from: https://github.com/dmontagu/fastapi-utils/blob/master/fastapi_utils/cbv.py

    Idempotently modifies the provided `cls`, performing the following modifications:
    * The `__init__` function is updated to set any class-annotated dependencies as instance attributes
    * The `__signature__` attribute is updated to indicate to FastAPI what arguments should be passed to the initializer
    """
    if getattr(view_cls, "__class_based_view", False):
        return  # Already initialized
    old_init: Callable[..., Any] = view_cls.__init__
    old_signature = inspect.signature(old_init)
    old_parameters = list(old_signature.parameters.values())[1:]  # drop `self`
    new_parameters = [
        x
        for x in old_parameters
        if x.kind
        not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    ]
    # Marker-based DI with MRO-aware shadowing: walk the MRO from the
    # base classes upward and pick, for each name, an annotation that
    # either carries a ``Depends(...)`` marker or names one of FastAPI's
    # bare-injectable special types (``Request`` / ``Response`` etc.).
    # A *plain* annotation on a more-derived class (e.g. a mixin
    # declaring ``session: AsyncSession`` for static-typing purposes)
    # does NOT shadow a marker-bearing annotation from a base — the
    # framework keeps the dependency over the most-derived hint.
    # Without this rule, any plain annotation a mixin adds would
    # silently break dependency injection.
    di_annotations: dict[str, Any] = {}
    request_markers: dict[str, Any] = {}
    for cls in reversed(view_cls.__mro__):
        try:
            cls_hints = get_type_hints(cls, include_extras=True)
        except Exception:
            continue
        for name, annotation in cls_hints.items():
            if get_origin(annotation) is ClassVar:
                continue
            metadata = getattr(annotation, "__metadata__", ())
            has_depends_marker = any(isinstance(m, _DependsMarker) for m in metadata)
            for marker in metadata:
                if isinstance(marker, _REQUEST_PARAMETER_MARKERS):
                    request_markers.setdefault(name, marker)
            underlying = (
                annotation
                if get_origin(annotation) is not Annotated
                else (get_args(annotation)[0] if get_args(annotation) else annotation)
            )
            is_special_type = inspect.isclass(underlying) and issubclass(
                underlying, _FASTAPI_SPECIAL_INJECTABLE
            )
            if has_depends_marker or is_special_type:
                # Marker-bearing annotation wins, regardless of MRO position.
                di_annotations[name] = annotation
            # Plain annotations are silently ignored — they neither set
            # nor clear an entry in di_annotations.

    # FastAPI never fills an attribute with a request-parameter marker: it
    # would keep its class default, with no error and no OpenAPI entry. A
    # Depends marker on the same name makes it a dependency, as above.
    for name, marker in request_markers.items():
        if name not in di_annotations:
            raise RestlyConfigurationError(
                f"{view_cls.__name__}.{name} has a {type(marker).__name__}() "
                "marker, but a view class attribute is filled only through "
                "Depends(...) or Security(...), so FastAPI would never set it. "
                "Declare the parameter on the endpoint method that reads it, "
                f"or annotate {name} with Annotated[..., Depends(...)] and read "
                "the parameter in that dependency."
            )

    dependency_names: list[str] = []
    for name, annotation in di_annotations.items():
        dependency_names.append(name)
        default_value = getattr(view_cls, name, inspect.Parameter.empty)
        new_parameters.append(
            inspect.Parameter(
                name=name,
                kind=inspect.Parameter.KEYWORD_ONLY,
                default=default_value,
                annotation=annotation,
            )
        )
    new_signature = old_signature.replace(parameters=new_parameters)

    def new_init(self: Any, *args: Any, **kwargs: Any) -> None:
        for dep_name in dependency_names:
            dep_value = kwargs.pop(dep_name)
            setattr(self, dep_name, dep_value)
        old_init(self, *args, **kwargs)

    setattr(view_cls, "__signature__", new_signature)
    setattr(view_cls, "__init__", new_init)
    setattr(view_cls, "__class_based_view", True)
