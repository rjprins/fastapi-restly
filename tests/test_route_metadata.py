"""CRUD names and OpenAPI metadata remain stable across view classes."""

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from sqlalchemy.orm import Mapped

import fastapi_restly as fr


@pytest.fixture(
    params=[fr.RestView, fr.AsyncRestView, fr.ReactAdminView, fr.AsyncReactAdminView],
    ids=["sync", "async", "react-admin-sync", "react-admin-async"],
)
def base(request):
    class Item(fr.IDBase):
        name: Mapped[str]

    class ItemRead(fr.IDSchema):
        name: str

    class ItemBase(request.param):
        model = Item
        schema = ItemRead

    return ItemBase


def _operations(app):
    return {
        (path, method): (operation["operationId"], operation["summary"])
        for path, methods in app.openapi()["paths"].items()
        for method, operation in methods.items()
    }


def test_two_view_operation_ids_and_summaries(base):
    class ItemView(base):
        prefix = "/items"

    class UserView(base):
        prefix = "/users"

    app = FastAPI()
    fr.include_view(app, ItemView)
    fr.include_view(app, UserView)

    expected = {
        ("/items", "get"): ("get_many_items_get", "List"),
        ("/items", "post"): ("create_items_post", "Create"),
        ("/items/{id}", "get"): ("get_one_items__id__get", "Retrieve"),
        ("/items/{id}", "patch"): ("update_items__id__patch", "Update"),
        ("/items/{id}", "delete"): ("delete_items__id__delete", "Delete"),
        ("/users", "get"): ("get_many_users_get", "List"),
        ("/users", "post"): ("create_users_post", "Create"),
        ("/users/{id}", "get"): ("get_one_users__id__get", "Retrieve"),
        ("/users/{id}", "patch"): ("update_users__id__patch", "Update"),
        ("/users/{id}", "delete"): ("delete_users__id__delete", "Delete"),
    }
    if issubclass(base, (fr.ReactAdminView, fr.AsyncReactAdminView)):
        expected.update(
            {
                ("/items/{id}", "put"): ("put_items__id__put", "Update (PUT)"),
                ("/users/{id}", "put"): ("put_users__id__put", "Update (PUT)"),
            }
        )
    assert _operations(app) == expected
    ids = [operation_id for operation_id, _ in expected.values()]
    assert len(ids) == len(set(ids))


def test_route_options_override_metadata_without_replacing_endpoints(base):
    class ItemView(base):
        prefix = "/items"
        route_options = {
            fr.ViewRoute.GET_MANY: {
                "name": "search_items",
                "summary": "Search items",
                "operation_id": "catalog_search",
            },
            "create_endpoint": {"name": "add_item", "summary": "Add an item"},
        }

    app = FastAPI()
    router = APIRouter()
    fr.include_view(router, ItemView)
    app.include_router(router, prefix="/api")

    operations = _operations(app)
    assert operations["/api/items", "get"] == ("catalog_search", "Search items")
    assert operations["/api/items", "post"] == (
        "add_item_api_items_post",
        "Add an item",
    )
    assert str(app.url_path_for("search_items")) == "/api/items"
    assert str(app.url_path_for("add_item")) == "/api/items"


def test_explicit_decorator_metadata_wins_over_defaults(base):
    class ItemView(base):
        prefix = "/items"

        @fr.get("/{id}", name="read_item", summary="Read an item", operation_id="read")
        def get_one_endpoint(self, id: int):
            return {"id": id, "name": "Item"}

    app = FastAPI()
    fr.include_view(app, ItemView)
    assert _operations(app)["/items/{id}", "get"] == ("read", "Read an item")
    assert str(app.url_path_for("read_item", id=1)) == "/items/1"


def test_route_options_win_over_decorator_metadata(base):
    class ItemView(base):
        prefix = "/items"
        route_options = {
            fr.ViewRoute.GET_ONE: {
                "name": "retrieve_item",
                "summary": "Retrieve an item",
                "operation_id": "retrieve",
            }
        }

        @fr.get("/{id}", name="read_item", summary="Read an item", operation_id="read")
        def get_one_endpoint(self, id: int):
            return {"id": id, "name": "Item"}

    app = FastAPI()
    fr.include_view(app, ItemView)
    assert _operations(app)["/items/{id}", "get"] == ("retrieve", "Retrieve an item")
    assert str(app.url_path_for("retrieve_item", id=1)) == "/items/1"


def test_inherited_options_and_repeated_registration_do_not_mutate_metadata(base):
    options = {fr.ViewRoute.GET_MANY: {"summary": "Find items"}}

    class ParentView(base):
        prefix = "/v1"
        route_options = options

    app = FastAPI()
    fr.include_view(app, ParentView)

    class ChildView(ParentView):
        prefix = "/items"

    class SiblingView(ParentView):
        prefix = "/others"
        route_options = {fr.ViewRoute.GET_MANY: {"summary": "Find others"}}

    fr.include_view(app, ChildView)
    fr.include_view(app, SiblingView)
    operations = _operations(app)
    assert operations["/v1", "get"] == ("get_many_v1_get", "Find items")
    assert operations["/v1/items", "get"] == ("get_many_v1_items_get", "Find items")
    assert operations["/v1/others", "get"] == ("get_many_v1_others_get", "Find others")

    second_app = FastAPI()
    fr.include_view(second_app, ChildView)
    assert _operations(second_app)["/v1/items", "get"] == operations["/v1/items", "get"]
    assert options == {fr.ViewRoute.GET_MANY: {"summary": "Find items"}}


def test_fastapi_custom_id_generator_receives_clean_route_names(base):
    def generate_id(route: APIRoute):
        return f"{route.tags[0]}-{route.name}"

    class ItemView(base):
        prefix = "/items"
        tags = ["items"]

    class UserView(base):
        prefix = "/users"
        tags = ["users"]

    app = FastAPI(generate_unique_id_function=generate_id)
    fr.include_view(app, ItemView)
    fr.include_view(app, UserView)
    operations = _operations(app)
    assert operations["/items", "get"][0] == "items-get_many"
    assert operations["/users", "post"][0] == "users-create"
    ids = [operation_id for operation_id, _ in operations.values()]
    assert len(ids) == len(set(ids))


def test_plain_view_metadata_and_options():
    class UtilityView(fr.View):
        prefix = "/utils"
        route_options = {"status": {"name": "status", "summary": "Service status"}}

        @fr.get("/status")
        def status(self):
            return {"status": "ok"}

        @fr.get("/custom")
        def get_many_endpoint(self):
            return []

    app = FastAPI()
    fr.include_view(app, UtilityView)
    operations = _operations(app)
    assert operations["/utils/status", "get"] == (
        "status_utils_status_get",
        "Service status",
    )
    assert operations["/utils/custom", "get"] == (
        "utilityview_get_many_endpoint_utils_custom_get",
        "Utilityview Get Many Endpoint",
    )


def test_unknown_route_options_key_raises(base):
    class ItemView(base):
        prefix = "/items"
        route_options = {"create": {"summary": "Add an item"}}

    with pytest.raises(fr.exc.RestlyConfigurationError, match="create.*route_options"):
        fr.include_view(FastAPI(), ItemView)


def test_options_for_excluded_routes_are_allowed(base):
    class ItemView(base):
        prefix = "/items"
        exclude_routes = (fr.ViewRoute.DELETE,)
        route_options = {fr.ViewRoute.DELETE: {"summary": "Remove an item"}}

    app = FastAPI()
    fr.include_view(app, ItemView)
    assert ("/items/{id}", "delete") not in _operations(app)
