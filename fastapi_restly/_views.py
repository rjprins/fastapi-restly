"""
This module provides a framework for class-based views on SQLAlchemy models.

View class:
This class is used to create a collection of endpoints that share an
APIRouter (created when calling `include_view()`) and dependencies
as class attributes. It uses the same mechanics as the class based
view decorator from fastapi-utils.
(https://fastapi-utils.davidmontague.xyz/user-guide/class-based-views/)

AsyncAlchemyView:
Provides default reading and writing functions on the database using
SQLAlchemy models.
"""

import functools
import inspect
from enum import Enum
from typing import (
    Annotated,
    Any,
    Callable,
    ClassVar,
    Sequence,
    TypeVar,
    get_origin,
    get_type_hints,
    overload,
)

import fastapi

from ._query_modifiers_config import create_query_param_schema
from ._schema_generator import auto_generate_schema_for_view
from ._schemas import (
    BaseSchema,
    create_model_with_optional_fields,
    create_model_without_read_only_fields,
)
from ._sqlbase import Base


class View:
    """
    A View that combined with `include_view()` will produce class-based views.
    Almost exactly like the @cbv decorator from fastapi-utils:
    https://fastapi-utils.davidmontague.xyz/user-guide/class-based-views/
    """

    prefix: ClassVar[str]
    tags: ClassVar[list[str] | None] = None  # View class name will be added by default
    dependencies: ClassVar[list[Any] | None] = None
    responses: ClassVar[dict[int, Any]] = {404: {"description": "Not found"}}

    @classmethod
    def before_include_view(cls):
        pass

    @classmethod
    def add_to_router(cls, parent_router: fastapi.APIRouter | fastapi.FastAPI) -> None:
        _init_view_cls_and_add_to_router(cls, parent_router)


V = TypeVar("V", bound=type[View])


@overload
def include_view(
    parent_router: fastapi.APIRouter | fastapi.FastAPI, view_cls: V
) -> V: ...
@overload
def include_view(
    parent_router: fastapi.APIRouter | fastapi.FastAPI,
) -> Callable[[V], V]: ...


def include_view(
    parent_router: fastapi.APIRouter | fastapi.FastAPI, view_cls: V | None = None
) -> V | Callable[[V], V]:
    """
    Add the routes of a View class to a FastAPI app or APIRouter.
    This function should be used for every View class.

    Can be used as decorator:
    @include_view(app)
    class MyView(AsyncAlchemyView):
        ...

    Or as a function:
    include_view(app, MyView)
    """
    if view_cls is not None:
        _init_view_cls_and_add_to_router(view_cls, parent_router)
        return view_cls

    def class_decorator(view_cls: V) -> V:
        _init_view_cls_and_add_to_router(view_cls, parent_router)
        return view_cls

    return class_decorator


def route(path: str, **api_route_kwargs: Any) -> Callable[..., Any]:
    """Decorator to mark a View method as an endpoint.
    The path and api_route_kwargs are passed into APIRouter.add_api_route(), see for example:
    https://fastapi.tiangolo.com/reference/apirouter/#fastapi.APIRouter.get

    Endpoints methods are later added as routes to the FastAPI app using `include_view()`
    """

    def store_args_decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        # Create a new attribute: '_api_route_args'
        func._api_route_args = (path, api_route_kwargs)  # type: ignore[attr-defined]
        return func

    return store_args_decorator


def get(path: str, **api_route_kwargs: Any) -> Callable[..., Any]:
    """Decorator to mark a View method as a GET endpoint.

    Equivalent to: @route(path, methods=["GET"], status_code=200, **api_route_kwargs)
    """
    return route(path, **api_route_kwargs)


def post(path: str, **api_route_kwargs: Any) -> Callable[..., Any]:
    """Decorator to mark a View method as a POST endpoint.

    Equivalent to: @route(path, methods=["POST"], status_code=201, **api_route_kwargs)
    """
    api_route_kwargs.setdefault("methods", ["POST"])
    api_route_kwargs.setdefault("status_code", 201)
    return route(path, **api_route_kwargs)


def put(path: str, **api_route_kwargs: Any) -> Callable[..., Any]:
    """Decorator to mark a View method as a PUT endpoint.

    Equivalent to: @route(path, methods=["PUT"], status_code=200, **api_route_kwargs)
    """
    api_route_kwargs.setdefault("methods", ["PUT"])
    return route(path, **api_route_kwargs)


