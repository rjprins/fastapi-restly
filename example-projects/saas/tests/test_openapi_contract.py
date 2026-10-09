"""Pin the canonical spelling of collection endpoints."""

import inspect
import warnings

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

import fastapi_restly as fr
from fastapi_restly.exc import RestlyDuplicateSchemaNameWarning


@pytest.mark.parametrize(
    "collection_path",
    [
        "/countries",
        "/labels",
        "/organizations",
        "/projects",
        "/task-labels",
        "/tasks",
        "/uploads",
        "/users",
    ],
)
def test_openapi_uses_collection_paths_without_trailing_slashes(
    restly_app: FastAPI, collection_path: str
) -> None:
    paths = restly_app.openapi()["paths"]

    assert collection_path in paths
    assert f"{collection_path}/" not in paths


@pytest.mark.parametrize("trash_path", ["/projects/trash", "/tasks/trash"])
def test_trash_routes_carry_the_list_params(
    restly_app: FastAPI, trash_path: str
) -> None:
    """A route declaring ``list_params`` shows the list params in the contract."""
    operation = restly_app.openapi()["paths"][trash_path]["get"]
    names = {parameter["name"] for parameter in operation["parameters"]}
    assert {"page", "page_size", "sort"} <= names


def test_openapi_class_names_are_unique(restly_app: FastAPI) -> None:
    """Restly warns when two classes in the API have the same name: OpenAPI
    then shows them under long names that can change, and generated clients
    use these names as type names. This test turns the warning into an error."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", RestlyDuplicateSchemaNameWarning)
        restly_app.openapi()


def _api_routes(app: FastAPI) -> list[APIRoute]:
    """The routes of the app. FastAPI 0.137 and later keep an included router
    as one entry in ``app.routes``, and ``effective_route_contexts()`` gives
    its routes."""
    routes: list[object] = []
    for route in app.routes:
        expand = getattr(route, "effective_route_contexts", None)
        for leaf in expand() if expand is not None else [route]:
            routes.append(getattr(leaf, "original_route", leaf))
    return [route for route in routes if isinstance(route, APIRoute)]


def _view_resources(app: FastAPI) -> set[str]:
    """The resources that have a view: the views are the classes that FastAPI
    builds as the ``self`` of their routes."""
    views = {
        dependency.call
        for route in _api_routes(app)
        for dependency in route.dependant.dependencies
        if inspect.isclass(dependency.call)
        and issubclass(dependency.call, fr.views.BaseRestView)
    }
    return {view.schema_response.__name__.removesuffix("Response") for view in views}


def test_each_resource_has_one_type_in_openapi(restly_app: FastAPI) -> None:
    """A custom route names the response classes of the view, so a generated
    client gets one type per resource. No route shows the view's schema or a
    ``PaginatedEnvelope_`` class next to ``<Resource>Response`` and
    ``<Resource>ListResponse``."""
    resources = _view_resources(restly_app)
    components = set(restly_app.openapi()["components"]["schemas"])

    assert {"Task", "Project", "User"} <= resources
    assert {name for name in components if "Envelope_" in name} == set()
    assert components & {f"{name}Schema" for name in resources} == set()
    assert {f"{name}Response" for name in resources} <= components
    assert {f"{name}ListResponse" for name in resources} <= components


@pytest.mark.parametrize(
    ("path", "method", "status", "name"),
    [
        ("/tasks/trash", "get", "200", "TaskListResponse"),
        ("/tasks/{id}/restore", "post", "201", "TaskResponse"),
        ("/tasks/{id}/start", "post", "201", "TaskResponse"),
        ("/projects/trash", "get", "200", "ProjectListResponse"),
        ("/projects/{id}/restore", "post", "201", "ProjectResponse"),
        ("/projects/by-slug/{slug}", "get", "200", "ProjectResponse"),
        ("/organizations", "post", "201", "OrganizationResponse"),
        ("/users/me", "get", "200", "UserResponse"),
        ("/uploads", "post", "201", "UploadResponse"),
        ("/task-labels/create-and-attach", "post", "201", "TaskLabelResponse"),
    ],
)
def test_custom_routes_name_the_view_s_response_classes(
    restly_app: FastAPI, path: str, method: str, status: str, name: str
) -> None:
    operation = restly_app.openapi()["paths"][path][method]
    schema = operation["responses"][status]["content"]["application/json"]["schema"]
    assert schema["$ref"] == f"#/components/schemas/{name}"
