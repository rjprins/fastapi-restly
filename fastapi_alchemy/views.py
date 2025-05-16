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
    get_origin,
    get_type_hints,
)

import fastapi
import pydantic
import sqlalchemy

from ._session import AsyncDBDependency, async_generate_session
from .query_modifiers import apply_query_modifiers, create_query_param_schema
from .schemas import (
    NOT_SET,
    BaseSchema,
    async_resolve_ids_to_sqlalchemy_objects,
    create_model_with_optional_fields,
    create_model_without_read_only_fields,
)
from .sqlbase import SQLBase


class View:
    """
    A View that combined with `include_view()` will produce class-based views.
    Almost exactly like the @cbv decorator from fastapi-utils:
    https://fastapi-utils.davidmontague.xyz/user-guide/class-based-views/
    """

    prefix: ClassVar[str]
    tags: ClassVar[list[str] | None] = None  # View class name will be added by default
    dependencies: ClassVar[list | None] = None
    responses: ClassVar[dict] = {404: {"description": "Not found"}}

    @classmethod
    def before_include_view(cls):
        pass

    @classmethod
    def add_to_router(cls, parent_router: fastapi.APIRouter | fastapi.FastAPI) -> None:
        _init_view_cls_and_add_to_router(cls, parent_router)


def include_view(
    parent_router: fastapi.APIRouter | fastapi.FastAPI,
    view_cls: type[View] | None = None,
) -> Callable:
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
    if view_cls:
        _init_view_cls_and_add_to_router(view_cls, parent_router)
        return view_cls

    def class_decorator(view_cls: type[View]) -> type[View]:
        _init_view_cls_and_add_to_router(view_cls, parent_router)
        return view_cls

    return class_decorator


def route(path: str, **api_route_kwargs) -> Callable:
    """Decorator to mark a View method as an endpoint.
    The path and api_route_kwargs are passed into APIRouter.add_api_route(), see for example:
    https://fastapi.tiangolo.com/reference/apirouter/#fastapi.APIRouter.get

    Endpoints methods are later added as routes to the FastAPI app using `include_view()`
    """

    def store_args_decorator(func: Callable) -> Callable:
        # Create a new attribute: '_api_route_args'
        func._api_route_args = (path, api_route_kwargs)  # type: ignore[attr-defined]
        return func

    return store_args_decorator


