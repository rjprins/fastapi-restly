from typing import Any, Sequence

import fastapi
import sqlalchemy
from sqlalchemy import func, select
from sqlalchemy.orm import DeclarativeBase

from ..db import AsyncSessionDep
from ..query import apply_query_modifiers, use_query_modifier_version
from ..schemas import (
    BaseSchema,
    IDSchema,
    async_resolve_ids_to_sqlalchemy_objects,
    get_writable_inputs,
    is_readonly_field,
)
from ._base import BaseRestView, delete, get, patch, post


class AsyncRestView(BaseRestView):
    """
    AsyncRestView creates an async CRUD/REST interface for database objects.
    Basic usage::

        class FooView(AsyncRestView):
            prefix = "/foo"
            schema = FooSchema
            model = Foo

    Where ``Foo`` is a SQLAlchemy model and ``FooSchema`` a Pydantic model.
    """

    session: AsyncSessionDep

    @get("/")
    async def index(self, query_params: Any) -> Any:
        objs = await self.on_list(query_params)
        if not self.include_pagination_metadata:
            return [self.to_response_schema(obj) for obj in objs]

        total = await self.count_index(query_params)
        return self._build_pagination_payload(query_params, objs, total)

    async def on_list(
        self,
        query_params: Any,
        query: sqlalchemy.Select[Any] | None = None,
    ) -> Sequence[Any]:
        """
        Handle a GET request on "/". This should return a list of objects.
        Accepts a query argument that can be used for narrowing down the selection.
        Feel free to override this method, e.g.:

            async def on_list(self, query_params, query=None):
                query = make_my_query()
                objs = await super().on_list(query_params, query)
                return add_my_info(objs)
        """
        if query is None:
            query = sqlalchemy.select(self.model)
        loader_options = self.get_relationship_loader_options()
        if loader_options:
            query = query.options(*loader_options)
        query_params = self._to_query_params(query_params)

        with use_query_modifier_version(self.get_query_modifier_version()):
            query = apply_query_modifiers(
                query_params, query, self.model, self.schema
            )
        scalar_result = await self.session.scalars(query)
        return scalar_result.all()

    async def count_index(self, query_params: Any) -> int:
        query_params = self._to_query_params(query_params)
        with use_query_modifier_version(self.get_query_modifier_version()):
            filtered_query = apply_query_modifiers(
                query_params, sqlalchemy.select(self.model), self.model, self.schema
            )
        filtered_query = filtered_query.order_by(None).limit(None).offset(None)
        count_query = select(func.count()).select_from(filtered_query.subquery())
        return int(await self.session.scalar(count_query) or 0)

    @get("/{id}")
    async def get(self, id: Any) -> Any:
        obj = await self.on_get(id)
        return self.to_response_schema(obj)

    async def on_get(self, id: Any) -> Any:
        """
        Handle a GET request on "/{id}". This should return a single object.
        Return a 404 if not found.
        Feel free to override this method.
        """
        loader_options = self.get_relationship_loader_options()
        obj = await self.session.get(self.model, id, options=loader_options)
        if obj is None:
            raise fastapi.HTTPException(404)
        return obj

    @post("/")
    async def post(
        self, schema_obj: BaseSchema
    ) -> Any:  # schema_obj type is set in before_include_view
        obj = await self.on_create(schema_obj)
        return self.to_response_schema(obj)

    async def on_create(self, schema_obj: BaseSchema) -> Any:
        """
        Handle a POST request on "/". This should create a new object.
        Feel free to override this method.
        """
        obj = await self.make_new_object(schema_obj)
        return await self.save_object(obj)

    @patch("/{id}")
    async def patch(self, id: Any, schema_obj: BaseSchema) -> Any:
        obj = await self.on_update(id, schema_obj)
        return self.to_response_schema(obj)

    async def on_update(self, id: Any, schema_obj: BaseSchema) -> Any:
        """
        Handle a PATCH request on "/{id}". This should partially update an existing
        object.
        Feel free to override this method.
        """
        obj = await self.on_get(id)
        return await self.update_object(obj, schema_obj)

    @delete("/{id}")
    async def delete(self, id: Any) -> fastapi.Response:
        return await self.on_delete(id)

    async def on_delete(self, id: Any) -> fastapi.Response:
        obj = await self.on_get(id)
        await self.delete_object(obj)
        return fastapi.Response(status_code=204)

    async def delete_object(self, obj: DeclarativeBase) -> None:
        """
        Handle a DELETE request on "/{id}". This should delete an object from the
        database. `on_get()` is called first to lookup the object.
        Feel free to override this method.
        """
        await self.session.delete(obj)
        await self.session.flush()

    async def make_new_object(self, schema_obj: BaseSchema) -> DeclarativeBase:
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
            if isinstance(value, IDSchema) and field_name.endswith("_id"):
                data[field_name] = value.id
                continue
            if isinstance(value, DeclarativeBase) and field_name.endswith("_id"):
                data[field_name] = value.id
                relation_name = field_name[:-3]
                if hasattr(self.model, relation_name):
                    data[relation_name] = value
                continue
            data[field_name] = value

        obj = self.model(**data)
        self.session.add(obj)
        return obj

    async def update_object(self, obj: DeclarativeBase, schema_obj: BaseSchema) -> DeclarativeBase:
        """
        Update an existing object with data from a schema object.
        Feel free to override this method.
        """
        await async_resolve_ids_to_sqlalchemy_objects(self.session, schema_obj)
        for field_name, value in get_writable_inputs(schema_obj, self.schema).items():
            if isinstance(value, IDSchema) and field_name.endswith("_id"):
                setattr(obj, field_name, value.id)
                continue
            if isinstance(value, DeclarativeBase) and field_name.endswith("_id"):
                setattr(obj, field_name, value.id)
                relation_name = field_name[:-3]
                if hasattr(obj, relation_name):
                    setattr(obj, relation_name, value)
                continue
            setattr(obj, field_name, value)
        return await self.save_object(obj)

    async def save_object(self, obj: DeclarativeBase) -> DeclarativeBase:
        """
        Save an object to the database.
        Feel free to override this method.
        """
        await self.session.flush()
        await self.session.refresh(obj)
        return obj
