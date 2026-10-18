"""CRUD names and OpenAPI metadata remain stable across view classes."""

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from sqlalchemy.orm import Mapped

import fastapi_restly as fr
from fastapi_restly.views import ViewCreate, ViewID, ViewResponse

from ._models import SharedBase


@pytest.fixture(
    params=[fr.RestView, fr.AsyncRestView, fr.ReactAdminView, fr.AsyncReactAdminView],
    ids=["sync", "async", "react-admin-sync", "react-admin-async"],
)
def base(request):
    class Item(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class ItemSchema(fr.IDSchema):
        name: str

    class ItemBase(request.param):
        model = Item
        schema = ItemSchema

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
        ("/items/{id}", "get"): ("items_get", "Get"),
        ("/items/{id}", "patch"): ("items_update", "Update"),
        ("/items/{id}", "delete"): ("items_delete", "Delete"),
        ("/users", "get"): ("users_list", "List"),
        ("/users", "post"): ("users_create", "Create"),
        ("/users/{id}", "get"): ("users_get", "Get"),
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


def test_override_with_a_decorator_sets_metadata_and_keeps_the_route(base):
    class ItemView(base):
        prefix = "/items"

        if issubclass(base, fr.AsyncRestView):

            @fr.get("/{id}", operation_id="catalog_get", summary="Get an item")
            async def get_one_endpoint(self, id: ViewID) -> ViewResponse:
                return await super().get_one_endpoint(id)

            @fr.post("/", name="add_item", summary="Add an item")
            async def create_endpoint(self, schema_obj: ViewCreate) -> ViewResponse:
                return await super().create_endpoint(schema_obj)

        else:

            @fr.get("/{id}", operation_id="catalog_get", summary="Get an item")
            def get_one_endpoint(self, id: ViewID) -> ViewResponse:
                return super().get_one_endpoint(id)

            @fr.post("/", name="add_item", summary="Add an item")
            def create_endpoint(self, schema_obj: ViewCreate) -> ViewResponse:
                return super().create_endpoint(schema_obj)

    app = FastAPI()
    router = APIRouter()
    fr.include_view(router, ItemView)
    app.include_router(router, prefix="/api")

    operations = _operations(app)
    assert operations["/api/items/{id}", "get"] == ("catalog_get", "Get an item")
    assert operations["/api/items", "post"] == ("api_items_add_item", "Add an item")
    assert str(app.url_path_for("get_one", id=1)) == "/api/items/1"
    assert str(app.url_path_for("add_item")) == "/api/items"

    # The markers give the overridden routes the view's types
    paths = app.openapi()["paths"]
    create = paths["/api/items"]["post"]
    body = create["requestBody"]["content"]["application/json"]["schema"]
    assert body["$ref"].endswith("/ItemCreate")
    response = create["responses"]["201"]["content"]["application/json"]["schema"]
    assert response["$ref"].endswith("/ItemResponse")
    (id_param,) = paths["/api/items/{id}"]["get"]["parameters"]
    assert id_param["schema"]["type"] == "integer"


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


def test_inherited_overrides_and_repeated_registration_keep_metadata(base):
    class ParentView(base):
        prefix = "/v1"

        @fr.get("/{id}", summary="Find an item")
        def get_one_endpoint(self, id: int):
            return {"id": id, "name": "Item"}

    app = FastAPI()
    fr.include_view(app, ParentView)

    class ChildView(ParentView):
        prefix = "/items"

    class SiblingView(ParentView):
        prefix = "/others"

        @fr.get("/{id}", summary="Find another")
        def get_one_endpoint(self, id: int):
            return {"id": id, "name": "Other"}

    fr.include_view(app, ChildView)
    fr.include_view(app, SiblingView)
    operations = _operations(app)
    assert operations["/v1/{id}", "get"] == ("v1_get", "Find an item")
    assert operations["/v1/items/{id}", "get"] == ("v1_items_get", "Find an item")
    assert operations["/v1/others/{id}", "get"] == ("v1_others_get", "Find another")

    second_app = FastAPI()
    fr.include_view(second_app, ChildView)
    second = _operations(second_app)
    assert second["/v1/items/{id}", "get"] == operations["/v1/items/{id}", "get"]


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

        if generator_source == "route":

            @fr.get("/{id}", generate_unique_id_function=generate_id)
            def get_one_endpoint(self, id: int):
                return {"id": id, "name": "Item"}

    kwargs = {"generate_unique_id_function": generate_id}
    router = APIRouter(**(kwargs if generator_source == "router" else {}))
    fr.include_view(router, ItemView)
    app = FastAPI(**(kwargs if generator_source == "app" else {}))
    app.include_router(
        router, prefix="/api", **(kwargs if generator_source == "mount" else {})
    )
    operations = _operations(app)
    assert operations["/api/items/{id}", "get"][0] == "custom_get_one_get"


def test_explicit_operation_id_wins_over_app_generator(base):
    class ItemView(base):
        prefix = "/items"

        @fr.get("/{id}", operation_id="catalog_get")
        def get_one_endpoint(self, id: int):
            return {"id": id, "name": "Item"}

    app = FastAPI(generate_unique_id_function=lambda route: f"custom_{route.name}")
    fr.include_view(app, ItemView)
    operations = _operations(app)
    assert operations["/items/{id}", "get"][0] == "catalog_get"
    assert operations["/items", "get"][0] == "custom_get_many"


def test_custom_member_route_name_changes_operation_id_action(base):
    class ItemView(base):
        prefix = "/items"

        @fr.get("/{id}", name="fetch_item")
        def get_one_endpoint(self, id: int):
            return {"id": id, "name": "Item"}

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


def test_plain_view_keeps_fastapi_metadata():
    class UtilityView(fr.View):
        prefix = "/utils"

        @fr.get("/status", name="status", summary="Service status")
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


def test_excluded_route_stays_out_when_the_view_overrides_it(base):
    class ItemView(base):
        prefix = "/items"
        exclude_routes = (fr.ViewRoute.DELETE,)

        @fr.delete("/{id}", summary="Remove an item")
        def delete_endpoint(self, id: int):
            return None

    app = FastAPI()
    fr.include_view(app, ItemView)
    assert ("/items/{id}", "delete") not in _operations(app)


@pytest.mark.parametrize(
    "base",
    [fr.ReactAdminView, fr.AsyncReactAdminView],
    ids=["react-admin-sync", "react-admin-async"],
    indirect=True,
)
def test_react_admin_put_override_sets_its_metadata(base):
    class ItemView(base):
        prefix = "/items"

        @fr.put("/{id}", summary="Replace an item", operation_id="catalog_replace")
        def put(self, id: int, schema_obj):
            return {"id": id, "name": "Item"}

    app = FastAPI()
    fr.include_view(app, ItemView)
    operations = _operations(app)
    assert operations["/items/{id}", "put"] == ("catalog_replace", "Replace an item")
    assert str(app.url_path_for("put", id=1)) == "/items/1"
