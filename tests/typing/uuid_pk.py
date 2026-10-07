"""Typing fixture: UUID primary keys.

Verifies that consumer code using `id_type = UUID` and `IDSchema[Model]`
relations on UUID-keyed models stays Pyright-clean.
"""

from uuid import UUID, uuid4

from fastapi import FastAPI
from sqlalchemy import ForeignKey, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

import fastapi_restly as fr

app = FastAPI()


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "account"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str]


class Project(Base):
    __tablename__ = "project"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    title: Mapped[str]
    account_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("account.id"))
    account: Mapped[Account] = relationship()


class AccountSchema(fr.BaseSchema):
    id: fr.ReadOnly[UUID]
    name: str


class ProjectSchema(fr.IDSchema[Project]):
    title: str
    account_id: fr.IDSchema[Account]


@fr.include_view(app)
class AccountView(
    fr.AsyncRestView[Account, AccountSchema, AccountSchema, AccountSchema, UUID]
):
    prefix = "/accounts"
    model = Account
    schema = AccountSchema
    id_type = UUID


@fr.include_view(app)
class ProjectView(
    fr.AsyncRestView[Project, ProjectSchema, ProjectSchema, ProjectSchema, UUID]
):
    prefix = "/projects"
    model = Project
    schema = ProjectSchema
    id_type = UUID
