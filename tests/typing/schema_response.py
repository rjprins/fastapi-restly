from typing import Any

from fastapi import FastAPI
from sqlalchemy.orm import Mapped
from typing_extensions import assert_type

import fastapi_restly as fr

app = FastAPI()


class Account(fr.IDBase):
    name: Mapped[str]
    password_hash: Mapped[str]


class AccountSchema(fr.IDSchema):
    name: str
    password: fr.WriteOnly[str]


class AccountResponse(fr.IDSchema):
    name: str


# A custom route names the list response class through the function, because
# the view builds it only when it is registered.
AccountListResponse = fr.schemas.derive_schema_list_response(AccountResponse)


@fr.include_view(app)
class AccountView(fr.AsyncRestView[Account, AccountResponse]):
    """A view with its own response class puts it in the second parameter."""

    prefix = "/accounts"
    model = Account
    schema = AccountSchema
    schema_response = AccountResponse

    @fr.get("/first", response_model=AccountResponse)
    async def first(self) -> AccountResponse:
        account = await self.get_one(1)
        return self.to_single_response(account)

    @fr.get("/inactive", response_model=AccountListResponse)
    async def inactive(self, list_params: Any) -> Any:
        result = await self.handle_get_many(list_params)
        return self.to_response(result, fr.ResponseShape.LIST)


class Note(fr.IDBase):
    text: Mapped[str]


class NoteSchema(fr.IDSchema):
    text: str


# The derived response class is a subclass of the schema.
NoteResponse = fr.schemas.derive_schema_response(NoteSchema)
assert_type(NoteResponse, type[NoteSchema])
NoteListResponse = fr.schemas.derive_schema_list_response(NoteResponse, pagination=None)


@fr.include_view(app)
class NoteView(fr.RestView[Note, NoteSchema]):
    """A view without its own response class keeps its schema there."""

    prefix = "/notes"
    model = Note
    schema = NoteSchema
    pagination = None

    @fr.post("/{id}/touch", response_model=NoteResponse)
    def touch(self, id: int) -> NoteSchema:
        note = self.get_one(id)
        return self.to_single_response(note)


# A function builds the derived class, so a type checker does not accept it in
# an annotation. At runtime it is a normal class: the docs tell users to add
# this ignore, and mypy's warn_unused_ignores fails here if it is not needed.
def show_note(note: NoteResponse) -> None:  # type: ignore[valid-type]
    ...
