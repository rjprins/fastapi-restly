from typing import Any, Sequence

import fastapi
import pydantic
import sqlalchemy

from ._query_modifiers_config import apply_query_modifiers
from ._schemas import (
    NOT_SET,
    BaseSchema,
    async_resolve_ids_to_sqlalchemy_objects,
    get_writable_inputs,
    is_readonly_field,
)
from ._session import AsyncSessionDep
from ._sqlbase import Base
from ._views import BaseAlchemyView, delete, get, post, put


class AsyncAlchemyView(BaseAlchemyView):
    """
    AsyncAlchemyView creates a CRUD/REST interface for database objects.
    Basic usage:

    class FooView:
        prefix = "/foo"
        schema = FooSchema
        model = Foo

    Where `Foo` is a SQLAlchemy model and `FooSchema` a Pydantic model.
    """

    session: AsyncSessionDep

    @get("/")
    async def index(self, query_params: Any) -> Sequence[Any]:
        return await self.process_index(query_params)

    async def process_index(
        self,
        query_params: pydantic.BaseModel,
        query: sqlalchemy.Select[Any] | None = None,
    ) -> Sequence[Any]:
        """
        Handle a GET request on "/". This should return a list of objects.
        Accepts a query argument that can be used for narrowing down the selection.
        Feel free to override this method, e.g.:

            async def process_index(self, query=None):
                query = make_my_query()
                objs = await super.process_index(query)
                return add_my_info(objs)
        """
        if query is None:
            query = sqlalchemy.select(self.model)
        query = apply_query_modifiers(
            self.request.query_params, query, self.model, self.schema
        )
        scalar_result = await self.session.scalars(query)
        return scalar_result.all()

    @get("/{id}")
    async def get(self, id: int) -> Any:
        return await self.process_get(id)

    async def process_get(self, id: int) -> Any:
        """
        Handle a GET request on "/{id}". This should return a single object.
        Return a 404 if not found.
        Feel free to override this method.
        """
        obj = await self.session.get(self.model, id)
        if obj is None:
            raise fastapi.HTTPException(404)
        return obj

    @post("/")
    async def post(
        self, schema_obj: BaseSchema
    ) -> Any:  # schema_obj type is set in before_include_view
        return await self.process_post(schema_obj)

    async def process_post(self, schema_obj: BaseSchema) -> Any:
        """
        Handle a POST request on "/". This should create a new object.
        Feel free to override this method.
        """
        obj = await self.make_new_object(schema_obj)
        return await self.save_object(obj)

    @put("/{id}")
    async def put(self, id: int, schema_obj: BaseSchema) -> Any:
        return await self.process_put(id, schema_obj)

    async def process_put(self, id: int, schema_obj: BaseSchema) -> Any:
        """
        Handle a PUT request on "/{id}". This should (partially) update an existing
        object.
        Feel free to override this method.
        """
        obj = await self.process_get(id)
        return await self.update_object(obj, schema_obj)

    @delete("/{id}")
    async def delete(self, id: int) -> fastapi.Response:
        return await self.process_delete(id)

    async def process_delete(self, id: int) -> fastapi.Response:
        obj = await self.process_get(id)
        await self.delete_object(obj)
        return fastapi.Response(status_code=204)

    async def delete_object(self, obj: Base) -> None:
        """
        Handle a DELETE request on "/{id}". This should delete an object from the
        database. `process_get()` is called first to lookup the object.
        Feel free to override this method.
        """
        await self.session.delete(obj)
        await self.session.flush()

    async def make_new_object(self, schema_obj: BaseSchema) -> Base:
        """
        Create a new object from a schema object.
        Feel free to override this method.
        """
        await async_resolve_ids_to_sqlalchemy_objects(self.session, schema_obj)

        # Filter out read-only fields when creating the object
        data = {}
        for field_name, value in schema_obj:
            is_readonly = is_readonly_field(self.schema, field_name)
            if is_readonly:
                continue
            data[field_name] = value

        obj = self.model(**data)
        self.session.add(obj)
        return obj

    async def update_object(self, obj: Base, schema_obj: BaseSchema) -> Base:
        """
        Update an existing object with data from a schema object.
        Feel free to override this method.
        """
        await async_resolve_ids_to_sqlalchemy_objects(self.session, schema_obj)
        for field_name, value in get_writable_inputs(schema_obj, self.schema).items():
            setattr(obj, field_name, value)
        return await self.save_object(obj)

    async def save_object(self, obj: Base) -> Base:
        """
        Save an object to the database.
        Feel free to override this method.
        """
        await self.session.flush()
        await self.session.refresh(obj)
        return obj
