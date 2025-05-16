from datetime import datetime
from typing import ClassVar, Generic, Optional, TypeVar

import pydantic
from pydantic.fields import FieldInfo
from sqlalchemy import select
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm.exc import NoResultFound


class BaseSchema(pydantic.BaseModel):
    # XXX: Explain how users can use their own pydantic BaseModel and config
    # model_config: ClassVar = pydantic.ConfigDict(
    #     alias_generator=to_camel,
    #     populate_by_name=True,
    #     from_attributes=True,
    # )
    # XXX: Collect read_only_fields by MRO
    read_only_fields: ClassVar[list[str]] = []


class TimestampsSchemaMixin(pydantic.BaseModel):
    read_only_fields: ClassVar[list[str]] = ["created_at", "updated_at"]
    created_at: datetime
    updated_at: datetime


SQLAlchemyModel = TypeVar("SQLAlchemyModel", bound=DeclarativeBase)


class IDSchema(BaseSchema, Generic[SQLAlchemyModel]):
    """Generic schema useful for serializing only the id of objects.
    Can be used as IDSchema[MyModel].
    """

    read_only_fields: ClassVar[list[str]] = ["id"]

    id: int

    def get_sql_model_annotation(self) -> SQLAlchemyModel | None:
        """
        Return the annotation on IDSchema when used as:

        foo: IDSchema[Foo]

        This property will return "Foo".
        """
        try:
            return self.__pydantic_generic_metadata__["args"][0]
        except Exception:
            return None


async def async_resolve_ids_to_sqlalchemy_objects(
    schema_obj: BaseSchema, session
) -> None:
    """
    Go over the Pydantic fields and turn any IDSchema objects into SQLAlchemy instances.
    A database request is made for each IDSchema to look up the related row in the database.
    If an id is not found in the database `sqlalchemy.orm.exc.NoResultFound` is raised.
    """
    # Go over all Pydantic fields and check if any of them are an IDSchema object or
    # a list of IDSchema objects.
    for field in schema_obj.model_fields_set:
        value = getattr(schema_obj, field, None)

        if isinstance(value, IDSchema):
            sql_model = value.get_sql_model_annotation()
            if not sql_model:
                continue

            # Replace the IDSchema object with a SQLAlchemy instance from the database
            sql_model_obj = await session.get_one(sql_model, value.id)
            setattr(schema_obj, field, sql_model_obj)

        elif isinstance(value, list) and any(isinstance(i, IDSchema) for i in value):
            # Assume all IdSchemas are for the same model
            sql_model = value[0].get_sql_model_annotation()
            if not sql_model:
                continue

            # Replace all IDSchema objects with SQLAlchemy instances
            ids = [obj.id for obj in value]
            query = select(sql_model).where(sql_model.id.in_(ids))
            sql_model_objs = list(await session.scalars(query))

            if len(ids) != len(sql_model_objs):
                missing_ids = set(ids).difference(o.id for o in sql_model_objs)
                raise NoResultFound(f"Id not found for {field}: {missing_ids}")

            setattr(schema_obj, field, sql_model_objs)


def resolve_ids_to_sqlalchemy_objects(schema_obj: BaseSchema, session) -> None:
    """
    Go over the Pydantic fields and turn any IDSchema objects into SQLAlchemy instances.
    A database request is made for each IDSchema to look up the related row in the database.
    If an id is not found in the database `sqlalchemy.orm.exc.NoResultFound` is raised.
    """
    # Go over all Pydantic fields and check if any of them are an IDSchema object or
    # a list of IDSchema objects.
    for field in schema_obj.model_fields_set:
        value = getattr(schema_obj, field, None)

        if isinstance(value, IDSchema):
            sql_model = value.get_sql_model_annotation()
            if not sql_model:
                continue

            # Replace the IDSchema object with a SQLAlchemy instance from the database
            sql_model_obj = session.get_one(sql_model, value.id)
            setattr(schema_obj, field, sql_model_obj)

        elif isinstance(value, list) and any(isinstance(i, IDSchema) for i in value):
            # Assume all IdSchemas are for the same model
            sql_model = value[0].get_sql_model_annotation()
            if not sql_model:
                continue

            # Replace all IDSchema objects with SQLAlchemy instances
            ids = [obj.id for obj in value]
            query = select(sql_model).where(sql_model.id.in_(ids))
            sql_model_objs = list(session.scalars(query))

            if len(ids) != len(sql_model_objs):
                missing_ids = set(ids).difference(o.id for o in sql_model_objs)
                raise NoResultFound(f"Id not found for {field}: {missing_ids}")

            setattr(schema_obj, field, sql_model_objs)


