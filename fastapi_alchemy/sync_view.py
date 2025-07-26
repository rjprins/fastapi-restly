from typing import Any, Sequence

import fastapi
import pydantic
import sqlalchemy

from ._session import DBDependency, generate_session
from .query_modifiers import apply_query_modifiers
from .schemas import NOT_SET, resolve_ids_to_sqlalchemy_objects
from .sqlbase import SQLBase
from .views import AsyncAlchemyView, route


def make_new_object(session, model_cls, schema_obj):
    resolve_ids_to_sqlalchemy_objects(schema_obj, session)
    obj = model_cls(**dict(schema_obj))
    session.add(obj)
    return obj


class AlchemyView(AsyncAlchemyView):
    """
    AsyncAlchemyView creates a CRUD/REST interface for database objects.
    Basic usage:

    class FooView:
        prefix = "/foo"
        schema = FooSchema
        model = Foo

    Where `Foo` is a SQLAlchemy model and `FooSchema` a Pydantic model.
    """

    db: DBDependency  # type: ignore[reportIncompatibleVariableOverride]

    @route("/")
    def index(self, query_params):
        return self.process_index()

    def process_index(self, query: sqlalchemy.Select | None = None) -> Sequence[Any]:
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
            # XXX: Ideally use query_params argument instead of request.query_params
            self.request.query_params,
            query,
            self.model,
            self.schema,
        )
        scalar_result = self.db.scalars(query)
        return scalar_result.all()

    @route("/{id}")
    def get(self, id: int):
        return self.process_get(id)

    def process_get(self, id: int) -> Any:
        """
        Handle a GET request on "/{id}". This should return a single object.
        Return a 404 if not found.
        Feel free to override this method.
        """
        obj = self.db.get(self.model, id)
        if obj is None:
            raise fastapi.HTTPException(404)
        return obj

    @route("/", methods=["POST"], status_code=201)
    def post(self, schema_obj):  # schema_obj type is set in before_include_view
        return self.process_post(schema_obj)

    def process_post(self, schema_obj) -> Any:
        """
        Handle a POST request on "/". This should create a new object.
        Feel free to override this method.
        """
        obj = self.make_new_object(schema_obj)
        obj = self.save_object(obj)
        return obj

    @route("/{id}", methods=["PUT"])
    def put(self, id: int, schema_obj):
        return self.process_put(id, schema_obj)

    def process_put(self, id, schema_obj) -> Any:
        """
        Handle a PUT request on "/{id}". This should (partially) update an existing
        object.
        Feel free to override this method.
        """
        obj = self.process_get(id)
        return self.update_object(obj, schema_obj)

    @route("/{id}", methods=["DELETE"], status_code=204)
    def delete(self, id: int):
        return self.process_delete(id)

    def process_delete(self, id: int) -> fastapi.Response:
        obj = self.process_get(id)
        self.delete_object(obj)
        return fastapi.Response(status_code=204)

    def delete_object(self, obj: SQLBase) -> None:
        """
        Handle a DELETE request on "/{id}". This should delete an object from the
        database. `process_get()` is called first to lookup the object.
        Feel free to override this method.
        """
        self.db.delete(obj)
        self.db.flush()

    def make_new_object(self, schema_obj):
        return make_new_object(self.db, self.model, schema_obj)

    def update_object(self, obj, schema_obj):
        resolve_ids_to_sqlalchemy_objects(schema_obj, self.db)
        for field_name, value in schema_obj:
            if value is NOT_SET:
                continue
            # `read_only_fields` are removed when using
            # `create_model_without_read_only_fields()` but if a custom
            # `creation_schema` is used this might still be needed.
            if field_name in self.schema.read_only_fields:
                continue
            setattr(obj, field_name, value)
        return self.save_object(obj)

    def save_object(self, obj):
        self.db.add(obj)
        self.db.flush()
        self.db.refresh(obj)
        return obj
