import types
from datetime import datetime
from typing import Annotated, Any, Generic, Optional, TypeVar

import pydantic
from pydantic.fields import Field, FieldInfo
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm.session import Session as SA_Session


class BaseSchema(pydantic.BaseModel):
    # TODO: Is this still needed?
    pass


class _Marker:
    def __init__(self, name: str):
        self.name = name

    def __repr__(self):
        return f"fd._Marker('{self.name}'>"


readonly_marker = _Marker("ReadOnly")
writeonly_marker = _Marker("WriteOnly")

_T = TypeVar("_T")

ReadOnly = Annotated[_T, readonly_marker, Field(json_schema_extra={"readOnly": True})]
WriteOnly = Annotated[
    _T, writeonly_marker, Field(json_schema_extra={"writeOnly": True})
]


class TimestampsSchemaMixin(pydantic.BaseModel):
    created_at: ReadOnly[datetime]
    updated_at: ReadOnly[datetime]


SQLAlchemyModel = TypeVar("SQLAlchemyModel", bound=DeclarativeBase)


class IDSchema(BaseSchema, Generic[SQLAlchemyModel]):
    """Generic schema useful for serializing only the id of objects.
    Can be used as IDSchema[MyModel].
    """

    id: ReadOnly[int]

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
    session: SA_Session, schema_obj: BaseSchema
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


def resolve_ids_to_sqlalchemy_objects(
    session: SA_Session, schema_obj: BaseSchema
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


def get_read_only_fields(model_cls: type[pydantic.BaseModel]) -> list[str]:
    """Get all fields from a model annotated as ReadOnly[]"""
    read_only_fields: list[str] = []
    # Get read-only fields from Annotated metadata
    for field_name, field_info in model_cls.model_fields.items():
        metadata = getattr(field_info, "metadata", None)
        if metadata and readonly_marker in metadata:
            read_only_fields.append(field_name)
    return read_only_fields


def is_readonly_field(
    model: pydantic.BaseModel | type[pydantic.BaseModel], field_name: str
) -> bool:
    """Check if a specific field is marked as readonly."""
    if isinstance(model, pydantic.BaseModel):
        model = model.__class__
    field_info = model.model_fields.get(field_name)
    return _is_readonly(field_info)


def _is_readonly(field_info: FieldInfo | None) -> bool:
    if field_info is None:
        return False
    metadata = getattr(field_info, "metadata", None)
    if not metadata:
        return False
    return readonly_marker in metadata


def get_write_only_fields(model_cls: type[pydantic.BaseModel]) -> list[str]:
    """Get all fields from a model annotated as WriteOnly[]"""
    write_only_fields: list[str] = []
    # Get write-only fields from Annotated metadata
    for field_name, field_info in model_cls.model_fields.items():
        metadata = getattr(field_info, "metadata", None)
        if metadata and writeonly_marker in metadata:
            write_only_fields.append(field_name)
    return write_only_fields


def is_field_writeonly(model_cls: type[pydantic.BaseModel], field_name: str) -> bool:
    """Check if a specific field is marked as writeonly."""
    field_info = model_cls.model_fields.get(field_name)
    if field_info is None:
        return False
    metadata = getattr(field_info, "metadata", None)
    if not metadata:
        return False
    return writeonly_marker in metadata


def create_model_without_read_only_fields(
    model_cls: type[pydantic.BaseModel],
) -> type[pydantic.BaseModel]:
    """
    Create a subclass of the given pydantic model class with a new name.
    """
    new_model_name = "Create" + model_cls.__name__
    new_doc = (model_cls.__doc__ or "") + "\nRead-only fields have been removed."

    # Create a subclass that mixes in OmitReadOnlyMixin
    new_model_cls = type(
        new_model_name,
        (OmitReadOnlyMixin, model_cls),
        {"__module__": model_cls.__module__, "__doc__": new_doc},
    )

    return new_model_cls


class OmitReadOnlyMixin(pydantic.BaseModel):
    """
    Mixin for pydantic models that removes all fields marked as ReadOnly.
    """

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)

        # Collect readonly fields to delete first
        readonly_fields = []
        for name, field_info in cls.model_fields.items():
            if _is_readonly(field_info):
                readonly_fields.append(name)

        # Delete readonly fields after iteration is complete
        for name in readonly_fields:
            del cls.model_fields[name]

        cls.model_rebuild(force=True)


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


def create_model_with_optional_fields(
    model_cls: type[pydantic.BaseModel],
) -> type[pydantic.BaseModel]:
    """
    Create a subclass of the given pydantic model class with a new name.
    Read-only fields are removed and all writable fields are made optional with NOT_SET defaults.
    """
    new_model_name = "Update" + model_cls.__name__
    new_doc = (
        model_cls.__doc__
        or "" + "\nRead-only fields have been removed and all fields are optional."
    )

    # Create a subclass that mixes in both OmitReadOnlyMixin and PatchMixin
    new_model_cls = type(
        new_model_name,
        (PatchMixin, OmitReadOnlyMixin, model_cls),
        {"__module__": model_cls.__module__, "__doc__": new_doc},
    )

    return new_model_cls