def delete(path: str, **api_route_kwargs: Any) -> Callable[..., Any]:
    """Decorator to mark a View method as a DELETE endpoint.

    Equivalent to: @route(path, methods=["DELETE"], status_code=204, **api_route_kwargs)
    """
    api_route_kwargs.setdefault("methods", ["DELETE"])
    api_route_kwargs.setdefault("status_code", 204)
    return route(path, **api_route_kwargs)


class BaseAlchemyView(View):
    """
    Base class for AlchemyView implementations.

    This class contains the common functionality shared between AsyncAlchemyView
    and AlchemyView, including schema definitions, model configuration, and
    common CRUD operation logic.
    """

    schema: ClassVar[type[BaseSchema]]
    # If 'creation_schema' is not defined it will be created from 'schema'
    # using `create_model_without_read_only_fields()`.
    creation_schema: ClassVar[type[BaseSchema]]
    update_schema: ClassVar[type[BaseSchema]]
    model: ClassVar[type[Base]]
    exclude_routes: ClassVar[list[str]] = []

    request: fastapi.Request

    @classmethod
    def before_include_view(cls):
        """
        Apply type annotations needed for FastAPI, before creating an APIRouter from
        this view and registering it.

        This function can be overridden to further tweak the endpoints before they
        are added to FastAPI.
        """
        # Auto-generate schema if none is provided
        if not hasattr(cls, "schema"):
            if not hasattr(cls, "model"):
                raise Exception(
                    f"'{cls.__name__}.model' must be specified to auto-generate schema"
                )
            cls.schema = auto_generate_schema_for_view(cls, cls.model)

        if not hasattr(cls, "index_param_schema"):
            cls.index_param_schema = create_query_param_schema(cls.schema)
        if not hasattr(cls, "creation_schema"):
            cls.creation_schema = create_model_without_read_only_fields(cls.schema)
        if not hasattr(cls, "update_schema"):
            cls.update_schema = create_model_with_optional_fields(cls.schema)

        response_schema = cls.schema

        # Only annotate if the methods exist (they will be overridden in subclasses)
        if hasattr(cls, "index"):
            _annotate(
                cls.index,
                return_annotation=Sequence[response_schema],
                query_params=Annotated[cls.index_param_schema, fastapi.Query()],
            )
        if hasattr(cls, "get"):
            _annotate(cls.get, return_annotation=response_schema)
        if hasattr(cls, "post"):
            _annotate(
                cls.post,
                return_annotation=response_schema,
                schema_obj=cls.creation_schema,
            )
        if hasattr(cls, "put"):
            _annotate(
                cls.put, return_annotation=response_schema, schema_obj=cls.update_schema
            )
        _exclude_routes(cls)


async def _excluded_route(self, *args, **kwargs):
    raise NotImplementedError(
        "This route has been excluded from {self.__class__.__name__}"
    )


def _exclude_routes(cls: type[View]):
    for method_name in cls.exclude_routes:
        # @route decorator adds `_api_route_args` to a method to create the route later.
        # By removing it from the method, the method will no longer be added as a route.
        try:
            view_func = getattr(cls, method_name)
            del view_func._api_route_args
        except AttributeError:
            raise AttributeError(f"{method_name!r} is not a route on {cls}")


def _init_view_cls_and_add_to_router(
    view_cls: type[View], parent_router: fastapi.APIRouter | fastapi.FastAPI
):
    """
    To make View classes work in FastAPI some hacks are needed. Those hacks are
    applied here.

    FastAPI does a lot with annotations. For example, accepted or returned JSON is
    often described with Pydantic classes like this:

        def my_endpoint(foo: FooSchema) -> FooSchema:

    Most of the hacks here are to set the correct annotations on (inherited) class
    methods.
    """
    _copy_all_parent_class_endpoints_into_this_subclass(view_cls)
    _init_all_endpoints(view_cls)
    view_cls.before_include_view()
    _init_class_based_view(view_cls)
    api_router = _init_api_router(view_cls)
    parent_router.include_router(api_router)