class AsyncAlchemyView(View):
    """
    AsyncAlchemyView creates a CRUD/REST interface for database objects.
    Basic usage:

    class FooView:
        prefix = "/foo"
        schema = FooSchema
        model = Foo

    Where `Foo` is a SQLAlchemy model and `FooSchema` a Pydantic model.
    """

    schema: ClassVar[type[BaseSchema]]
    # If 'creation_schema' is not defined it will be created from 'schema'
    # using `create_model_without_read_only_fields()`.
    creation_schema: ClassVar[type[BaseSchema]]
    update_schema: ClassVar[type[BaseSchema]]
    model: ClassVar[type[SQLBase]]
    exclude_routes: ClassVar[list[str]] = []

    request: fastapi.Request
    db: AsyncDBDependency = fastapi.Depends(async_generate_session)

    @classmethod
    def before_include_view(cls):
        """
        Apply type annotations needed for FastAPI, before creating an APIRouter from
        this view and registering it.

        This function can be overridden to further tweak the endpoints before they
        are added to FastAPI.
        """
        if not hasattr(cls, "schema"):
            raise Exception(f"'{cls.__name__}.schema' must be specified")
        if not hasattr(cls, "index_param_schema"):
            cls.index_param_schema = create_query_param_schema(cls.schema)
        if not hasattr(cls, "creation_schema"):
            cls.creation_schema = create_model_without_read_only_fields(cls.schema)
        if not hasattr(cls, "update_schema"):
            cls.update_schema = create_model_with_optional_fields(cls.schema)

        _annotate(
            cls.index,
            return_annotation=Sequence[cls.schema],
            query_params=Annotated[cls.index_param_schema, fastapi.Query()],
        )
        _annotate(cls.get, return_annotation=cls.schema)
        _annotate(
            cls.post, return_annotation=cls.schema, schema_obj=cls.creation_schema
        )
        _annotate(cls.put, return_annotation=cls.schema, schema_obj=cls.update_schema)
        _exclude_routes(cls)

    @route("/")
    async def index(self, query_params):
        return await self.process_index(query_params)

    async def process_index(
        self, query_params: pydantic.BaseModel, query: sqlalchemy.Select | None = None
    ) -> Sequence[Any]:
        """
        Handle a GET request on "/". This should return a list of objects.
        Accepts a query argument that can be used for narrowing down the selection.
        Feel free to override this method, e.g.:

            async def process_index(self, query=None):
                query = make_my_query()
                objs = await super.process_index(query)
                return add_my_info(objs)
        """
        if query is None:
            query = sqlalchemy.select(self.model)
        query = apply_query_modifiers(
            self.request.query_params, query, self.model, self.schema
        )
        scalar_result = await self.db.scalars(query)
        return scalar_result.all()

    @route("/{id}")
    async def get(self, id: int):
        return await self.process_get(id)

    async def process_get(self, id: int) -> Any:
        """
        Handle a GET request on "/{id}". This should return a single object.
        Return a 404 if not found.
        Feel free to override this method.
        """
        obj = await self.db.get(self.model, id)
        if obj is None:
            raise fastapi.HTTPException(404)
        return obj

    @route("/", methods=["POST"], status_code=201)
    async def post(self, schema_obj):  # schema_obj type is set in before_include_view
        return await self.process_post(schema_obj)

    async def process_post(self, schema_obj) -> Any:
        """
        Handle a POST request on "/". This should create a new object.
        Feel free to override this method.
        """
        obj = await self.make_new_object(schema_obj)
        return await self.save_object(obj)

    @route("/{id}", methods=["PUT"])
    async def put(self, id: int, schema_obj):
        return await self.process_put(id, schema_obj)

    async def process_put(self, id, schema_obj) -> Any:
        """
        Handle a PUT request on "/{id}". This should (partially) update an existing
        object.
        Feel free to override this method.
        """
        obj = await self.process_get(id)
        return await self.update_object(obj, schema_obj)

    @route("/{id}", methods=["DELETE"], status_code=204)
    async def delete(self, id: int):
        obj = await self.process_get(id)
        await self.process_delete(obj)
        return fastapi.Response(status_code=204)

    async def process_delete(self, obj: SQLBase) -> None:
        """
        Handle a DELETE request on "/{id}". This should delete an object from the
        database. `process_get()` is called first to lookup the object.
        Feel free to override this method.
        """
        await self.db.delete(obj)
        await self.db.flush()

    async def make_new_object(self, schema_obj):
        await async_resolve_ids_to_sqlalchemy_objects(schema_obj, self.db)
        obj = self.model(**dict(schema_obj))
        self.db.add(obj)
        return obj

    async def update_object(self, obj, schema_obj):
        await async_resolve_ids_to_sqlalchemy_objects(schema_obj, self.db)
        for field_name, value in schema_obj:
            if value is NOT_SET:
                continue
            # `read_only_fields` are removed when using
            # `create_model_without_read_only_fields()` but if a custom
            # `creation_schema` is used this might still be needed.
            if field_name in self.schema.read_only_fields:
                continue
            setattr(obj, field_name, value)
        return await self.save_object(obj)

    async def save_object(self, obj):
        await self.db.flush()
        await self.db.refresh(obj)
        return obj


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
    with a new copy on directly on view_cls . This allows us to change the
    annotations on these endpoints without affecting the parent endpoints.

    FooView.get() delegates to AsyncAlchemyView.get() if it is not overridden (this is
    called implicit delegation through method resolution). And if we add the
    annotation that FooView.get() returns FooSchema but do not make a copy then
    AsyncAlchemyView.get() will get the FooSchema annotation.
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
    for name, hint in get_type_hints(view_cls).items():
        if get_origin(hint) is ClassVar:
            continue
        parameter_kwargs = {"default": getattr(view_cls, name, Ellipsis)}
        dependency_names.append(name)
        new_parameters.append(
            inspect.Parameter(
                name=name,
                kind=inspect.Parameter.KEYWORD_ONLY,
                annotation=hint,
                **parameter_kwargs,
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
