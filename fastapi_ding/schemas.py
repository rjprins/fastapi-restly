import types
from datetime import datetime
from typing import Annotated, Any, ClassVar, Generic, TypeVar

import pydantic
from sqlalchemy import select
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.exc import NoResultFound


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
T = TypeVar("T")


class ReadOnly:
    """
    A class that can be used with square brackets to mark fields as read-only.
    Example:
        class UserSchema(IDSchema[User]):
            name: str
            email: str
            id: ReadOnly[int]  # This field is read-only
    """
    
    def __getitem__(self, t: type[T]) -> Annotated[T, "readonly"]:
        return Annotated[t, "readonly"]


# Create a singleton instance
ReadOnly = ReadOnly()


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


class IDStampsSchema(TimestampsSchemaMixin, IDSchema[SQLAlchemyModel]):
    pass


async def async_resolve_ids_to_sqlalchemy_objects(
    schema_obj: BaseSchema, session: Any
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


def resolve_ids_to_sqlalchemy_objects(schema_obj: BaseSchema, session: Any) -> None:
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


def get_read_only_fields(model_cls: type[pydantic.BaseModel]) -> set[str]:
    """
    Get all read-only fields from a model, including those from class-level read_only_fields
    and those marked with ReadOnly (Annotated with 'readonly').
    """
    read_only_fields: set[str] = set()
    # Get read-only fields from class-level read_only_fields
    for cls in model_cls.mro():
        if "read_only_fields" in cls.__dict__:
            read_only_fields.update(cls.__dict__["read_only_fields"])
    # Get read-only fields from Annotated metadata
    for field_name, field_info in model_cls.model_fields.items():
        if getattr(field_info, "metadata", None) and "readonly" in field_info.metadata:
            read_only_fields.add(field_name)
    return read_only_fields


def create_model_without_read_only_fields(
    model_cls: type[pydantic.BaseModel],
    raise_on_readonly: bool = False,
) -> type[pydantic.BaseModel]:
    """
    Create a new pydantic model/schema where all writable fields (those not mentioned
    in `read_only_fields`) are preserved, and read-only fields are made optional.
    This preserves all validators, config, and other functionality.
    
    Args:
        model_cls: The original model class
        raise_on_readonly: If True, raise an error when read-only fields are provided
    """
    new_model_name = "Create" + model_cls.__name__
    doc = model_cls.__doc__ or "" + "\nRead-only fields are optional and ignored."
    
    # Get read-only fields from the original model
    read_only_fields = get_read_only_fields(model_cls)
    
    # Create field overrides for read-only fields to make them optional
    field_overrides = {}
    for field_name in read_only_fields:
        if field_name in model_cls.model_fields:
            field_info = model_cls.model_fields[field_name]
            # Make the field optional with None as default
            from typing import Optional
            field_overrides[field_name] = (Optional[field_info.annotation], None)
    
    # Create a new model by inheriting from the original
    # This preserves all validators, config, and other functionality
    new_model_cls = pydantic.create_model(  # type: ignore
        new_model_name,
        __doc__=doc,
        __base__=(model_cls,),
        **field_overrides,
    )
    
    return new_model_cls


def rebase_with_model_config(
    base: tuple[type, ...], model_cls: type[pydantic.BaseModel]
) -> type[pydantic.BaseModel]:
    def class_body(ns: dict[str, Any]) -> None:
        ns["model_config"] = model_cls.model_config.copy()

    return types.new_class(
        f"{model_cls.__name__}ModelConfig", base, exec_body=class_body
    )


# NOT_SET is used as a sentinel value for validating incoming data. It is the default
# value for update_schemas and allows us to see the difference between 'None' and
# 'not submitted'. A string is used so it renders nicely in /docs and /redoc.
NOT_SET = "Partial PUT does not support default values"


def create_model_with_optional_fields(model_cls: type[BaseSchema], raise_on_readonly: bool = False) -> type[BaseSchema]:
    """
    Create a new pydantic model/schema where all writable fields (those not mentioned
    in `read_only_fields`) are made optional. The field defaults are set or replaced
    with the `NOT_SET` object. `NOT_SET` is used for partial updates to prevent
    replacing existing data with default data.
    
    Args:
        model_cls: The original model class
        raise_on_readonly: If True, raise an error when read-only fields are provided
    """
    new_model_name = "Update" + model_cls.__name__
    doc = (
        model_cls.__doc__
        or "" + "\nRead-only have been fields removed and all fields are optional"
    )
    
    # Get read-only fields from the original model
    read_only_fields = get_read_only_fields(model_cls)
    
    # Create field overrides for read-only fields to make them optional
    field_overrides = {}
    for field_name in read_only_fields:
        if field_name in model_cls.model_fields:
            field_info = model_cls.model_fields[field_name]
            # Make the field optional with None as default
            from typing import Optional
            field_overrides[field_name] = (Optional[field_info.annotation], None)
    
    # Create a new model by inheriting from the original
    # This preserves all validators, config, and other functionality
    new_model_cls = pydantic.create_model(  # type: ignore
        new_model_name,
        __doc__=doc,
        __base__=(model_cls,),
        **field_overrides,
    )
    
    return new_model_cls


def _get_writable_field_definitions(
    model_cls: type[pydantic.BaseModel],
) -> dict[str, tuple[Any, Any]]:
    """
    Return fields from a Pydantic model that are not mentioned in `read_only_fields`.
    Field definitions are returned in the form suitable for `pydantic.create_model()`.
    See https://docs.pydantic.dev/latest/api/base_model/#pydantic.create_model
    """
    read_only_fields = get_read_only_fields(model_cls)
    writable_fields: dict[str, tuple[Any, Any]] = {}
    for field_name, field_info in model_cls.model_fields.items():
        if field_name not in read_only_fields:
            writable_fields[field_name] = (field_info.annotation, field_info)
    return writable_fields


def getattrs(obj: Any, *attrs: str, default: Any = None) -> Any:
    """
    Try access a chain of attributes and return the default if any of the attrs is not defined.
    """
    for attr in attrs:
        if not hasattr(obj, attr):
            return default
        obj = getattr(obj, attr)
    return obj
