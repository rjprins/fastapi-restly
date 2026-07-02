"""The endpoint methods carry override-redirect docstrings for help()/source
readers, but those must never leak into a user's OpenAPI spec as operation
descriptions (FastAPI reads endpoint.__doc__). User-defined endpoints keep
FastAPI's normal docstring behavior."""

import fastapi
from sqlalchemy.orm import Mapped

import fastapi_restly as fr


class EndpointDocModel(fr.IDBase):
    name: Mapped[str]


class EndpointDocRead(fr.IDSchema):
    name: str


def _spec_for(view_cls) -> dict:
    app = fastapi.FastAPI()
    fr.include_view(app, view_cls)
    return app.openapi()


def test_endpoint_methods_have_redirect_docstrings():
    for cls in (fr.RestView, fr.AsyncRestView):
        for endpoint in (
            "get_many_endpoint",
            "get_one_endpoint",
            "create_endpoint",
            "update_endpoint",
            "delete_endpoint",
        ):
            doc = getattr(cls, endpoint).__doc__
            assert doc and "endpoint method" in doc, (cls, endpoint)


def test_generated_routes_do_not_leak_endpoint_docstrings_into_openapi():
    class PlainView(fr.AsyncRestView):
        prefix = "/endpoint-doc-plain"
        model = EndpointDocModel
        schema = EndpointDocRead

    spec = _spec_for(PlainView)
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            assert not op.get("description"), (path, method, op.get("description"))


def test_user_replaced_endpoint_method_keeps_its_docstring_in_openapi():
    class CustomView(fr.AsyncRestView):
        prefix = "/endpoint-doc-custom"
        model = EndpointDocModel
        schema = EndpointDocRead

        @fr.get("/{id}")
        async def get_one_endpoint(self, id: int):
            """Fetch one record, customer-facing description."""
            obj = await self.handle_get_one(id)
            return self.to_response(obj)

    spec = _spec_for(CustomView)
    op = spec["paths"]["/endpoint-doc-custom/{id}"]["get"]
    assert op.get("description") == "Fetch one record, customer-facing description."