def _copy_all_parent_class_endpoints_into_this_subclass(view_cls: type[View]):
    """
    Override all methods with a @route decorator of the parent classes of view_cls
    with a new copy directly on view_cls . This allows us to change the
    annotations on these endpoints without affecting the parent endpoints.

    For example, FooView.get() delegates to AsyncAlchemyView.get() if it is not
    overridden (this is called implicit delegation through method resolution). And if
    we add the annotation that FooView.get() returns FooSchema but do not make a copy
    then AsyncAlchemyView.get() and all other subclasses will get the FooSchema
    annotation as well.
    """
    for endpoint in _get_all_parent_endpoints(view_cls):
        # Use `cls.__dict__` to check what attributes are directly on the class.
        # This way we side-step the method resolution.
        if endpoint.__name__ in view_cls.__dict__:
            # This endpoint is already overridden!
            continue

        # The original endpoint might be shared between subclasses.
        # So make a copy and put that on the view_cls.
        endpoint_wrapper = _make_copy(endpoint, view_cls)
        # Set explicit __qualname__ for debugging purposes.
        endpoint_wrapper.__qualname__ = (
            f"{view_cls.__name__}_{endpoint.__qualname__}_wrapper"
        )
        setattr(view_cls, endpoint.__name__, endpoint_wrapper)


def _make_copy(endpoint: Callable, view_cls: type[View]) -> Callable:
    """
    Wrap the endpoint in a new function as kind of copy.

    Fun fact: You cannot do this inside a for loop, because the closure of 'endpoint'
    inside the wrapper works on the variable, not on the value. And for-loops in Python
    do not have their own variable scope.

    https://eev.ee/blog/2011/04/24/gotcha-python-scoping-closures/
    """
    if inspect.iscoroutinefunction(endpoint):

        @functools.wraps(endpoint)
        async def endpoint_wrapper(self, *args, **kwargs):
            return await endpoint(self, *args, **kwargs)

    else:

        @functools.wraps(endpoint)
        def endpoint_wrapper(self, *args, **kwargs):
            return endpoint(self, *args, **kwargs)

    endpoint_wrapper.__annotations__ = endpoint.__annotations__.copy()
    return endpoint_wrapper


def _init_all_endpoints(view_cls: type[View]):
    """
    Ensure every endpoint has a unique name and update the 'self' annotation.
    """
    for attr in view_cls.__dict__.values():
        if not hasattr(attr, "_api_route_args"):
            continue
        endpoint = attr
        # Give every endpoint a unique name
        # This will give the FooView.post() endpoint the name "fooview_post"
        endpoint.__name__ = view_cls.__name__.lower() + "_" + endpoint.__name__
        _annotate_self(view_cls, endpoint)


def _annotate(func: Callable, return_annotation: Any = None, **param_annotations):
    """
    Annotate a function by setting func.__signature__ explicitly.
    """
    sig = inspect.signature(func)
    new_params = []
    for param in sig.parameters.values():
        if param.name in param_annotations:
            annotation = param_annotations[param.name]
            new_param = param.replace(annotation=annotation)
            new_params.append(new_param)
        else:
            new_params.append(param)
    func.__signature__ = sig.replace(  # type: ignore[attr-defined]
        parameters=new_params, return_annotation=return_annotation
    )


def _get_all_parent_endpoints(view_cls: type[View]) -> list[Callable]:
    endpoints = []
    for cls in view_cls.mro():
        if cls is view_cls:
            continue
        for name, value in cls.__dict__.items():
            if hasattr(value, "_api_route_args"):
                endpoints.append(value)
    return endpoints


def _init_api_router(view_cls: type[View]) -> fastapi.APIRouter:
    tags: list[str | Enum] = [view_cls.__name__]
    if view_cls.tags:
        tags += view_cls.tags

    api_router = fastapi.APIRouter(
        prefix=view_cls.prefix, tags=tags, responses=view_cls.responses
    )

    # Find all endpoint functions in this class and add them to the router
    for attr in view_cls.__dict__.values():
        if not hasattr(attr, "_api_route_args"):
            continue
        endpoint = attr
        path, route_kwargs = endpoint._api_route_args
        api_router.add_api_route(path, endpoint, **route_kwargs)

    return api_router


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
    dependency_names: list[str] = []
    for name, annotation in get_type_hints(view_cls, include_extras=True).items():
        if get_origin(annotation) is ClassVar:
            continue
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