def create_model_without_read_only_fields(
    model_cls: type[pydantic.BaseModel],
) -> type[pydantic.BaseModel]:
    """
    Copy the given pydantic model class, but with fields with names in
    'read_only_fields' removed.
    """
    bases: list[type] = []
    if hasattr(model_cls, "__orig_bases__"):
        orig_bases = model_cls.__orig_bases__
    else:
        orig_bases = model_cls.__bases__
    for base_cls in orig_bases:
        if hasattr(base_cls, "read_only_fields"):
            new_base_cls = create_model_without_read_only_fields(base_cls)
            bases.append(new_base_cls)
        else:
            bases.append(base_cls)

    new_model_name = "Create" + model_cls.__name__
    doc = model_cls.__doc__ or "" + "\nRead-only have been fields removed."
    writable_fields = _get_writable_field_definitions(model_cls)

    # Ignore mypy because Pydantic typing on __base__ is too strict
    new_model_cls = pydantic.create_model(  # type: ignore
        new_model_name,
        __doc__=doc,
        __base__=tuple(bases),
        __module__=model_cls.__module__,
        **writable_fields,
    )
    return new_model_cls


# NOT_SET is used as a sentinel value for validating incoming data. It is the default
# value for update_schemas and allows us to see the difference between 'None' and
# 'not submitted'. A string is used so it renders nicely in /docs and /redoc.
NOT_SET = "Partial PUT does not support default values"


def create_model_with_optional_fields(model_cls: type[BaseSchema]) -> type[BaseSchema]:
    """
    Create a new pydantic model/schema where all writable fields (those not mentioned
    in `read_only_fields`) are made optional. The field defaults are set or replaced
    with the `NOT_SET` object. `NOT_SET` is used for partial updates to prevent
    replacing existing data with default data.
    """
    new_model_name = "Update" + model_cls.__name__
    doc = (
        model_cls.__doc__
        or "" + "\nRead-only have been fields removed and all fields are optional"
    )
    writable_fields = _get_writable_field_definitions(model_cls)
    optional_fields: dict[str, tuple] = {}
    for name, (annotation, field_info) in writable_fields.items():
        field_with_default = FieldInfo.merge_field_infos(field_info, default=NOT_SET)
        optional_fields[name] = (Optional[annotation], field_with_default)

    # Ignore mypy because Pydantic typing on __base__ is too strict
    new_model_cls = pydantic.create_model(  # type: ignore
        new_model_name,
        __doc__=doc,
        __base__=model_cls.__bases__,
        __module__=model_cls.__module__,
        **optional_fields,
    )
    return new_model_cls


def _get_writable_field_definitions(model_cls: type[BaseSchema]) -> dict[str, tuple]:
    """
    Return fields from a Pydantic model that are not mentioned in `read_only_fields`.
    Field definitions are returned in the form suitable for `pydantic.create_model()`.
    See https://docs.pydantic.dev/latest/api/base_model/#pydantic.create_model
    """
    read_only_fields: set[str] = set()
    for cls in model_cls.mro():
        if "read_only_fields" in cls.__dict__:
            read_only_fields.update(cls.__dict__["read_only_fields"])
    writable_fields: dict[str, tuple] = {}
    for field_name, field_info in model_cls.model_fields.items():
        if field_name not in read_only_fields:
            writable_fields[field_name] = (field_info.annotation, field_info)
    return writable_fields


def getattrs(obj, *attrs, default=None):
    """
    Try access a chain of attributes and return the default if any of the attrs is not defined.
    """
    for attr in attrs:
        if not hasattr(obj, attr):
            return default
        obj = getattr(obj, attr)
    return obj
