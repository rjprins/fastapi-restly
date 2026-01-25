from typing import Any, Sequence, TypeVar

import fastapi
import sqlalchemy
from sqlalchemy.orm import Session

from ..db import SessionDep
from ..models import Base
from ..query import apply_query_modifiers
from ..schemas import BaseSchema, get_writable_inputs, is_readonly_field, resolve_ids_to_sqlalchemy_objects
from ._base import BaseAlchemyView, delete, get, patch, post

T = TypeVar("T", bound=Base)


def make_new_object(
    session: Session,
    model_cls: type[T],
    schema_obj: BaseSchema,
    schema_cls: type[BaseSchema] | None = None,
) -> T:
    resolve_ids_to_sqlalchemy_objects(session, schema_obj)
    # Filter out read-only fields when creating the object
    data = {}
    for field_name, value in schema_obj:
        if schema_cls is not None and is_readonly_field(schema_cls, field_name):
            continue
        data[field_name] = value
    obj = model_cls(**data)
    session.add(obj)
    return obj


def update_object(
    session: Session,
    obj: Base,
    schema_obj: BaseSchema,
    schema_cls: type[BaseSchema] | None = None,
) -> Base:
    resolve_ids_to_sqlalchemy_objects(session, schema_obj)
    for field_name, value in get_writable_inputs(schema_obj, schema_cls).items():
        setattr(obj, field_name, value)
    return save_object(session, obj)


def save_object(session, obj: Base) -> Base:
    session.add(obj)
    session.flush()
    session.refresh(obj)
    return obj


class AlchemyView(BaseAlchemyView):
    """
    AlchemyView creates a CRUD/REST interface for database objects.
    Basic usage:

    class FooView:
        prefix = "/foo"
        schema = FooSchema
        model = Foo

    Where `Foo` is a SQLAlchemy model and `FooSchema` a Pydantic model.
    """

    session: SessionDep  # type: ignore[reportIncompatibleVariableOverride]

    @get("/")
    def index(self, query_params: Any) -> Sequence[Any]:
        return self.process_index(query_params)

    def process_index(
        self,
        query_params: Any,
        query: sqlalchemy.Select[Any] | None = None,
    ) -> Sequence[Any]:
        """
        Handle a GET request on "/". This should return a list of objects.
        Accepts a query argument that can be used for narrowing down the selection.
        Feel free to override this method, e.g.:

            def process_index(self, query=None):
                query = make_my_query()
                objs = super().process_index(query)
                return add_my_info(objs)
        """
        if query is None:
            query = sqlalchemy.select(self.model)
        query = apply_query_modifiers(
            query_params,
            query,
            self.model,
            self.schema,
        )
        scalar_result = self.session.scalars(query)
        return scalar_result.all()

    @get("/{id}")
    def get(self, id: int) -> Any:
        return self.process_get(id)

    def process_get(self, id: int) -> Any:
        """
        Handle a GET request on "/{id}". This should return a single object.
        Return a 404 if not found.
        Feel free to override this method.
        """
        obj = self.session.get(self.model, id)
        if obj is None:
            raise fastapi.HTTPException(404)
        return obj

    @post("/")
    def post(
        self, schema_obj: BaseSchema
    ) -> Any:  # schema_obj type is set in before_include_view
        return self.process_post(schema_obj)

    def process_post(self, schema_obj: BaseSchema) -> Any:
        """
        Handle a POST request on "/". This should create a new object.
        Feel free to override this method.
        """
        obj = self.make_new_object(schema_obj)
        obj = self.save_object(obj)
        return obj

    @patch("/{id}")
    def patch(self, id: int, schema_obj: BaseSchema) -> Any:
        return self.process_patch(id, schema_obj)

    def process_patch(self, id: int, schema_obj: BaseSchema) -> Any:
        """
        Handle a PATCH request on "/{id}". This should partially update an existing
        object.
        Feel free to override this method.
        """
        obj = self.process_get(id)
        return self.update_object(obj, schema_obj)

    @delete("/{id}")
    def delete(self, id: int) -> fastapi.Response:
        return self.process_delete(id)

    def process_delete(self, id: int) -> fastapi.Response:
        obj = self.process_get(id)
        self.delete_object(obj)
        return fastapi.Response(status_code=204)

    def delete_object(self, obj: Base) -> None:
        """
        Delete an object from the database.
        Feel free to override this method.
        """
        self.session.delete(obj)
        self.session.flush()

    def make_new_object(self, schema_obj: BaseSchema) -> Base:
        """
        Create a new object from a schema object.
        Feel free to override this method.
        """
        return make_new_object(
            self.session, self.model, schema_obj, self.creation_schema
        )

    def update_object(self, obj: Base, schema_obj: BaseSchema) -> Base:
        """
        Update an existing object with data from a schema object.
        Feel free to override this method.
        """
        return update_object(self.session, obj, schema_obj, self.schema)

    def save_object(self, obj: Base) -> Base:
        """
        Save an object to the database.
        Feel free to override this method.
        """
        return save_object(self.session, obj)
