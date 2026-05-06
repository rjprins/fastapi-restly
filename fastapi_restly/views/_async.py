from typing import Any, TypeVar, cast

import fastapi
import pydantic
import sqlalchemy
from sqlalchemy import func, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

from ..db import AsyncSessionDep
from ..query import apply_list_params
from ..schemas._base import _async_resolve_ids_to_sqlalchemy_objects
from ._base import (
    BaseRestView,
    CreateSchemaT,
    IdT,
    ListingResult,
    ModelT,
    SchemaT,
    UpdateSchemaT,
    apply_create_assignments,
    apply_update_to_object,
    build_create_plan,
    delete,
    get,
    patch,
    post,
    validate_resolved_reference_consistency,
)

T = TypeVar("T", bound=DeclarativeBase)


async def async_make_new_object(
    session: AsyncSession,
    model_cls: type[T],
    schema_obj: pydantic.BaseModel,
    schema_cls: type[pydantic.BaseModel] | None = None,
) -> T:
    """Async equivalent of :func:`fastapi_restly.views.make_new_object`.

    Create a new instance of ``model_cls`` from ``schema_obj`` and add it to
    ``session``. Read-only fields and any unset fields' defaults are handled by
    the shared helper. The session is not flushed here.

    **Structural-only.** This helper applies the schema-to-ORM mapping only;
    it does not run any view-layer business logic. Anything schema-derived
    (hashing, slug derivation, denormalised rollups) should be applied by the
    caller. See ``docs/howto_compose_views_with_mixins.md`` for the rule.
    """
    await _async_resolve_ids_to_sqlalchemy_objects(session, schema_obj)
    validate_resolved_reference_consistency(model_cls, schema_obj, schema_cls)
    create_plan = build_create_plan(model_cls, schema_obj, schema_cls)
    obj = model_cls(**create_plan.kwargs)
    apply_create_assignments(obj, create_plan.post_assignments)
    # AsyncSession.add() is synchronous: it only stages the object. Database
    # I/O happens later at the explicit await session.flush()/refresh() boundary.
    session.add(obj)
    return obj


async def async_update_object(
    session: AsyncSession,
    obj: DeclarativeBase,
    schema_obj: pydantic.BaseModel,
    schema_cls: type[pydantic.BaseModel] | None = None,
) -> DeclarativeBase:
    """Async equivalent of :func:`fastapi_restly.views.update_object`.

    Apply writable inputs from ``schema_obj`` onto ``obj``. Only fields the
    caller explicitly set are applied; read-only fields are skipped.

    **Structural-only.** This helper applies the schema-to-ORM mapping only;
    it does not run any view-layer business logic. Anything schema-derived
    (hashing, slug derivation, denormalised rollups) should be applied by the
    caller. See ``docs/howto_compose_views_with_mixins.md`` for the rule.
    """
    await _async_resolve_ids_to_sqlalchemy_objects(session, schema_obj)
    validate_resolved_reference_consistency(type(obj), schema_obj, schema_cls)
    apply_update_to_object(obj, schema_obj, schema_cls)
    return obj


async def async_save_object(
    session: AsyncSession, obj: DeclarativeBase
) -> DeclarativeBase:
    """Async equivalent of :func:`fastapi_restly.views.save_object`.

    Flush the session and refresh ``obj`` so its server-side defaults and
    generated columns (PKs, timestamps, etc.) are populated.
    """
    await session.flush()
    await session.refresh(obj)
    return obj


