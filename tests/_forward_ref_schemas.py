"""Schemas whose forward reference is defined after Restly builds their
response class, as when a module includes a view before it defines a schema
that the view's schema refers to."""

from typing import Optional

import pydantic

import fastapi_restly as fr
from fastapi_restly.schemas._base import _derive_schema_response


class AuthorSchema(fr.IDSchema):
    name: str
    note: fr.ReadOnly[Optional["Note"]] = None


class LoginSchema(fr.IDSchema):
    name: str
    note: fr.ReadOnly[Optional["Note"]] = None
    password: fr.WriteOnly[str]


AuthorResponse = _derive_schema_response(AuthorSchema)
LoginResponse = _derive_schema_response(LoginSchema)


class Note(pydantic.BaseModel):
    text: str