class PatchMixin(pydantic.BaseModel):
    """
    A mixin for pydantic classes that makes all fields optional and replaces default
    with the NOT_SET marker.
    """

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)

        for field in cls.model_fields.values():
            field.default = NOT_SET
            field.annotation = Optional[field.annotation]

        cls.model_rebuild(force=True)


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


def set_schema_title(schema_cls: type[pydantic.BaseModel]) -> None:
    """Set the title of a schema class to its name.
    This is used to make the schema title match the model name in the OpenAPI schema.
    """
    schema_cls.model_config["title"] = schema_cls.__name__


def get_writable_inputs(
    schema_obj: BaseSchema, schema_cls: type[pydantic.BaseModel] | None = None
) -> dict[str, Any]:
    """
    Return a dictionary of field_name: value pairs for writable input fields.

    Filters out:
    - Fields with NOT_SET values
    - ReadOnly fields

    Args:
        schema_obj: The schema object to extract writable fields from
        schema_cls: The schema class to check for readonly fields. If None, uses schema_obj.__class__

    Returns:
        Dictionary mapping field names to their values for writable input fields only
    """
    if schema_cls is None:
        schema_cls = schema_obj.__class__

    updated_fields: dict[str, Any] = {}
    for field_name, value in schema_obj:
        if value is NOT_SET:
            continue
        # Skip readonly fields
        if is_readonly_field(schema_cls, field_name):
            continue
        updated_fields[field_name] = value

    return updated_fields


# def make_response_schema(
#     schema_cls: type[pydantic.BaseModel],
# ) -> type[pydantic.BaseModel]:
#     """
#     Create a response model from a schema that includes populate_by_name=True.
#     This allows GET responses to work with aliases while keeping POST/PUT operations
#     safe by requiring field names.

#     Args:
#         schema_cls: The original schema class

#     Returns:
#         A new schema class with populate_by_name=True enabled
#     """
#     config = getattr(schema_cls, "model_config", pydantic.ConfigDict())
#     if config.get("populate_by_name") and config.get("from_attributes"):
#         set_schema_title(schema_cls)
#         return schema_cls

#     new_config = config.copy()
#     new_config.update(populate_by_name=True, from_attributes=True)

#     # Build field definitions for the new schema
#     field_definitions = {}
#     for field_name, field_info in schema_cls.model_fields.items():
#         if hasattr(
#             field_info.annotation, "__origin__"
#         ) and field_info.annotation.__origin__ in (List, list):
#             if (
#                 hasattr(field_info.annotation, "__args__")
#                 and len(field_info.annotation.__args__) == 1
#             ):
#                 nested_schema = field_info.annotation.__args__[0]
#                 if isinstance(nested_schema, type) and issubclass(
#                     nested_schema, pydantic.BaseModel
#                 ):
#                     new_nested_schema = _create_response_schema_for_nested(
#                         nested_schema
#                     )
#                     field_definitions[field_name] = (List[new_nested_schema], ...)
#                 else:
#                     field_definitions[field_name] = (field_info.annotation, ...)
#             else:
#                 field_definitions[field_name] = (field_info.annotation, ...)
#         elif isinstance(field_info.annotation, type) and issubclass(
#             field_info.annotation, pydantic.BaseModel
#         ):
#             new_nested_schema = _create_response_schema_for_nested(
#                 field_info.annotation
#             )
#             field_definitions[field_name] = (new_nested_schema, ...)
#         else:
#             field_definitions[field_name] = (field_info.annotation, ...)

#     # Create the new schema from scratch
#     new_schema = pydantic.create_model(
#         f"Response{schema_cls.__name__}",
#         **field_definitions,
#         __module__=schema_cls.__module__,
#     )

#     new_schema.model_config = new_config
#     set_schema_title(new_schema)

#     return new_schema


# def _create_response_schema_for_nested(
#     schema_cls: type[pydantic.BaseModel],
# ) -> type[pydantic.BaseModel]:
#     """
#     Create a response schema for a nested schema with populate_by_name=True and from_attributes=True.
#     """
#     config = getattr(schema_cls, "model_config", pydantic.ConfigDict())
#     if config.get("populate_by_name") and config.get("from_attributes"):
#         return schema_cls

#     new_config = config.copy()
#     new_config.update(populate_by_name=True, from_attributes=True)

#     new_schema = type(
#         f"Response{schema_cls.__name__}",
#         (schema_cls,),
#         {"model_config": new_config, "__module__": schema_cls.__module__},
#     )

#     set_schema_title(new_schema)
#     return new_schema
