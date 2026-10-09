"""The response class and the list response class of a view.

``fr.schemas.derive_schema_response`` and
``fr.schemas.derive_schema_list_response`` return the classes that a view
uses, so a custom route can name them and OpenAPI shows one type for each
resource.
"""

import warnings
from typing import Any, Generic, TypeVar

import fastapi
import pydantic
from sqlalchemy.orm import Mapped

import fastapi_restly as fr
from fastapi_restly.exc import RestlyDuplicateSchemaNameWarning

T = TypeVar("T")


class DataCount(pydantic.BaseModel, Generic[T]):
    data: list[T]
    total_count: int


def _spec(app: fastapi.FastAPI) -> dict[str, Any]:
    with warnings.catch_warnings():
        warnings.simplefilter("error", RestlyDuplicateSchemaNameWarning)
        return app.openapi()


def _components(app: fastapi.FastAPI) -> set[str]:
    return set(_spec(app)["components"]["schemas"]) - {
        "HTTPValidationError",
        "ValidationError",
    }


def _ref(app: fastapi.FastAPI, path: str, method: str, status: str = "200") -> str:
    operation = _spec(app)["paths"][path][method]
    schema = operation["responses"][status]["content"]["application/json"]["schema"]
    return schema["$ref"].removeprefix("#/components/schemas/")


def _response_model(app: fastapi.FastAPI, path: str, method: str) -> Any:
    for route in app.routes:
        if (
            isinstance(route, fastapi.routing.APIRoute)
            and route.path_format == path
            and method.upper() in route.methods
        ):
            return route.response_model
    raise AssertionError(f"no route {method} {path}")


def test_derive_schema_response_returns_the_class_the_view_uses():
    class Account(fr.IDBase):
        name: Mapped[str]
        password_hash: Mapped[str] = ""

    class AccountSchema(fr.IDSchema):
        name: str
        password: fr.WriteOnly[str]

    AccountResponse = fr.schemas.derive_schema_response(AccountSchema)

    app = fastapi.FastAPI()

    @fr.include_view(app)
    class AccountView(fr.AsyncRestView):
        prefix = "/accounts"
        model = Account
        schema = AccountSchema

    assert AccountResponse.__name__ == "AccountResponse"
    assert issubclass(AccountResponse, AccountSchema)
    assert set(AccountResponse.model_fields) == {"id", "name"}
    assert fr.schemas.derive_schema_response(AccountSchema) is AccountResponse
    assert _response_model(app, "/accounts/{id}", "get") is AccountResponse


def test_derive_schema_list_response_returns_the_class_the_view_uses():
    class Book(fr.IDBase):
        title: Mapped[str]

    class BookSchema(fr.IDSchema):
        title: str

    BookResponse = fr.schemas.derive_schema_response(BookSchema)
    BookListResponse = fr.schemas.derive_schema_list_response(BookResponse)

    app = fastapi.FastAPI()

    @fr.include_view(app)
    class BookView(fr.AsyncRestView):
        prefix = "/books"
        model = Book
        schema = BookSchema

    assert BookListResponse.__name__ == "BookListResponse"
    assert issubclass(BookListResponse, fr.views.PaginatedEnvelope)
    assert _response_model(app, "/books", "get") is BookListResponse


def test_only_the_envelope_of_the_pagination_matters():
    class LeafSchema(fr.IDSchema):
        name: str

    LeafResponse = fr.schemas.derive_schema_response(LeafSchema)
    paginated = fr.schemas.derive_schema_list_response(LeafResponse)
    small_pages = fr.schemas.derive_schema_list_response(
        LeafResponse, pagination=fr.NumberedPagination(default_page_size=5)
    )
    unpaginated = fr.schemas.derive_schema_list_response(LeafResponse, pagination=None)
    no_pagination = fr.schemas.derive_schema_list_response(
        LeafResponse, pagination=fr.NoPagination()
    )
    counted = fr.schemas.derive_schema_list_response(
        LeafResponse, pagination=fr.NoPagination(envelope=DataCount)
    )

    assert small_pages is paginated
    assert no_pagination is unpaginated
    assert issubclass(unpaginated, fr.views.Envelope)
    assert not issubclass(unpaginated, fr.views.PaginatedEnvelope)
    assert issubclass(counted, DataCount)
    assert {paginated.__name__, unpaginated.__name__, counted.__name__} == {
        "LeafListResponse"
    }


def test_a_custom_route_that_names_the_classes_shows_one_type_per_resource():
    class Task(fr.IDBase):
        title: Mapped[str]
        archived: Mapped[bool] = False

    class TaskSchema(fr.IDSchema):
        title: str
        archived: bool = False

    TaskResponse = fr.schemas.derive_schema_response(TaskSchema)
    TaskListResponse = fr.schemas.derive_schema_list_response(
        TaskResponse, pagination=None
    )

    app = fastapi.FastAPI()

    @fr.include_view(app)
    class TaskView(fr.AsyncRestView):
        prefix = "/tasks"
        model = Task
        schema = TaskSchema
        pagination = None

        @fr.get("/archived", response_model=TaskListResponse)
        async def archived(self, list_params):
            result = await self.handle_get_many(list_params, where=Task.archived)
            return self.to_response(result, fr.ResponseShape.LIST)

        @fr.post("/{id}/archive", response_model=TaskResponse)
        async def archive(self, id: int):
            task = await self.get_one(id)
            async with self.write_action("archive", obj=task):
                task.archived = True
            return self.to_response(task)

    assert _ref(app, "/tasks/archived", "get") == "TaskListResponse"
    assert _ref(app, "/tasks", "get") == "TaskListResponse"
    assert _ref(app, "/tasks/{id}/archive", "post", "201") == "TaskResponse"
    assert _components(app) == {
        "TaskResponse",
        "TaskCreate",
        "TaskUpdate",
        "TaskListResponse",
    }


def test_the_wrong_pagination_gives_a_second_class_and_a_warning():
    class Shelf(fr.IDBase):
        name: Mapped[str]

    class ShelfSchema(fr.IDSchema):
        name: str

    ShelfResponse = fr.schemas.derive_schema_response(ShelfSchema)
    # The view has no pagination, but the route forgets to say so.
    ShelfListResponse = fr.schemas.derive_schema_list_response(ShelfResponse)

    app = fastapi.FastAPI()

    @fr.include_view(app)
    class ShelfView(fr.AsyncRestView):
        prefix = "/shelves"
        model = Shelf
        schema = ShelfSchema
        pagination = None

        @fr.get("/empty", response_model=ShelfListResponse)
        async def empty(self, list_params):
            result = await self.handle_get_many(list_params)
            return self.to_response(result, fr.ResponseShape.LIST)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", RestlyDuplicateSchemaNameWarning)
        app.openapi()

    assert any(
        issubclass(warning.category, RestlyDuplicateSchemaNameWarning)
        and "ShelfListResponse" in str(warning.message)
        for warning in caught
    )
