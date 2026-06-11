"""The route shells carry override-redirect docstrings for help()/source
readers, but those must never leak into a user's OpenAPI spec as operation
descriptions (FastAPI reads endpoint.__doc__). User-defined endpoints keep
FastAPI's normal docstring behavior."""

import fastapi
from sqlalchemy.orm import Mapped

import fastapi_restly as fr


class ShellDocModel(fr.IDBase):
    name: Mapped[str]


class ShellDocRead(fr.IDSchema):
    name: str


def _spec_for(view_cls) -> dict:
    app = fastapi.FastAPI()
    fr.include_view(app, view_cls)
    return app.openapi()


def test_shells_have_redirect_docstrings():
    for cls in (fr.RestView, fr.AsyncRestView):
        for shell in (
            "get_many_endpoint",
            "get_one_endpoint",
            "create_endpoint",
            "update_endpoint",
            "delete_endpoint",
        ):
            doc = getattr(cls, shell).__doc__
            assert doc and "route shell" in doc, (cls, shell)


def test_generated_routes_do_not_leak_shell_docstrings_into_openapi():
    class PlainView(fr.AsyncRestView):
        prefix = "/shell-doc-plain"
        model = ShellDocModel
        schema = ShellDocRead

    spec = _spec_for(PlainView)
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            assert not op.get("description"), (path, method, op.get("description"))


def test_user_overridden_shell_keeps_its_docstring_in_openapi():
    class CustomView(fr.AsyncRestView):
        prefix = "/shell-doc-custom"
        model = ShellDocModel
        schema = ShellDocRead

        @fr.get("/{id}")
        async def get_one_endpoint(self, id: int):
            """Fetch one record, customer-facing description."""
            obj = await self.handle_get_one(id)
            return self.to_response(obj)

    spec = _spec_for(CustomView)
    op = spec["paths"]["/shell-doc-custom/{id}"]["get"]
    assert op.get("description") == "Fetch one record, customer-facing description."
