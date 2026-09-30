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
        ("/items", "get"): ("items_list", "List"),
        ("/items", "post"): ("items_create", "Create"),
        ("/items/{id}", "get"): ("items_get", "Retrieve"),
        ("/items/{id}", "patch"): ("items_update", "Update"),
        ("/items/{id}", "delete"): ("items_delete", "Delete"),
        ("/users", "get"): ("users_list", "List"),
        ("/users", "post"): ("users_create", "Create"),
        ("/users/{id}", "get"): ("users_get", "Retrieve"),
        ("/users/{id}", "patch"): ("users_update", "Update"),
        ("/users/{id}", "delete"): ("users_delete", "Delete"),
    }
    if issubclass(base, (fr.ReactAdminView, fr.AsyncReactAdminView)):
        expected.update(
            {
                ("/items/{id}", "put"): ("items_put", "Update (PUT)"),
                ("/users/{id}", "put"): ("users_put", "Update (PUT)"),
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
    assert operations["/api/items", "post"] == ("api_items_add_item", "Add an item")
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
    assert operations["/v1", "get"] == ("v1_list", "Find items")
    assert operations["/v1/items", "get"] == ("v1_items_list", "Find items")
    assert operations["/v1/others", "get"] == ("v1_others_list", "Find others")

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


def test_operation_ids_follow_repeated_nested_router_mounts(base):
    class ItemView(base):
        prefix = "/items"

    router = APIRouter()
    fr.include_view(router, ItemView)
    catalog = APIRouter()
    catalog.include_router(router, prefix="/catalog")
    app = FastAPI()
    app.include_router(catalog, prefix="/v1")
    app.include_router(catalog, prefix="/v2")

    operations = _operations(app)
    assert operations["/v1/catalog/items", "get"][0] == "v1_catalog_items_list"
    assert operations["/v2/catalog/items", "get"][0] == "v2_catalog_items_list"
    assert operations["/v1/catalog/items/{id}", "get"][0] == "v1_catalog_items_get"
    assert operations["/v2/catalog/items/{id}", "patch"][0] == "v2_catalog_items_update"
    ids = [operation_id for operation_id, _ in operations.values()]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(
    ("prefix", "resource"),
    [
        ("/order-items", "order_items"),
        ("/people", "people"),
        ("/news", "news"),
        ("/projects/{project_id}/items", "projects_by_project_id_items"),
        ("/groups/{group_id}", "groups_by_group_id"),
    ],
)
def test_operation_id_resource_preserves_scope_and_plural_spelling(
    base, prefix, resource
):
    class ItemView(base):
        pass

    ItemView.prefix = prefix
    app = FastAPI()
    fr.include_view(app, ItemView)
    operations = _operations(app)
    assert operations[prefix, "get"][0] == f"{resource}_list"
    assert operations[prefix + "/{id}", "get"][0] == f"{resource}_get"


@pytest.mark.parametrize("generator_source", ["app", "router", "mount", "route"])
def test_explicit_generators_override_crud_fallback_after_mount(base, generator_source):
    def generate_id(route: APIRoute):
        return f"custom_{route.name}_{next(iter(route.methods)).lower()}"

    class ItemView(base):
        prefix = "/items"

    kwargs = {"generate_unique_id_function": generate_id}
    if generator_source == "route":
        ItemView.route_options = {fr.ViewRoute.GET_MANY: kwargs}
    router = APIRouter(**(kwargs if generator_source == "router" else {}))
    fr.include_view(router, ItemView)
    app = FastAPI(**(kwargs if generator_source == "app" else {}))
    app.include_router(
        router, prefix="/api", **(kwargs if generator_source == "mount" else {})
    )
    operations = _operations(app)
    assert operations["/api/items", "get"][0] == "custom_get_many_get"


def test_explicit_operation_id_wins_over_app_generator(base):
    class ItemView(base):
        prefix = "/items"
        route_options = {fr.ViewRoute.GET_MANY: {"operation_id": "catalog_search"}}

    app = FastAPI(generate_unique_id_function=lambda route: f"custom_{route.name}")
    fr.include_view(app, ItemView)
    operations = _operations(app)
    assert operations["/items", "get"][0] == "catalog_search"
    assert operations["/items/{id}", "get"][0] == "custom_get_one"


def test_custom_member_route_name_changes_operation_id_action(base):
    class ItemView(base):
        prefix = "/items"
        route_options = {fr.ViewRoute.GET_ONE: {"name": "fetch_item"}}

    app = FastAPI()
    fr.include_view(app, ItemView)
    assert _operations(app)["/items/{id}", "get"][0] == "items_fetch_item"
    assert str(app.url_path_for("fetch_item", id=1)) == "/items/1"


def test_class_name_and_tags_do_not_change_default_operation_ids(base):
    class ItemView(base):
        prefix = "/items"
        tags = ["catalog"]

    class RenamedView(base):
        prefix = "/items"
        tags = ["inventory"]

    app = FastAPI()
    renamed_app = FastAPI()
    fr.include_view(app, ItemView)
    fr.include_view(renamed_app, RenamedView)
    assert _operations(app) == _operations(renamed_app)
    assert _operations(app)["/items", "get"][0] == "items_list"


def test_custom_routes_on_rest_view_keep_fastapi_operation_ids(base):
    class ItemView(base):
        prefix = "/items"

        @fr.get("/stats", name="statistics")
        def stats(self):
            return {"count": 0}

    app = FastAPI()
    fr.include_view(app, ItemView)
    operations = _operations(app)
    assert operations["/items/stats", "get"][0] == "statistics_items_stats_get"
    assert operations["/items", "get"][0] == "items_list"


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