class AsyncRestView(BaseRestView[ModelT, SchemaT, CreateSchemaT, UpdateSchemaT, IdT]):
    """
    AsyncRestView creates an async CRUD/REST interface for database objects.
    Basic usage::

        class FooView(AsyncRestView):
            prefix = "/foo"
            schema = FooRead
            model = Foo

    Where ``Foo`` is a SQLAlchemy model and ``FooRead`` a Pydantic model.
    """

    session: AsyncSessionDep

    @get("/")
    async def listing(self, query_params: Any) -> Any:
        self._reject_unknown_query_params()
        listing_result = await self.perform_listing(query_params)
        return self.to_listing_response(query_params, listing_result)

    def build_query(self) -> sqlalchemy.Select[Any]:
        """
        Return the base SQLAlchemy ``Select`` used by every read on this
        view's model — listing, count, and retrieve. Override to add
        ``WHERE`` clauses that should apply to all of them — e.g. tenant
        scoping, soft-delete filtering, row-level permission visibility.
        Call ``super().build_query()`` and chain ``.where(...)`` to compose
        with any base-class or mixin filters.

        Because retrieve also routes through this query, a row hidden from
        listing cannot be fetched directly via ``GET /{id}`` — visibility
        stays consistent across endpoints by construction.
        """
        return sqlalchemy.select(self.model)

    async def perform_listing(self, query_params: Any) -> ListingResult[ModelT]:
        """
        Handle a GET request on "/". This should return listed objects and the
        total count before pagination.
        Feel free to override this method, e.g.:

            async def perform_listing(self, query_params):
                result = await super().perform_listing(query_params)
                return ListingResult(add_my_info(result.objects), result.total_count)

        ``query_params`` is the validated query-parameter Pydantic model
        injected by FastAPI; pagination bounds (``page`` / ``page_size``)
        have already been validated by the schema returned from
        :func:`fastapi_restly.query.create_list_params_schema`.

        For WHERE-clause-only filtering that should also apply to the
        pagination total *and* to retrieve, override :meth:`build_query`
        instead.
        """
        query = self.build_query()
        query = apply_list_params(query_params, query, self.model, self.schema)
        total_count = await self.count_listing(query)
        loader_options = self.get_relationship_loader_options()
        if loader_options:
            query = query.options(*loader_options)

        scalar_result = await self.session.scalars(query)
        return ListingResult(objects=scalar_result.all(), total_count=total_count)

    async def count_listing(self, query: sqlalchemy.Select[Any]) -> int:
        count_source = query.order_by(None).limit(None).offset(None)
        count_query = select(func.count()).select_from(count_source.subquery())
        return int(await self.session.scalar(count_query) or 0)

    @get("/{id}")
    async def get(self, id: Any) -> Any:
        obj = await self.perform_get(id)
        return self.to_response_schema(obj)

    async def perform_get(self, id: IdT) -> ModelT:
        """
        Handle a GET request on "/{id}". This should return a single object.
        Return a 404 if not found.

        Routes through :meth:`build_query`, so any read-side filters layered
        there (tenant scoping, soft-delete, row-level permissions) apply to
        retrieve as well — a row hidden from listing returns 404 here too,
        without a separate post-fetch guard.
        """
        pk_cols = sa_inspect(self.model).primary_key
        if len(pk_cols) != 1:
            raise NotImplementedError(
                f"{self.model.__name__} has a composite primary key; "
                "override perform_get to fetch it."
            )
        query = self.build_query().where(pk_cols[0] == id)
        loader_options = self.get_relationship_loader_options()
        if loader_options:
            query = query.options(*loader_options)
        obj = (await self.session.scalars(query)).first()
        if obj is None:
            raise fastapi.HTTPException(
                status_code=404,
                detail=f"{self.model.__name__} with id {id!r} was not found",
            )
        return cast(ModelT, obj)

    @post("/")
    async def create(self, schema_obj: Any) -> Any:
        obj = await self.perform_create(schema_obj)
        return self.to_response_schema(obj)

    async def perform_create(self, schema_obj: CreateSchemaT) -> ModelT:
        """
        Handle a POST request on "/". This should create a new object.
        Feel free to override this method.
        """
        obj = await self.make_new_object(schema_obj)
        return await self.save_object(obj)

    @patch("/{id}")
    async def update(self, id: Any, schema_obj: Any) -> Any:
        obj = await self.perform_update(id, schema_obj)
        return self.to_response_schema(obj)

    async def perform_update(self, id: IdT, schema_obj: UpdateSchemaT) -> ModelT:
        """
        Handle a PATCH request on "/{id}". This should partially update an existing
        object.
        Feel free to override this method.
        """
        obj = await self.perform_get(id)
        obj = await self.update_object(obj, schema_obj)
        return await self.save_object(obj)

    @delete("/{id}")
    async def delete(self, id: Any) -> fastapi.Response:
        return await self.perform_delete(id)

    async def perform_delete(self, id: IdT) -> fastapi.Response:
        obj = await self.perform_get(id)
        await self.delete_object(obj)
        return fastapi.Response(status_code=204)

    async def delete_object(self, obj: ModelT) -> None:
        """
        Delete ``obj`` and flush the session.

        ``perform_delete`` calls ``perform_get`` first, so this method receives an
        existing object. Override it to change the deletion mechanics, for
        example to implement soft-delete.
        """
        await self.session.delete(obj)
        await self.session.flush()

    async def make_new_object(self, schema_obj: CreateSchemaT) -> ModelT:
        """
        Build a new ORM object from ``schema_obj`` and add it to the session.

        This does not flush. The default ``perform_create`` calls
        ``save_object`` afterwards; override this method for construction-time
        changes that must happen before that save boundary.

        **Structural-only intent.** Override for stamping or scoping with
        server-controlled fields (audit columns, tenant id, soft-delete
        flags). Schema-derived computation (hashing, slug derivation,
        denormalised rollups) belongs in ``perform_create`` instead. See
        ``docs/howto_compose_views_with_mixins.md`` for the rule.
        """
        model_cls = cast(type[ModelT], self.model)
        return await async_make_new_object(
            self.session, model_cls, schema_obj, self.schema
        )

    async def update_object(self, obj: ModelT, schema_obj: UpdateSchemaT) -> ModelT:
        """
        Apply writable fields from ``schema_obj`` to ``obj``.

        This does not flush. The default ``perform_update`` calls
        ``save_object`` afterwards; override this method for update-time changes
        that must happen before that save boundary.

        **Structural-only intent.** Override for stamping or scoping with
        server-controlled fields (audit columns, tenant id, soft-delete
        flags). Schema-derived computation (hashing, slug derivation,
        denormalised rollups) belongs in ``perform_update`` instead. See
        ``docs/howto_compose_views_with_mixins.md`` for the rule.
        """
        updated_obj = await async_update_object(
            self.session, obj, schema_obj, self.schema
        )
        return cast(ModelT, updated_obj)

    async def save_object(self, obj: ModelT) -> ModelT:
        """
        Flush the session and refresh ``obj`` from the database.

        This is the explicit persistence boundary used by the default create and
        update handlers. Override it for behavior that should run after every
        successful create/update flush.
        """
        return cast(ModelT, await async_save_object(self.session, obj))
